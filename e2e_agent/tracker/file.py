"""Backlog trên đĩa — chạy trọn pipeline không cần mạng, không cần GitLab.

Có backend này từ đầu để giao diện Tracker không bị logic GitLab làm lệch,
và để selftest chạy được ở mọi nơi.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import yaml

from .base import STATE_LABELS, Ticket, meta

_CREATE_LOCK = threading.Lock()


class FileTracker:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        (self.root / "tickets").mkdir(parents=True, exist_ok=True)
        (self.root / "comments").mkdir(parents=True, exist_ok=True)
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

    def _comments_path(self, ticket_id: str) -> Path:
        return self.root / "comments" / f"{ticket_id}.jsonl"

    def comment(self, ticket_id: str, body: str) -> None:
        """Ghi vào kho comment riêng, KHÔNG nối vào mô tả ticket.

        Backlog và GitLab đều tách hai thứ này. Nối vào body cho tiện thì backend
        đĩa hết giống backend thật, và mọi test chạy trên nó thôi chứng minh được
        điều gì cho lúc chạy thật.
        """
        with self._comments_path(ticket_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"body": body}, ensure_ascii=False) + "\n")

    def comments(self, ticket_id: str) -> list[str]:
        path = self._comments_path(ticket_id)
        if not path.is_file():
            return []
        return [json.loads(line)["body"]
                for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def set_state(self, ticket_id: str, label: str) -> None:
        ticket = self.get(ticket_id)
        ticket.labels = [l for l in ticket.labels if l not in STATE_LABELS] + [label]
        self._save(ticket)

    def create_ticket(self, title: str, body: str, labels: list[str],
                      parent: str | None = None) -> Ticket:
        with _CREATE_LOCK:
            n = len(list((self.root / "tickets").glob("*.yaml"))) + 1
            # Metadata nhúng trong mô tả là hợp đồng chung; backend đĩa phải đọc nó
            # giống hệt Backlog và GitLab, nếu không nó lại che mất lỗi thay vì lộ ra.
            ticket = Ticket(id=f"T-{n:03d}", title=title, body=body, labels=list(labels),
                            parent=parent or meta(body, "parent"),
                            base_sha=meta(body, "base_sha"))
            self._save(ticket)
        return ticket

    def notify(self, ticket_id: str, message: str) -> None:
        path = self.root / "notifications" / f"{ticket_id}.md"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")
