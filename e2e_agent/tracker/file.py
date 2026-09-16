"""Backlog trên đĩa — chạy trọn pipeline không cần mạng, không cần GitLab.

Có backend này từ đầu để giao diện Tracker không bị logic GitLab làm lệch,
và để selftest chạy được ở mọi nơi.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .base import STATE_LABELS, Ticket


class FileTracker:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        (self.root / "tickets").mkdir(parents=True, exist_ok=True)
        (self.root / "notifications").mkdir(parents=True, exist_ok=True)

    # -- đọc -------------------------------------------------------------
    def _path(self, ticket_id: str) -> Path:
        return self.root / "tickets" / f"{ticket_id}.yaml"

    def get(self, ticket_id: str) -> Ticket:
        raw = yaml.safe_load(self._path(ticket_id).read_text(encoding="utf-8"))
        return Ticket(**raw)

    def list_by_label(self, label: str) -> list[Ticket]:
        out = []
        for path in sorted((self.root / "tickets").glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if label in (raw.get("labels") or []):
                out.append(Ticket(**raw))
        return out

    # -- ghi -------------------------------------------------------------
    def _save(self, ticket: Ticket) -> None:
        self._path(ticket.id).write_text(
            yaml.safe_dump(ticket.__dict__, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def comment(self, ticket_id: str, body: str) -> None:
        ticket = self.get(ticket_id)
        ticket.body = (ticket.body or "") + f"\n\n---\n{body}\n"
        self._save(ticket)

    def set_state(self, ticket_id: str, label: str) -> None:
        ticket = self.get(ticket_id)
        ticket.labels = [l for l in ticket.labels if l not in STATE_LABELS] + [label]
        self._save(ticket)

    def create_ticket(self, title: str, body: str, labels: list[str],
                      parent: str | None = None) -> Ticket:
        n = len(list((self.root / "tickets").glob("*.yaml"))) + 1
        ticket = Ticket(id=f"T-{n:03d}", title=title, body=body, labels=list(labels), parent=parent)
        self._save(ticket)
        return ticket

    def notify(self, ticket_id: str, message: str) -> None:
        path = self.root / "notifications" / f"{ticket_id}.md"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")
