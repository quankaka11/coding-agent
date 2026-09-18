"""Gọi Claude Code headless và ghi lại từng lượt vào log.

Đây là ranh giới giữa lớp phán đoán (agent) và lớp phán quyết (script):
mọi thứ agent làm đều chảy qua đây để còn xem lại được.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..core.run import RunContext

DEFAULT_TOOLS = "Read,Write,Edit,Glob,Grep,Bash"
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


@dataclass
class AgentResult:
    ok: bool
    text: str = ""
    cost_usd: float = 0.0
    turns: int = 0
    tools: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    error: str = ""


class ClaudeCode:
    """Runner thật. Test dùng StubAgent cùng giao diện .run()."""

    def __init__(self, binary: str = "claude", model: str | None = None,
                 permission_mode: str = "auto",
                 allowed_tools: str = DEFAULT_TOOLS, timeout_sec: int = 1800,
                 effort: str | None = None) -> None:
        self.binary, self.model, self.effort = binary, model, effort
        self.permission_mode, self.allowed_tools = permission_mode, allowed_tools
        self.timeout_sec = timeout_sec

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def run(self, ctx: RunContext, prompt: str, cwd: Path, name: str) -> AgentResult:
        if not self.available():
            return AgentResult(False, error=f"không thấy lệnh {self.binary!r} trong PATH")

        (ctx.run_dir / f"prompt-{name}.md").write_text(prompt, encoding="utf-8")
        argv = [self.binary, "-p", prompt, "--output-format", "stream-json", "--verbose",
                "--permission-mode", self.permission_mode, "--allowed-tools", self.allowed_tools]
        if self.model:
            argv += ["--model", self.model]
        if self.effort:
            argv += ["--effort", self.effort]

        ctx.emit("agent.start", agent=name, cwd=str(cwd), tools=self.allowed_tools,
                 permission_mode=self.permission_mode, model=self.model, effort=self.effort)
        res = ctx.cmd(argv, cwd=cwd, timeout=self.timeout_sec)
        (ctx.run_dir / f"agent-{name}.jsonl").write_text(res.stdout, encoding="utf-8")
        out = _parse_stream(ctx, res.stdout, name)
        out.ok = res.ok and not out.error
        if not res.ok and not out.error:
            out.error = f"claude thoát với mã {res.exit_code}"
        ctx.cost_usd += out.cost_usd
        ctx.emit("agent.end", agent=name, ok=out.ok, turns=out.turns,
                 cost_usd=round(out.cost_usd, 4), files=out.files_touched[:10],
                 level="info" if out.ok else "warn", error=out.error or None)
        ctx.check_budget()
        return out


def _parse_stream(ctx: RunContext, stdout: str, name: str) -> AgentResult:
    out = AgentResult(ok=False)
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    tool = block.get("name", "?")
                    target = (block.get("input") or {}).get("file_path") or ""
                    out.turns += 1
                    out.tools.append(tool)
                    # chỉ tool GHI mới tính là "đụng file"; Read/Glob/Grep là đọc
                    if target and tool in WRITE_TOOLS and target not in out.files_touched:
                        out.files_touched.append(target)
                    ctx.emit("agent.turn", level="debug", agent=name, tool=tool, target=target)
        elif kind == "result":
            out.text = event.get("result") or ""
            out.cost_usd = float(event.get("total_cost_usd") or 0.0)
            if event.get("is_error"):
                out.error = out.text[:300] or "agent báo lỗi"
    return out


class StubAgent:
    """Agent giả cho selftest: chạy một hàm Python thay vì gọi LLM."""

    def __init__(self, script: dict) -> None:
        self.script = script          # {tên bước: callable(cwd) -> str}
        self.calls: list[str] = []
        self.prompts: dict[str, str] = {}   # để test soi được cái gì đã vào prompt

    def available(self) -> bool:
        return True

    def run(self, ctx: RunContext, prompt: str, cwd: Path, name: str) -> AgentResult:
        self.calls.append(name)
        self.prompts[name] = prompt
        ctx.emit("agent.start", agent=name, cwd=str(cwd), stub=True)
        # "implement-ag1-2" → "implement-ag1" → "implement": kịch bản theo tên gốc,
        # hậu tố lượt/vòng khắc phục không cần khai riêng.
        handler, key = None, name
        while handler is None and key:
            handler = self.script.get(key)
            key = key.rsplit("-", 1)[0] if "-" in key else ""
        if handler is None:
            return AgentResult(False, error=f"stub không có kịch bản cho {name!r}")
        text = handler(cwd) or ""
        ctx.emit("agent.end", agent=name, ok=True, turns=1, stub=True)
        return AgentResult(True, text=text, turns=1)
