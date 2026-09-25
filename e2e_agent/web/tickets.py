"""Ticket và hai quyết định của người: duyệt plan, và gán nhãn kết quả MR.

Giao diện KHÔNG chạy pipeline. Duyệt plan chỉ là đổi trạng thái trên tracker —
đúng thứ vòng quét sau chờ để chạy Phase B ngay trên ticket đó. Hai bàn phím, một máy
trạng thái, và tracker vẫn là nơi giữ sự thật.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..tracker import base as L
from . import workspace as ws_mod

#: Hai trạng thái là việc của người. Mọi cái khác agent tự lo.
WAITING_ON_HUMAN = (L.PLAN_READY, L.NEEDS_HUMAN)


def _tracker(root: Path):
    from ..config import profile as profile_mod
    from .. import tracker as tracker_mod
    ws = ws_mod.load(root)
    if not ws.layout or not ws.layout["profile"].is_file():
        raise RuntimeError("chưa có hồ sơ repo")
    prof = profile_mod.load(str(ws.layout["profile"]))
    return tracker_mod.make(prof, ws.layout["profile"].parent), ws


#: Giao diện poll 15 giây một lần, mà mỗi lần queue là 9 request sang tracker.
#: Không có lớp đệm này thì Backlog ăn ~36 request/phút chỉ để vẽ lại cùng một
#: danh sách, và người dùng đợi cả 9 round-trip mới thấy màn hình nhúc nhích.
_TTL_SEC = 10.0
_CACHED: dict[str, tuple[float, dict]] = {}


#: Trạng thái ticket mới nhất mà giao diện biết: {thư mục: {ticket: (nhãn, epoch)}}. Nạp từ
#: mỗi lần đọc hàng đợi, và NGAY khi người bấm duyệt/từ chối — để danh sách run không
#: còn ghi "plan chờ duyệt" trong lúc chờ vòng quét sau chạy Phase B.
_STATES: dict[str, dict[str, tuple[str, float]]] = {}


def _remember(root: Path, ticket_id: str, label: str, at: float | None = None) -> None:
    _STATES.setdefault(str(root), {})[ticket_id] = (label, at or time.time())


def current_states(root: Path) -> dict[str, tuple[str, float]]:
    """Không gọi mạng: chỉ trả những gì đã biết. Rỗng thì danh sách run hiện như cũ."""
    return dict(_STATES.get(str(root), {}))


def queue(root: Path, max_age: float = _TTL_SEC) -> dict:
    """Danh sách theo từng trạng thái. Mở mỗi màn hình là biết có việc hay không.

    `fetched_at` đi kèm để màn hình nói được số này lấy lúc nào — số đếm không
    có mốc thời gian là số đếm không kiểm chứng được.
    """
    key = str(root)
    hit = _CACHED.get(key)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]

    tracker, _ = _tracker(root)
    out: dict[str, list[dict]] = {}
    labels = list(L.STATE_LABELS)

    def fetch(label: str):
        return label, [{"id": t.id, "title": t.title, "url": t.url, "parent": t.parent}
                       for t in tracker.list_by_label(label)]

    # Chín nhãn độc lập nhau: gọi song song thì tổng thời gian là request chậm
    # nhất chứ không phải tổng của chín cái.
    with ThreadPoolExecutor(max_workers=len(labels)) as pool:
        try:
            for label, rows in pool.map(fetch, labels):
                out[label] = rows
        except Exception as exc:
            return {"error": " ".join(str(exc).split())[:300], "by_label": out,
                    "waiting": 0, "fetched_at": time.time()}

    waiting = sum(len(out.get(label, [])) for label in WAITING_ON_HUMAN)
    result = {"by_label": out, "waiting": waiting, "error": None, "fetched_at": time.time()}
    for label, rows in out.items():
        for row in rows:
            _remember(root, row["id"], label, result["fetched_at"])
    _CACHED[key] = (time.time(), result)
    return result


def invalidate(root: Path) -> None:
    """Sau khi người vừa duyệt/từ chối, đừng bắt họ nhìn danh sách cũ thêm 10 giây."""
    _CACHED.pop(str(root), None)


def plan(root: Path, ticket_id: str) -> dict:
    """Mọi thứ người cần để gật hay lắc: giả định, phần cố ý bỏ, phạm vi, plan."""
    from ..pipeline.orchestrator import MAX_REJECTS, REJECT_MARK
    tracker, ws = _tracker(root)
    ticket = tracker.get(ticket_id)
    comments = tracker.comments(ticket_id)
    rejected = sum(1 for c in comments if REJECT_MARK in c)

    run_dir = _latest_run(ws.runs_root, ticket_id)
    spec, markdown = {}, ""
    if run_dir:
        markdown = _read(run_dir / "plan.md")
        raw = _read(run_dir / "spec.yaml")
        if raw:
            import yaml
            try:
                spec = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                spec = {}
    return {
        "id": ticket.id, "title": ticket.title, "url": ticket.url,
        "labels": ticket.labels, "parent": ticket.parent,
        "can_decide": L.PLAN_READY in ticket.labels,
        "rejected": rejected, "max_rejects": MAX_REJECTS,
        "run_dir": str(run_dir) if run_dir else None,
        "base_sha": (ticket.base_sha or "")[:12] or None,
        "task_type": spec.get("task_type"),
        "modules": (spec.get("scope") or {}).get("modules") or [],
        "assumptions": spec.get("assumptions") or [],
        "out_of_scope": spec.get("out_of_scope") or [],
        "acceptance_criteria": spec.get("acceptance_criteria") or [],
        "questions": spec.get("questions") or [],
        "plan_md": markdown,
    }


def approve(root: Path, ticket_id: str) -> dict:
    """Đổi category sang plan-approved. Vòng quét sau chạy Phase B ngay trên ticket này."""
    tracker, _ = _tracker(root)
    ticket = tracker.get(ticket_id)
    if L.PLAN_READY not in ticket.labels:
        return {"ok": False, "detail": f"{ticket_id} không ở trạng thái chờ duyệt nữa — "
                                       f"hiện là {', '.join(ticket.labels) or 'không có'}"}
    tracker.set_state(ticket_id, L.PLAN_APPROVED)
    _remember(root, ticket_id, L.PLAN_APPROVED)
    invalidate(root)
    return {"ok": True, "detail": "Đã duyệt. Vòng quét tới agent bắt đầu viết code trên chính ticket này."}


def reject(root: Path, ticket_id: str, why: str) -> dict:
    """Từ chối kèm lý do.

    Comment TRƯỚC, đổi category SAU. `Orchestrator.reject()` đọc lý do bằng
    `_last_human_comment()`; đổi nhãn trước thì vòng quét có thể chen vào giữa
    và ghi "người không ghi lý do" — mất luôn cái duy nhất agent cần để lập lại.
    """
    why = (why or "").strip()
    if not why:
        return {"ok": False, "detail": "Phải viết lý do: plan sau lập lại dựa trên chính câu này."}
    tracker, _ = _tracker(root)
    ticket = tracker.get(ticket_id)
    if L.PLAN_READY not in ticket.labels:
        return {"ok": False, "detail": f"{ticket_id} không ở trạng thái chờ duyệt nữa"}
    tracker.comment(ticket_id, why)
    tracker.set_state(ticket_id, L.PLAN_REJECTED)
    _remember(root, ticket_id, L.PLAN_REJECTED)
    invalidate(root)
    return {"ok": True, "detail": "Đã từ chối. Agent sẽ lập plan mới có tính tới lý do này."}


def review(root: Path, task_id: str, run_id: str, outcome: str, test_value: str,
           note: str, reviewer: str = "") -> dict:
    """Nhãn reviewer cho một MR đã review — nguồn duy nhất của chỉ số false-green."""
    from ..metrics import Review, save_review
    ws = ws_mod.load(root)
    try:
        run_dir = ws.runs_root / ws_mod.safe_segment(task_id) / ws_mod.safe_segment(run_id)
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    if not run_dir.is_dir():
        return {"ok": False, "detail": f"không thấy run {task_id}/{run_id}"}
    try:
        save_review(run_dir, Review(outcome=outcome, test_value=test_value,
                                    note=note, reviewer=reviewer))
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "detail": "Đã gán nhãn."}


def review_options() -> dict:
    from ..metrics import OUTCOMES, TEST_VALUES
    return {"outcomes": OUTCOMES, "test_values": TEST_VALUES}


def _latest_run(runs_root: Path, task_id: str) -> Path | None:
    runs = [p.parent for p in (runs_root / task_id).glob("*/events.jsonl")]
    return max(runs, key=lambda p: p.stat().st_mtime) if runs else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
