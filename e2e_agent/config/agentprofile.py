"""Để chính agent soi một repo KHÔNG phải Python và đề xuất lệnh setup/build/test/lint.

Viết tay luật cho từng hệ sinh thái (Node có npm/pnpm/yarn × jest/vitest/mocha, Go,
Java maven/gradle, PHP, Ruby, Rust…) là một ma trận không bao giờ đủ. Agent đọc được
manifest và cấu hình CI của repo, và chạy thử được lệnh — nên nó đề xuất. Phần tất
định vẫn nằm ở đây: kiểm đầu ra đúng schema, rồi TỰ chạy lại setup + test + lint để
xác nhận, không tin lời agent nói "verified".
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

import yaml

from ..core import parsers
from ..core.run import Decided, RunContext
from ..core.secrets import command_env

SKILL = Path(__file__).resolve().parents[1] / "skills" / "profile.md"
KEYS = ("language", "setup", "build", "test", "lint", "allowed_paths", "test_globs",
        "extra_forbidden", "verified", "notes")


def suggest(repo: Path, agent, run_dir: Path) -> tuple[dict | None, str]:
    """Hỏi agent. Trả về (đề xuất đã kiểm schema, hoặc None; lý do khi None)."""
    ctx = RunContext(run_dir=run_dir, task_id="init", phase="init", timeout_min=30,
                     cost_cap_usd=5.0, console=False)
    try:
        result = agent.run(ctx, SKILL.read_text(encoding="utf-8"), repo, "profile")
    except Decided as exc:            # vượt trần thời gian/chi phí
        return None, f"agent dừng giữa chừng: {exc.why}"
    finally:
        _restore_tracked(repo, ctx)
        ctx.log.close()
    if not result.ok:
        return None, f"agent lỗi: {result.error[:200]}"
    try:
        return parse(result.text), ""
    except ValueError as exc:
        return None, f"đề xuất của agent sai định dạng: {exc}"


def parse(text: str) -> dict:
    from ..core.parsers import fenced_block
    body = fenced_block(text)
    try:
        raw = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML hỏng: {exc}") from None
    if not isinstance(raw, dict):
        raise ValueError("cần một mapping YAML")
    out = {k: raw.get(k) for k in KEYS}
    errs = []
    if not isinstance(out["test"], str) or "{junit}" not in out["test"]:
        errs.append("test phải là chuỗi có {junit}")
    if not isinstance(out["lint"], str) or not out["lint"].strip():
        errs.append("thiếu lint")
    for key in ("setup", "build"):
        if out[key] is not None and not isinstance(out[key], str):
            errs.append(f"{key} phải là chuỗi hoặc null")
    for key in ("allowed_paths", "test_globs", "extra_forbidden", "notes"):
        out[key] = out[key] or []
        if not isinstance(out[key], list) or not all(isinstance(x, str) for x in out[key]):
            errs.append(f"{key} phải là danh sách chuỗi")
    if not out["allowed_paths"]:
        errs.append("allowed_paths rỗng")
    if errs:
        raise ValueError("; ".join(errs))
    return out


def verify(repo: Path, commands: dict[str, str], timeout: int = 900) -> tuple[list[str], list[str]]:
    """Chạy lại setup → build → test → lint trên repo. Trả về (ghi chú, TODO)."""
    notes: list[str] = []
    todos: list[str] = []
    for name in ("setup", "build"):
        if cmd := commands.get(name):
            proc = _sh(cmd, repo, timeout)
            if proc.returncode != 0:
                todos.append(f"commands.{name} thất bại (exit {proc.returncode}): {_tail(proc)} — sửa lệnh trong hồ sơ")
                return notes, todos
            notes.append(f"{name}: `{cmd}` chạy được")
    with tempfile.TemporaryDirectory(prefix="e2ea-probe-") as tmp:
        junit = Path(tmp) / "junit.xml"
        proc = _sh(commands["test"].format(junit=str(junit)), repo, timeout)
        report = parsers.junit(junit)
        if proc.returncode != 0 and report.total == 0:
            todos.append(f"commands.test không chạy được (exit {proc.returncode}, không đọc được test nào "
                         f"từ JUnit): {_tail(proc)}")
        elif not junit.exists():
            todos.append("commands.test chạy xong nhưng không ghi JUnit XML vào {junit} — gate sẽ "
                         "không phân loại được test fail")
        else:
            notes.append(f"test: {report.total} test, {len(report.failed)} fail sẵn trên code hiện tại")
    proc = _sh(commands["lint"], repo, timeout)
    if proc.returncode != 0:
        todos.append(f"commands.lint đang đỏ trên code hiện tại (exit {proc.returncode}): {_tail(proc)} — "
                     "chọn lệnh lint xanh, không thì mọi run phải sửa lint trước")
    else:
        notes.append("lint: xanh trên code hiện tại")
    return notes, todos


def _sh(cmd: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL, env=command_env())
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"timeout {timeout}s")


def _tail(proc: subprocess.CompletedProcess, n: int = 3) -> str:
    lines = [l for l in ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines() if l.strip()]
    return " | ".join(lines[-n:])[:300]


def _restore_tracked(repo: Path, ctx: RunContext) -> None:
    """Agent được dặn không sửa file đang theo dõi; lỡ sửa thì trả lại nguyên trạng."""
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        subprocess.run(["git", "checkout", "--", "."], cwd=repo, capture_output=True, check=False)
        ctx.emit("profile.restored", level="warn", files=dirty.splitlines()[:10],
                 note="agent đã sửa file đang theo dõi khi soi repo — đã hoàn nguyên")


def run_dir_for(runs_root: Path) -> Path:
    return runs_root / "_init" / time.strftime("%Y%m%d-%H%M%S")
