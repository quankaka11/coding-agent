"""Bàn giao Phase A → người duyệt → Phase B, đi qua chính ticket.

Phase B từng lấy plan và spec từ `runs/` trên đĩa của máy đang chạy. Đổi máy,
dọn `runs/`, hay chạy `watch` từ thư mục khác là mất sạch — trong khi plan vẫn
nằm nguyên trên ticket, và người vận hành nhận một thông báo sai sự thật rằng
agent chưa từng lập plan. Nguồn sự thật phải là nơi người duyệt đã nhìn thấy.

Payload nằm trong một HTML comment ở cuối comment plan: người đọc ticket không
thấy nó, còn máy đọc không phải đoán theo định dạng markdown.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

OPEN = "<!-- e2ea:handoff"
TASK_OPEN = "<!-- e2ea:task"
CLOSE = "-->"


@dataclass(frozen=True)
class Handoff:
    """Đúng ba thứ Phase B cần, và cả ba đều là thứ người đã duyệt."""
    base_sha: str
    spec_yaml: str
    plan: str


def _wrap(open_mark: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    # Không dấu `>` nào lọt vào payload thì không chuỗi nào trong plan có thể
    # đóng sớm khối HTML comment. `>` vẫn là JSON hợp lệ.
    return f"{open_mark}\n{payload.replace('>', chr(92) + 'u003e')}\n{CLOSE}\n"


def _find(open_mark: str, texts: list[str]) -> dict | None:
    """Payload MỚI NHẤT mang dấu này. Không có thì None — không đoán."""
    for text in reversed(texts):
        if open_mark not in text:
            continue
        raw = text.split(open_mark, 1)[1].split(CLOSE, 1)[0].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def pack(human_text: str, *, base_sha: str, spec_yaml: str, plan: str) -> str:
    return f"{human_text}\n\n" + _wrap(
        OPEN, {"base_sha": base_sha, "spec": spec_yaml, "plan": plan})


def pack_task(human_text: str, *, task_type: str, modules: list[str],
              acceptance_ids: list[str]) -> str:
    """Bàn giao MR → CI.

    CI chỉ có cái MR, không có ticket lẫn `runs/`, nên nếu không mang theo thì
    G-8 và G-10 vĩnh viễn SKIP ở đúng nơi đối chứng đáng tin nhất. Không có biến
    CI nào chở được `scope.modules`, và người thì không thể tự điền vào file CI
    vì mỗi MR một giá trị khác.
    """
    return f"{human_text}\n\n" + _wrap(
        TASK_OPEN, {"task_type": task_type, "modules": list(modules),
                    "ac": list(acceptance_ids)})


def unpack_task(text: str) -> dict | None:
    """Đọc bàn giao MR → CI. Thiếu thì trả None và các luật liên quan vẫn SKIP."""
    return _find(TASK_OPEN, [text or ""])


def unpack(comments: list[str]) -> Handoff | None:
    """Lấy bàn giao MỚI NHẤT trong danh sách comment. Không có thì trả None.

    Trả None chứ không đoán: ticket bị gán `agent:plan-approved` mà chưa từng qua
    Phase A là lỗi thao tác của người, và phải được nói thẳng ra như vậy.
    """
    for text in reversed(comments):
        data = _find(OPEN, [text])
        if not data or not data.get("spec"):
            continue
        return Handoff(base_sha=str(data.get("base_sha") or ""),
                       spec_yaml=str(data["spec"]), plan=str(data.get("plan") or ""))
    return None
