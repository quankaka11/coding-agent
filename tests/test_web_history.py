"""Danh sách run: Phase A và Phase B cùng một ticket — dòng cũ không được nói "plan chờ duyệt"."""
import json
import os

from e2e_agent.web import workspace as ws


def _run(root, task, run_id, phase, ts, label, why="", mtime=None):
    d = root / task / run_id
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(
        json.dumps({"ts": ts, "event": "run.start", "run_id": run_id, "phase": phase}) + "\n"
        + json.dumps({"ts": ts, "event": "run.end", "run_id": run_id, "phase": phase,
                      "data": {"reason": "OK"}}) + "\n")
    (d / "outcome.json").write_text(json.dumps({"run_id": run_id, "task_id": task, "reason": "OK",
                                                "label": label, "why": why, "duration_ms": 1}))
    if mtime:
        os.utime(d / "events.jsonl", (mtime, mtime))


def _rows(root, current=None):
    return {r["run_id"]: r for r in ws.runs_index(root, current=current)}


def test_plan_ready_run_followed_by_phase_b_reads_as_approved(tmp_path):
    _run(tmp_path, "T-28", "r-a", "A", "2026-09-24T04:15:00Z", "agent:plan-ready", mtime=1000)
    _run(tmp_path, "T-28", "r-b", "B", "2026-09-24T04:20:00Z", "agent:mr-created", mtime=2000)
    rows = _rows(tmp_path)
    assert rows["r-a"]["superseded_by"] == "r-b" and rows["r-a"]["history_word"] == "plan đã duyệt"
    assert "r-b" in rows["r-a"]["history_why"]
    assert rows["r-b"]["superseded_by"] is None and rows["r-b"]["history_word"] is None


def test_plan_replaced_by_new_plan_reads_as_old(tmp_path):
    _run(tmp_path, "T-1", "r-1", "A", "2026-09-24T01:00:00Z", "agent:plan-ready", mtime=1000)
    _run(tmp_path, "T-1", "r-2", "A", "2026-09-24T02:00:00Z", "agent:plan-ready", mtime=2000)
    rows = _rows(tmp_path)
    assert rows["r-1"]["history_word"] == "plan cũ"
    assert rows["r-2"]["history_word"] is None          # plan mới nhất vẫn đang chờ duyệt


def test_just_approved_plan_before_next_scan(tmp_path):
    _run(tmp_path, "T-2", "r-1", "A", "2026-09-24T01:00:00Z", "agent:plan-ready", mtime=1000)
    rows = _rows(tmp_path, current={"T-2": ("agent:plan-approved", 1500.0)})
    assert rows["r-1"]["history_word"] == "plan đã duyệt"


def test_old_approval_does_not_mask_a_fresh_plan(tmp_path):
    # Biết "đã duyệt" lúc 1500, nhưng plan này xong lúc 2000 (lập lại sau đó) → vẫn chờ duyệt.
    _run(tmp_path, "T-3", "r-9", "A", "2026-09-24T03:00:00Z", "agent:plan-ready", mtime=2000)
    rows = _rows(tmp_path, current={"T-3": ("agent:plan-approved", 1500.0)})
    assert rows["r-9"]["history_word"] is None
