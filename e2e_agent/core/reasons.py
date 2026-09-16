"""Mã kết cục (B2). Dùng chung cho log, label và metric — một nguồn duy nhất."""
from __future__ import annotations

from enum import Enum


class Reason(str, Enum):
    OK = "OK"
    NOMR_NOT_READY = "NOMR_NOT_READY"
    NOMR_T4 = "NOMR_T4"
    NOMR_PLAN_REJECTED = "NOMR_PLAN_REJECTED"
    NOMR_BASELINE_RED = "NOMR_BASELINE_RED"
    NOMR_TEST_PASSES_PRE = "NOMR_TEST_PASSES_PRE"
    NOMR_GATE_FAIL = "NOMR_GATE_FAIL"
    NOMR_LOOP_LIMIT = "NOMR_LOOP_LIMIT"
    HUMAN_ANTIGAMING = "HUMAN_ANTIGAMING"
    HUMAN_FLAKY = "HUMAN_FLAKY"
    HUMAN_BUDGET = "HUMAN_BUDGET"
    HUMAN_PLAN_STALE = "HUMAN_PLAN_STALE"
    HUMAN_CI_MISMATCH = "HUMAN_CI_MISMATCH"
    ERROR = "ERROR"


#: Câu giải thích cho người đọc báo cáo (mục 4.5 của build-plan).
EXPLAIN: dict[Reason, str] = {
    Reason.OK: "Đủ điều kiện tạo MR.",
    Reason.NOMR_NOT_READY: "Ticket chưa đạt Definition of Ready.",
    Reason.NOMR_T4: "Task không kiểm chứng được bằng test tất định (T4).",
    Reason.NOMR_PLAN_REJECTED: "Plan bị từ chối quá 2 lần.",
    Reason.NOMR_BASELINE_RED: "Baseline không sạch nên không phân biệt được lỗi sẵn có với lỗi agent gây ra.",
    Reason.NOMR_TEST_PASSES_PRE: "Test viết trước đã pass trên code chưa sửa — không có bug xác định để sửa.",
    Reason.NOMR_GATE_FAIL: "Gate vẫn fail sau số vòng tự sửa cho phép.",
    Reason.NOMR_LOOP_LIMIT: "Vòng implement chạm trần lặp.",
    Reason.HUMAN_ANTIGAMING: "Anti-gaming bắt được vi phạm. KHÔNG cho agent tự sửa — giữ nguyên hiện trường cho người xem.",
    Reason.HUMAN_FLAKY: "Test không nhất quán giữa các lần chạy.",
    Reason.HUMAN_BUDGET: "Vượt trần thời gian hoặc chi phí.",
    Reason.HUMAN_PLAN_STALE: "Code đã đổi kể từ lúc plan được duyệt.",
    Reason.HUMAN_CI_MISMATCH: "Gate trong sandbox và gate trên CI cho kết quả khác nhau.",
    Reason.ERROR: "Hệ thống gặp lỗi ngoài dự kiến.",
}

#: Kết cục → label đặt lên ticket.
LABEL: dict[Reason, str] = {Reason.OK: "agent:mr-created", Reason.ERROR: "agent:needs-human"}
for _r in Reason:
    LABEL.setdefault(_r, "agent:needs-human" if _r.name.startswith("HUMAN_") else "agent:no-mr")


def explain(reason: Reason) -> str:
    return EXPLAIN.get(reason, reason.value)
