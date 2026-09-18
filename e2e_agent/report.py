"""Dựng báo cáo người đọc được từ events.jsonl (build-plan mục 4.5).

Đây là thứ người review bên ngoài nhìn thấy, nên nó phải tự giải thích được.
"""
from __future__ import annotations

import json
from pathlib import Path

from .core.reasons import EXPLAIN, EXPLAIN_KIND, Kind, Reason

_ICON = {"pass": "✅", "fail": "❌", "needs_review": "⚠️", "out_of_scope": "⊘",
         "PASS": "✅", "FAIL": "❌"}


def read_events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def render(run_dir: Path) -> str:
    events = read_events(run_dir)
    if not events:
        return f"# Run report\n\nKhông có sự kiện nào trong {run_dir}.\n"

    first = events[0]
    decision = next((e for e in reversed(events) if e["event"] == "decision"), None)
    end = next((e for e in reversed(events) if e["event"] == "run.end"), None)
    d = (decision or {}).get("data", {})

    out = [f"# Run {first.get('run_id', '?')} — ticket {first.get('task_id', '?')}", ""]
    kind = f" / `{d['kind']}`" if d.get("kind") else ""
    out += [f"**Kết cục:** `{d.get('reason', '?')}`{kind} → label `{d.get('label', '?')}`", ""]
    if d.get("meaning"):
        out += [f"> {d['meaning']}", ""]
    if d.get("why"):
        out += [f"**Cụ thể:** {d['why']}", ""]
    if end:
        secs = round((end.get("duration_ms") or 0) / 1000, 1)
        out += [f"**Thời gian:** {secs}s · **Chi phí:** {end['data'].get('cost_usd', 0)} USD", ""]

    out += ["## Các bước đã chạy", "", "| Bước | Thời gian |", "|---|---|"]
    for e in events:
        if e["event"] == "step.end":
            out.append(f"| {e.get('step', '?')} | {round((e.get('duration_ms') or 0) / 1000, 1)}s |")
    out.append("")

    checks = [e for e in events if e["event"] == "gate.check"]
    if checks:
        out += ["## Gate", "", "| Check | Kết quả | Ghi chú |", "|---|---|---|"]
        for e in checks:
            data = e.get("data", {})
            note = data.get("reason") or (f"exit {data['exit_code']}" if "exit_code" in data else "")
            if data.get("new_failures"):
                note = f"test mới fail: {', '.join(data['new_failures'][:3])}"
            out.append(f"| {data.get('check')} | {_ICON.get(data.get('status'), '')} {data.get('status')} | {note} |")
        out.append("")

    rules = [e for e in events if e["event"] == "antigaming.rule"]
    if rules:
        out += ["## Anti-gaming", "", "| Luật | Kết quả | Bằng chứng |", "|---|---|---|"]
        for e in rules:
            data = e.get("data", {})
            ev = {k: v for k, v in (data.get("evidence") or {}).items() if v}
            detail = data.get("why", "")
            if ev:
                detail += " — " + json.dumps(ev, ensure_ascii=False)[:160]
            out.append(f"| {data.get('rule')} | {_ICON.get(data.get('status'), '')} {data.get('status')} | {detail} |")
        out.append("")

    cmds = [e for e in events if e["event"] == "cmd.exec"]
    if cmds:
        out += ["## Lệnh đã chạy", "", "| Lệnh | Exit | Thời gian |", "|---|---|---|"]
        for e in cmds:
            data = e.get("data", {})
            argv = str(data.get("argv", ""))[:90].replace("|", "\\|")
            out.append(f"| `{argv}` | {data.get('exit_code')} | {round((data.get('duration_ms') or 0) / 1000, 1)}s |")
        out.append("")

    warns = [e for e in events if e["level"] in ("warn", "error") and e["event"] != "decision"]
    if warns:
        out += ["## Cảnh báo", ""]
        for e in warns:
            out.append(f"- `{e['event']}` — {json.dumps(e.get('data', {}), ensure_ascii=False)[:200]}")
        out.append("")

    out += ["---", "", "*Báo cáo sinh từ `events.jsonl`. Mọi kết cục đều có mã lý do; "
            "danh sách mã và ý nghĩa nằm trong `e2e_agent/core/reasons.py`.*"]
    return "\n".join(out) + "\n"


def explain_all() -> str:
    rows = ["| Kết cục | Nghĩa |", "|---|---|"]
    rows += [f"| `{r.value}` | {EXPLAIN[r]} |" for r in Reason]
    rows += ["", "| Kind (chi tiết) | Nghĩa |", "|---|---|"]
    rows += [f"| `{k.value}` | {EXPLAIN_KIND[k]} |" for k in Kind]
    return "\n".join(rows)
