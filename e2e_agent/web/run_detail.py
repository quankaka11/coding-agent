"""Biên bản một run: từng bước, gate, mười luật anti-gaming, bằng chứng.

Dựng lại từ `events.jsonl` — cùng file mà `e2ea report` đọc. Mười ô luật luôn
hiện đủ, kể cả luật không chạy: điểm của cả hệ thống là không có đường thoát im
lặng, lọc bớt ô là phá đúng chỗ đó.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import progress
from . import workspace as ws_mod

ALL_RULES = [f"G-{i}" for i in range(1, 11)]

#: File nào đáng mở ra đọc, và gọi nó là gì trên màn hình.
ARTIFACTS = {
    "plan.md": "Plan",
    "spec.yaml": "Spec ticket",
    "discovery.md": "Khảo sát",
    "mr-body.md": "Mô tả MR",
    "report.md": "Báo cáo",
    "gate-report.json": "Gate",
    "antigaming-report.json": "Anti-gaming",
    "evidence.json": "Bằng chứng",
    "outcome.json": "Kết cục",
    "baseline.json": "Baseline",
}


def detail(root: Path, task_id: str, run_id: str) -> dict:
    try:
        run_dir = _dir(root, task_id, run_id)
    except ValueError:
        return {"found": False}
    if not run_dir.is_dir():
        return {"found": False}
    events = _events(run_dir)
    track = progress.track(run_dir / "events.jsonl", events, time.time())
    outcome = _json(run_dir / "outcome.json")
    later = ws_mod.next_runs(run_dir.parent.parent, task_id).get(run_id)
    from .tickets import current_states
    past = ws_mod.history({"label": (outcome or {}).get("label"), "why": (outcome or {}).get("why"),
                           "live": track["live"], "stale": track["stale"],
                           "updated_at": track["updated_at"]},
                          later, current_states(root).get(task_id))
    return {
        **past,
        "found": True,
        "phase": track["phase"],
        "phase_word": progress.phase_word(track["phase"]),
        "step": track["step"],
        "step_word": track["step_word"],
        "step_index": track["step_index"],
        "steps_total": track["steps_total"],
        "updated_at": track["updated_at"],
        "idle_sec": track["idle_sec"],
        "stale": track["stale"],
        "task_id": task_id,
        "run_id": run_id,
        "outcome": outcome,
        "evidence": _json(run_dir / "evidence.json"),
        "gate": _json(run_dir / "gate-report.json"),
        "steps": _steps(events),
        "summary": _summary(events, _json(run_dir / "evidence.json")),
        "rules": _rules(events),
        "mr": _mr(events),
        "live": track["live"],
        "cost_usd": track["cost_usd"],
        "started_at": events[0]["ts"] if events else None,
        "artifacts": _artifacts(run_dir),
        "event_count": len(events),
    }


def _steps(events: list[dict]) -> list[dict]:
    """Các bước theo đúng thứ tự chạy, kèm việc đã làm trong từng bước.

    Số vòng tự sửa không suy ra từ đâu khác được: nó chính là số lần một bước
    xuất hiện lại. Đó là thứ đáng nhìn nhất của một run có vấn đề.
    """
    steps: list[dict] = []
    for event in events:
        name = event.get("step") or "-"
        kind = event.get("event")
        if kind == "step.start":
            steps.append({"step": name, "started_at": event.get("ts"), "duration_ms": None,
                          "round": sum(1 for s in steps if s["step"] == name) + 1, "notes": []})
            continue
        if kind == "step.end" and steps:
            for step in reversed(steps):
                if step["step"] == name and step["duration_ms"] is None:
                    step["duration_ms"] = event.get("duration_ms")
                    break
            continue
        if not steps or kind in ("cmd.exec", "agent.turn"):
            continue
        note = _note(event)
        if note:
            steps[-1]["notes"].append(note)
    return steps


def _summary(events: list[dict], evidence: dict | None) -> list[dict]:
    """Chuyện đã xảy ra, bằng câu người đọc — mỗi bước một dòng.

    Người mở màn hình này để biết run đã làm gì và kết luận ra sao, không phải
    để đọc lại tên sự kiện. Chi tiết máy vẫn còn nguyên trong `steps`.
    """
    facts: dict[str, dict] = {}
    for event in events:
        data, kind = event.get("data") or {}, event.get("event")
        if kind == "baseline.ready":
            facts["baseline"] = {"detail": f"{data.get('total', 0)} test xanh trên code chưa sửa",
                                 "tone": "ok"}
        elif kind == "baseline.lint_red":
            facts["lint_fix"] = {"detail": "lint đỏ sẵn — agent sửa bằng một commit riêng",
                                 "tone": "warn"}
        elif kind == "testfirst.result":
            # `total` là cả suite; con số người cần là số test MỚI đỏ trên code cũ.
            new = len(((evidence or {}).get("fail_before_pass_after") or {}).get("tests") or [])
            facts["test_first"] = {"tone": "ok",
                                   "detail": f"{new} test mới, đỏ trên code chưa sửa" if new
                                             else f"{data.get('total', 0)} test chạy được"}
        elif kind == "testgen.retry":
            facts["test_first"] = {"detail": "test đầu chưa dùng được, agent viết lại", "tone": "warn"}
        elif kind == "implement.done":
            rounds = data.get("rounds") or 1
            facts["implement"] = {"detail": "một lượt là xong" if rounds == 1
                                  else f"{rounds} lượt mới qua", "tone": "ok" if rounds == 1 else "warn"}
        elif kind == "gate.verdict":
            ok = data.get("verdict") == "PASS"
            facts["gate"] = {"detail": "mọi kiểm tra đều đạt" if ok else "có kiểm tra không đạt",
                             "tone": "ok" if ok else "bad"}
        elif kind == "antigaming.verdict":
            ok = data.get("verdict") == "PASS"
            facts["antigaming"] = {"detail": "không có dấu hiệu gian lận" if ok
                                   else "có luật không qua", "tone": "ok" if ok else "bad"}
        elif kind == "mr.created":
            facts["create_mr"] = {"detail": data.get("branch") or "", "tone": "ok"}
        elif kind == "planning.ready":
            facts["planning"] = {"detail": "plan đã gửi lên ticket chờ người duyệt", "tone": "ok"}

    out, seen = [], set()
    for step in _order(events):
        if step in seen:
            continue
        seen.add(step)
        fact = facts.get(step, {})
        out.append({"step": step, "title": progress.step_word(step) or step,
                    "detail": fact.get("detail", ""), "tone": fact.get("tone", "quiet"),
                    "rounds": sum(1 for e in events
                                  if e.get("event") == "step.start" and e.get("step") == step)})
    return out


def _order(events: list[dict]) -> list[str]:
    return [e.get("step") for e in events
            if e.get("event") == "step.start" and e.get("step") not in (None, "-")]


def _note(event: dict) -> dict | None:
    """Một dòng người đọc được cho những sự kiện đáng kể. Phần còn lại là tiếng ồn."""
    data = event.get("data") or {}
    kind = event.get("event")
    if kind == "gate.check":
        return {"kind": "gate", "text": f"{data.get('check')}: {data.get('status')}",
                "status": data.get("status"), "why": data.get("reason") or ""}
    if kind == "agent.end":
        return {"kind": "agent", "text": f"agent {data.get('agent')} xong",
                "status": "info", "why": f"${data.get('cost_usd') or 0}"}
    if kind == "mr.created":
        # URL của MR đã là tiêu đề của cả màn hình; ở đây chỉ cần nhánh.
        return {"kind": kind, "text": "mr.created", "status": "info",
                "why": data.get("branch") or ""}
    if kind in ("testfirst.frozen", "testfirst.result", "baseline.ready", "implement.done",
                "gate.verdict", "antigaming.verdict", "planning.ready", "intake.spec",
                "plan.handed", "testgen.precheck", "sandbox.ready"):
        return {"kind": kind, "text": kind, "status": event.get("level", "info"),
                "why": _short(data)}
    return None      # `decision` không vào đây: nó chính là kết luận in to ở đầu trang


def _rules(events: list[dict]) -> list[dict]:
    """Mười ô, luôn đủ. Luật không chạy hiện `skip`, không biến mất."""
    from ..enforce.antigaming import _HOWTO
    seen: dict[str, dict] = {}
    for event in events:
        if event.get("event") != "antigaming.rule":
            continue
        data = event.get("data") or {}
        rule = data.get("rule")
        if rule:
            seen[rule] = {"rule": rule, "status": data.get("status"), "why": data.get("why") or "",
                          "remedy": data.get("remedy"), "howto": _HOWTO.get(rule, "")}
    return [seen.get(rule, {"rule": rule, "status": "skip", "why": "", "remedy": None,
                            "howto": _HOWTO.get(rule, "")}) for rule in ALL_RULES]


def _mr(events: list[dict]) -> dict | None:
    for event in reversed(events):
        if event.get("event") == "mr.created":
            data = event.get("data") or {}
            return {"url": data.get("url"), "iid": data.get("iid"), "branch": data.get("branch")}
    return None


def _artifacts(run_dir: Path) -> list[dict]:
    out = []
    for path in sorted(run_dir.iterdir()):
        if not path.is_file():
            continue
        label = ARTIFACTS.get(path.name)
        if label is None:
            if path.name.startswith("prompt-"):
                label = f"Prompt {path.stem.removeprefix('prompt-')}"
            elif path.name.startswith("agent-") and path.suffix == ".jsonl":
                label = f"Nhật ký agent {path.stem.removeprefix('agent-')}"
            else:
                continue
        out.append({"name": path.name, "label": label, "bytes": path.stat().st_size})
    return out


def artifact(root: Path, task_id: str, run_id: str, name: str) -> str | None:
    """Đọc một file trong run. Tên phải nằm gọn trong thư mục run, không đi ra ngoài."""
    try:
        run_dir = _dir(root, task_id, run_id)
        ws_mod.safe_segment(name)
    except ValueError:
        return None
    target = (run_dir / name).resolve()
    if not str(target).startswith(str(run_dir.resolve()) + "/") or not target.is_file():
        return None
    if target.stat().st_size > 2_000_000:
        return target.read_text(encoding="utf-8", errors="replace")[:2_000_000] + "\n… (cắt bớt)"
    return target.read_text(encoding="utf-8", errors="replace")


def stream(root: Path, task_id: str, run_id: str, from_line: int = 0):
    """SSE: đẩy từng sự kiện mới của run đang chạy. Dừng khi có `run.end`."""
    path = _dir(root, task_id, run_id) / "events.jsonl"
    sent, idle = from_line, 0.0
    yield ": mo dong\n\n"
    while idle < 900:
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        if len(lines) > sent:
            idle = 0.0
            for line in lines[sent:]:
                if line.strip():
                    yield f"data: {line.strip()}\n\n"
            sent = len(lines)
            if any('"run.end"' in ln for ln in lines[-3:]):
                yield "event: end\ndata: {}\n\n"
                return
        else:
            yield ": cho\n\n"          # giữ kết nối sống qua proxy
            time.sleep(1.0)
            idle += 1.0


def _dir(root: Path, task_id: str, run_id: str) -> Path:
    """Thư mục run. Ném ValueError nếu mã nào đó định đi ra ngoài `runs_root`."""
    ws = ws_mod.load(root)
    return ws.runs_root / ws_mod.safe_segment(task_id) / ws_mod.safe_segment(run_id)


def _events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _short(data: dict, limit: int = 160) -> str:
    if not data:
        return ""
    parts = [f"{k}={v}" for k, v in data.items()
             if isinstance(v, (str, int, float, bool)) and str(v)]
    text = ", ".join(parts)
    return text if len(text) <= limit else text[:limit] + "…"
