"""Chuẩn DUY NHẤT cho "run đang ở đâu" và "run còn sống không".

Trước đây mỗi chỗ tự trả lời hai câu này một kiểu: `workspace.runs_index` lấy
`phase` của sự kiện đầu tiên (tức là pha khởi động, không đổi tới cuối run), còn
`live` chỉ là "chưa thấy `run.end`" — nên một run bị kill nằm lại đó mãi mãi với
nhãn "đang chạy". Cả hai đều ở đây, và chỉ ở đây.

Quy tắc telemetry: chỉ được gọi là ĐANG CHẠY khi có nguồn hiện hành. Không có
`run.end` mà cũng không có sự kiện mới trong `STALE_AFTER_SEC` thì đó là MẤT TÍN
HIỆU, không phải đang chạy — nói thẳng ra màn hình thay vì để người đoán.
"""
from __future__ import annotations

from pathlib import Path

#: Tên bước trong máy → việc mà người hiểu. Màn hình không bao giờ hiện cột trái.
STEP_WORDS = {
    "intake": "Đọc ticket",
    "discovery": "Khảo sát repo",
    "planning": "Lập plan",
    "baseline": "Chụp mốc trên code chưa sửa",
    "verify_baseline": "Đối chiếu mốc",
    "lint_fix": "Sửa lint đỏ sẵn có",
    "test_first": "Viết test theo ticket",
    "test_gen": "Viết test theo ticket",
    "verify_fail_before": "Kiểm test mới có đỏ trên code cũ không",
    "implement": "Sửa code",
    "gate": "Chạy lại toàn bộ kiểm tra",
    "coverage": "Đo độ phủ",
    "mutation": "Thử làm hỏng code xem test có bắt được",
    "antigaming": "Soi dấu hiệu gian lận",
    "create_mr": "Mở merge request",
}

#: Đường đi chuẩn của từng pha, dùng để nói "bước mấy trên mấy". Bước tuỳ nghi
#: (lint_fix, coverage, mutation) vẫn nằm trong mẫu số: thà ước lượng hơi dư còn
#: hơn để thanh tiến độ tụt ngược khi một bước bị bỏ qua.
PHASE_STEPS = {
    "A": ["intake", "discovery", "planning"],
    "B": ["baseline", "lint_fix", "test_first", "implement", "gate",
          "coverage", "mutation", "antigaming", "create_mr"],
}

PHASE_WORDS = {"A": "Lập plan", "B": "Viết code"}

#: Bao lâu không có sự kiện mới thì coi là mất tín hiệu. Bước dài nhất của
#: pipeline là `implement` (agent chạy, có thể vài phút không ghi gì), nên ngưỡng
#: phải rộng hơn thế — nhưng vẫn đủ ngắn để người không nhìn một con số chết.
STALE_AFTER_SEC = 600


def step_word(step: str | None) -> str:
    if not step or step == "-":
        return ""
    return STEP_WORDS.get(step, step)


def phase_word(phase: str | None) -> str:
    return PHASE_WORDS.get(phase or "", "")


def liveness(row: dict, now: float) -> dict:
    """Ba field đổi theo đồng hồ, tính lại mỗi lần hỏi.

    Tách riêng khỏi `track` vì `runs_index` cache kết quả theo mtime của
    `events.jsonl`: nội dung không đổi thì khỏi parse lại, nhưng "im lặng bao lâu
    rồi" thì phải tính lại, không thì run chết vẫn mãi báo vừa mới cập nhật.
    """
    if row.get("ended") or row.get("updated_at") is None:
        return {"live": False, "stale": False,
                "idle_sec": row.get("idle_sec") if row.get("ended") else None}
    idle = max(0, int(now - row["updated_at"]))
    over = idle > STALE_AFTER_SEC
    return {"live": not over, "stale": over, "idle_sec": idle}


def track(events_path: Path, events: list[dict], now: float) -> dict:
    """Run đang ở bước nào, và còn sống không.

    `events` đã parse sẵn để không phải đọc file hai lần. `now` truyền vào để
    mọi hàng trong một lần gọi API cùng một mốc thời gian.
    """
    out = {
        "phase": None, "step": None, "step_word": "", "step_index": None,
        "steps_total": None, "started_at": None, "updated_at": None,
        "cost_usd": None, "live": False, "stale": False, "idle_sec": None,
        "ended": False,
    }
    ended = False
    seen: list[str] = []
    for event in events:
        if out["started_at"] is None:
            out["started_at"] = event.get("ts")
        if event.get("phase") not in (None, "-"):
            out["phase"] = event["phase"]          # pha HIỆN TẠI, không phải pha đầu
        if event.get("event") == "step.start":
            step = event.get("step")
            if step and step != "-":
                out["step"] = step
                if step not in seen:
                    seen.append(step)
        if event.get("event") == "run.end":
            ended = True
            out["cost_usd"] = (event.get("data") or {}).get("cost_usd")

    if out["cost_usd"] is None:
        total = sum(float((e.get("data") or {}).get("cost_usd") or 0)
                    for e in events if e.get("event") == "agent.end")
        out["cost_usd"] = round(total, 4) or None

    try:
        out["updated_at"] = events_path.stat().st_mtime
        out["idle_sec"] = max(0, int(now - out["updated_at"]))
    except OSError:
        pass

    out["ended"] = ended
    out["step_word"] = step_word(out["step"])
    out.update(liveness(out, now))
    if ended:
        return out

    plan = PHASE_STEPS.get(out["phase"] or "")
    if plan and out["step"]:
        out["steps_total"] = len(plan)
        # Bước lặp lại (vòng tự sửa) không đẩy tiến độ lùi: lấy vị trí xa nhất
        # từng chạm tới, đó mới là "đã đi được tới đâu".
        reached = [plan.index(s) for s in seen if s in plan]
        out["step_index"] = (max(reached) + 1) if reached else 1
    return out
