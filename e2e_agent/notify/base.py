"""Kênh thông báo ra ngoài.

Comment lên ticket là chưa đủ: người duyệt không ngồi canh Backlog. Không có
thông báo thì plan nằm đó chờ, và toàn bộ vòng tự động đứng lại ở đúng chỗ đó.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

#: Các thời điểm người phải ra tay.
PLAN_READY = "plan-ready"
NEEDS_HUMAN = "needs-human"
MR_CREATED = "mr-created"
NO_MR = "no-mr"
ALL_EVENTS = [PLAN_READY, NEEDS_HUMAN, MR_CREATED, NO_MR]

DEFAULT_EVENTS = [PLAN_READY, NEEDS_HUMAN, MR_CREATED]


@dataclass
class Notice:
    event: str
    title: str
    body: str
    url: str = ""

    def as_text(self) -> str:
        icon = {PLAN_READY: "📋", NEEDS_HUMAN: "🚨", MR_CREATED: "✅", NO_MR: "⊘"}.get(self.event, "•")
        parts = [f"{icon} *{self.title}*", self.body]
        if self.url:
            parts.append(self.url)
        return "\n".join(p for p in parts if p)


class Notifier(Protocol):
    def send(self, notice: Notice) -> bool: ...
