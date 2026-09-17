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
CLOSE = "-->"


@dataclass(frozen=True)
class Handoff:
    """Đúng ba thứ Phase B cần, và cả ba đều là thứ người đã duyệt."""
    base_sha: str
    spec_yaml: str
    plan: str


def pack(human_text: str, *, base_sha: str, spec_yaml: str, plan: str) -> str:
    payload = json.dumps({"base_sha": base_sha, "spec": spec_yaml, "plan": plan},
                         ensure_ascii=False)
    # Không dấu `>` nào lọt vào payload thì không chuỗi nào trong plan có thể
    # đóng sớm khối HTML comment. `>` vẫn là JSON hợp lệ.
    payload = payload.replace(">", "\\u003e")
    return f"{human_text}\n\n{OPEN}\n{payload}\n{CLOSE}\n"


def unpack(comments: list[str]) -> Handoff | None:
    """Lấy bàn giao MỚI NHẤT trong danh sách comment. Không có thì trả None.

    Trả None chứ không đoán: ticket bị gán `agent:plan-approved` mà chưa từng qua
    Phase A là lỗi thao tác của người, và phải được nói thẳng ra như vậy.
    """
    for text in reversed(comments):
        if OPEN not in text:
            continue
        raw = text.split(OPEN, 1)[1].split(CLOSE, 1)[0].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or not data.get("spec"):
            continue
        return Handoff(base_sha=str(data.get("base_sha") or ""),
                       spec_yaml=str(data["spec"]), plan=str(data.get("plan") or ""))
    return None
