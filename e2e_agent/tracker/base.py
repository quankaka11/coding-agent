"""Giao diện backlog. Orchestrator chỉ biết giao diện này, không biết GitLab."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# Máy trạng thái label (quyết định A7, A8, B2).
TRY = "agent:try"                  # ticket gốc, chờ Phase A
RUNNING = "agent:running"
PLAN_READY = "agent:plan-ready"    # chờ người duyệt
PLAN_APPROVED = "agent:plan-approved"
PLAN_REJECTED = "agent:plan-rejected"
IMPL = "agent:impl"                # ticket chi tiết, chờ Phase B
MR_CREATED = "agent:mr-created"
NO_MR = "agent:no-mr"
NEEDS_HUMAN = "agent:needs-human"

ALL_LABELS = [TRY, RUNNING, PLAN_READY, PLAN_APPROVED, PLAN_REJECTED, IMPL,
              MR_CREATED, NO_MR, NEEDS_HUMAN]
#: Các label trạng thái loại trừ nhau — đặt cái mới thì gỡ hết cái cũ.
STATE_LABELS = [TRY, RUNNING, PLAN_READY, PLAN_APPROVED, PLAN_REJECTED, IMPL,
                MR_CREATED, NO_MR, NEEDS_HUMAN]


@dataclass
class Ticket:
    id: str
    title: str
    body: str = ""
    labels: list[str] = field(default_factory=list)
    url: str = ""
    parent: str | None = None          # ticket chi tiết trỏ về ticket gốc
    base_sha: str | None = None        # sha lúc plan được duyệt (quyết định D1)

    @property
    def reject_count(self) -> int:
        return self.body.count("<!-- plan-rejected -->")


class Tracker(Protocol):
    """Nơi chứa TICKET. Không biết gì về code, nhánh hay MR — đó là việc của Forge."""

    def list_by_label(self, label: str) -> list[Ticket]: ...
    def get(self, ticket_id: str) -> Ticket: ...
    def comment(self, ticket_id: str, body: str) -> None: ...
    def set_state(self, ticket_id: str, label: str) -> None: ...
    def create_ticket(self, title: str, body: str, labels: list[str],
                      parent: str | None = None) -> Ticket: ...
    def notify(self, ticket_id: str, message: str) -> None: ...
