"""Nhãn reviewer (A2) và rollup metric (A1).

Metric đọc thẳng từ events.jsonl — không có đường thống kê riêng nào, vì thứ
không được log thì không được tính.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Nhãn chính reviewer gán sau khi review MR (quyết định A2).
OUTCOMES = {
    "merged-as-is": "Merge, không sửa dòng nào",
    "merged-trivial-fix": "Merge sau sửa vặt (đặt tên, format, ≤ ~5 dòng)",
    "merged-substantive-fix": "Merge nhưng reviewer phải sửa logic",
    "rejected-wrong": "Giải sai bài — nếu gate đã PASS thì đây là false-green",
    "rejected-unsafe": "Giải đúng nhưng rủi ro hoặc vượt phạm vi",
    "rejected-style": "Đúng và an toàn nhưng sai convention repo",
}
#: Test có thật sự kiểm được gì không — tách riêng vì fix đúng mà test rỗng vẫn qua gate.
TEST_VALUES = {"real": "test kiểm đúng hành vi",
               "weak": "test có assert nhưng không kiểm đúng cái cần kiểm",
               "empty": "test không kiểm gì"}
FALSE_GREEN = "rejected-wrong"
MERGED = ("merged-as-is", "merged-trivial-fix", "merged-substantive-fix")


@dataclass
class Review:
    outcome: str
    test_value: str
    note: str
    reviewer: str = ""
    at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def save_review(run_dir: Path, review: Review) -> Path:
    if review.outcome not in OUTCOMES:
        raise ValueError(f"outcome phải thuộc {sorted(OUTCOMES)}")
    if review.test_value not in TEST_VALUES:
        raise ValueError(f"test_value phải thuộc {sorted(TEST_VALUES)}")
    if not review.note.strip():
        raise ValueError("bắt buộc có note — 4 tuần sau không ai nhớ nổi ca này")
    path = run_dir / "review.json"
    path.write_text(json.dumps(asdict(review), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def latest_run(runs_root: Path, task_id: str) -> Path | None:
    runs = [p.parent for p in (runs_root / task_id).glob("*/outcome.json")]
    return max(runs, key=lambda p: p.stat().st_mtime) if runs else None


# -- rollup ----------------------------------------------------------------

@dataclass
class Rollup:
    runs: int = 0
    by_phase: dict = field(default_factory=dict)
    by_reason: dict = field(default_factory=dict)
    mr_rate: float = 0.0
    reviewed: int = 0
    by_review: dict = field(default_factory=dict)
    merged_as_is_rate: float | None = None
    false_green_rate: float | None = None
    test_value: dict = field(default_factory=dict)
    antigaming_triggers: dict = field(default_factory=dict)
    avg_duration_sec: float = 0.0
    total_cost_usd: float = 0.0


def collect(runs_root: Path) -> Rollup:
    roll = Rollup()
    durations: list[float] = []
    phase_b_runs = mr_runs = 0

    for outcome_path in sorted(runs_root.glob("*/*/outcome.json")):
        run_dir = outcome_path.parent
        outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        reason = outcome.get("reason", "?")
        roll.runs += 1
        roll.by_reason[reason] = roll.by_reason.get(reason, 0) + 1
        durations.append((outcome.get("duration_ms") or 0) / 1000)

        phase = _phase(run_dir)
        roll.by_phase[phase] = roll.by_phase.get(phase, 0) + 1
        if phase == "B":
            phase_b_runs += 1
            mr_runs += reason == "OK"

        for event in _events(run_dir):
            data = event.get("data") or {}
            if event["event"] == "antigaming.rule" and data.get("status") == "fail":
                rule = data.get("rule", "?")
                roll.antigaming_triggers[rule] = roll.antigaming_triggers.get(rule, 0) + 1
            elif event["event"] == "run.end":
                roll.total_cost_usd += float(data.get("cost_usd") or 0)

        review_path = run_dir / "review.json"
        if review_path.is_file():
            review = json.loads(review_path.read_text(encoding="utf-8"))
            roll.reviewed += 1
            roll.by_review[review["outcome"]] = roll.by_review.get(review["outcome"], 0) + 1
            roll.test_value[review["test_value"]] = roll.test_value.get(review["test_value"], 0) + 1

    roll.mr_rate = round(mr_runs / phase_b_runs, 3) if phase_b_runs else 0.0
    if roll.reviewed:
        roll.merged_as_is_rate = round(roll.by_review.get("merged-as-is", 0) / roll.reviewed, 3)
        roll.false_green_rate = round(roll.by_review.get(FALSE_GREEN, 0) / roll.reviewed, 3)
    roll.avg_duration_sec = round(sum(durations) / len(durations), 1) if durations else 0.0
    roll.total_cost_usd = round(roll.total_cost_usd, 4)
    return roll


def render(roll: Rollup) -> str:
    lines = [f"# Metric — {roll.runs} run", ""]
    lines += [f"- Phase: {roll.by_phase or '—'}",
              f"- Tỉ lệ ra MR (trên số run Phase B): **{roll.mr_rate:.0%}**",
              f"- Thời gian trung bình: {roll.avg_duration_sec}s · Tổng chi phí: ${roll.total_cost_usd}",
              ""]
    lines += ["## Kết cục theo mã lý do", "", "| Mã | Số run |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in sorted(roll.by_reason.items(), key=lambda x: -x[1])]
    lines.append("")
    lines += ["## Anti-gaming bị trigger", ""]
    if roll.antigaming_triggers:
        lines += ["| Luật | Số lần |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(roll.antigaming_triggers.items())]
    else:
        lines.append("Chưa lần nào.")
    lines.append("")
    lines += [f"## Nhãn reviewer — {roll.reviewed} MR đã gán", ""]
    if roll.reviewed:
        lines += ["| Nhãn | Số MR |", "|---|---|"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(roll.by_review.items(), key=lambda x: -x[1])]
        lines += ["", f"- Merge nguyên trạng: **{roll.merged_as_is_rate:.0%}**",
                  f"- **False-green** (gate xanh nhưng giải sai bài): **{roll.false_green_rate:.0%}**",
                  f"- Chất lượng test: {roll.test_value}"]
    else:
        lines.append("Chưa có MR nào được gán nhãn — mọi tỉ lệ ở trên chưa nói lên điều gì.")
    lines += ["", "---", "",
              "*Pilot loại `infrastructure` chỉ chứng minh pipeline chạy được; "
              "số đo không suy ra được giá trị trên ticket khách (R-5).*"]
    return "\n".join(lines) + "\n"


def _phase(run_dir: Path) -> str:
    for event in _events(run_dir):
        if event.get("phase") and event["phase"] != "-":
            return event["phase"]
    return "?"


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
