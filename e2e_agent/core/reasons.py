"""Mã kết cục (B2). Dùng chung cho log, label và metric — một nguồn duy nhất.

Đúng BỐN kết cục, theo skill e2e-agent (references/e2e-agent/SKILL.md, mục "Khi kết
luận NO_MR"): ra MR, NO_MR, cần người, lỗi hệ thống. Mọi thứ khác không phải kết cục
mà là việc agent phải làm tiếp trong cùng run (sửa lint, viết lại test, sửa code
theo gate/anti-gaming, lập plan lại).

`Kind` là chi tiết "vì sao" — đi vào log, metric và comment ticket, KHÔNG phải một
trạng thái riêng. Người vận hành chỉ cần nhìn Reason để biết mình có việc hay không:
  NO_MR        agent đã thử và kết luận không ra MR được. Kết quả hợp lệ, nhánh để lại,
               lý do cụ thể trong comment. Không ai phải làm gì ngay.
  NEEDS_HUMAN  chỉ khi bằng chứng không còn tin được (flaky, evidence hỏng) hoặc cần
               một quyết định máy không có quyền đoán (hồ sơ repo, ticket đặt sai).
"""
from __future__ import annotations

from enum import Enum


class Reason(str, Enum):
    OK = "OK"
    NO_MR = "NO_MR"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    ERROR = "ERROR"


class Kind(str, Enum):
    """Chi tiết của kết cục. Nhóm theo Reason mà nó thường đi cùng."""
    # -- NO_MR ------------------------------------------------------------
    NOT_READY = "not_ready"              # Definition of Ready chưa đạt
    T4 = "t4"                            # không kiểm chứng được bằng test tất định
    PLAN_REJECTED = "plan_rejected"      # người bác plan quá số lần
    PLAN_STALE = "plan_stale"            # code trong phạm vi đổi sau khi duyệt → tự lập plan lại
    BASELINE_RED = "baseline_red"        # build/test đỏ sẵn trong phạm vi; lint đỏ không sửa được
    TESTGEN_BROKEN = "testgen_broken"    # test viết ra không chạy được, sau các lần sửa
    TEST_PASSES_PRE = "test_passes_pre"  # test pass sẵn trên code cũ, sau các lần sửa
    T2_RED = "t2_red"                    # T2 mà test bù đỏ trên code chưa sửa, sau các lần sửa
    GATE_FAIL = "gate_fail"              # gate vẫn fail sau số vòng tự sửa
    LOOP_LIMIT = "loop_limit"            # cùng một lỗi lặp lại quá trần
    ANTIGAMING = "antigaming"            # anti-gaming vẫn fail sau số vòng tự sửa
    # -- NEEDS_HUMAN --------------------------------------------------------
    FLAKY = "flaky"                      # test không nhất quán giữa các lần chạy
    ANTIGAMING_EVIDENCE = "antigaming_evidence"  # G-4 thiếu bằng chứng / G-5 lệch commit
    MISROUTED = "misrouted"              # người đặt category sai
    PROFILE_GAP = "profile_gap"          # hồ sơ repo thiếu thứ loại task này cần
    BUDGET = "budget"                    # vượt trần thời gian/chi phí
    STALE_RUN = "stale_run"              # run đã chết mà chưa chốt kết cục
    # -- ERROR ----------------------------------------------------------------
    SYSTEM = "system"


#: Câu giải thích cho người đọc báo cáo (mục 4.5 của build-plan).
EXPLAIN: dict[Reason, str] = {
    Reason.OK: "Đủ điều kiện tạo MR.",
    Reason.NO_MR: ("Agent đã thử và kết luận không ra MR được. Đây là kết quả hợp lệ: nhánh "
                   "để lại cho người xem, lý do cụ thể ở dưới. Sửa nguyên nhân rồi đặt lại "
                   "`agent:try` nếu muốn chạy lại."),
    Reason.NEEDS_HUMAN: ("Cần người: bằng chứng không còn tin được, hoặc phải quyết một điều máy "
                         "không có quyền đoán. Agent không tự sửa tiếp."),
    Reason.ERROR: "Hệ thống gặp lỗi ngoài dự kiến.",
}

EXPLAIN_KIND: dict[Kind, str] = {
    Kind.NOT_READY: "Ticket chưa đạt Definition of Ready — viết rõ mục còn thiếu rồi đặt lại `agent:try`.",
    Kind.T4: "Task không kiểm chứng được bằng test tất định (phụ thuộc output LLM, đổi prompt…).",
    Kind.PLAN_REJECTED: "Plan bị từ chối quá số lần cho phép — ticket cần viết lại cho rõ.",
    Kind.PLAN_STALE: ("Code trong phạm vi plan đã đổi trên nhánh gốc sau khi duyệt. Ticket gốc "
                      "đã được đặt lại `agent:try` để agent lập plan mới trên code mới."),
    Kind.BASELINE_RED: ("Code chưa sửa đã đỏ trong chính phạm vi sắp sửa, hoặc lint đỏ mà agent "
                        "không tự sửa được — không phân biệt được lỗi sẵn có với lỗi agent gây ra."),
    Kind.TESTGEN_BROKEN: "Test viết ra không chạy được (sai import, cú pháp) dù đã cho sửa lại.",
    Kind.TEST_PASSES_PRE: "Test viết theo ticket đã pass trên code chưa sửa — không có bug xác định để sửa.",
    Kind.T2_RED: ("Task khai T2 (test bù cho code đúng) nhưng test bù vẫn đỏ trên code chưa sửa "
                  "sau khi agent kiểm lại kỳ vọng — nhiều khả năng code có bug thật; đổi ticket sang T1."),
    Kind.GATE_FAIL: "Gate vẫn fail sau số vòng agent tự sửa cho phép.",
    Kind.LOOP_LIMIT: "Cùng một lỗi lặp lại quá trần — dừng để khỏi đốt token.",
    Kind.ANTIGAMING: "Anti-gaming vẫn fail sau số vòng agent tự sửa cho phép.",
    Kind.FLAKY: "Test không nhất quán giữa các lần chạy — không quarantine, không skip, không xoá.",
    Kind.ANTIGAMING_EVIDENCE: ("Bằng chứng fail-trước hỏng hoặc gate không cùng commit — không ai được "
                               "tự sửa, giữ nguyên hiện trường."),
    Kind.MISROUTED: ("Ticket bị đặt vào trạng thái dành cho ticket do agent sinh ra. Người chỉ nên đặt "
                     "`agent:try`; `agent:plan-approved`/`agent:plan-rejected` chỉ dùng trên ticket đã có plan."),
    Kind.PROFILE_GAP: "Hồ sơ repo thiếu thứ mà loại task này bắt buộc phải có để kết luận được.",
    Kind.BUDGET: "Vượt trần thời gian hoặc chi phí của run.",
    Kind.STALE_RUN: "Run đã bắt đầu từ lâu mà chưa chốt kết cục — tiến trình nhiều khả năng đã bị kill.",
    Kind.SYSTEM: "Lỗi hệ thống.",
}

#: Kết cục → label đặt lên ticket.
LABEL: dict[Reason, str] = {
    Reason.OK: "agent:mr-created",
    Reason.NO_MR: "agent:no-mr",
    Reason.NEEDS_HUMAN: "agent:needs-human",
    Reason.ERROR: "agent:needs-human",
}


def explain(reason: Reason, kind: Kind | str | None = None) -> str:
    text = EXPLAIN.get(reason, reason.value)
    if kind:
        try:
            text += " " + EXPLAIN_KIND[Kind(kind)]
        except (ValueError, KeyError):
            pass
    return text
