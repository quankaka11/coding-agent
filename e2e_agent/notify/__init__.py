"""Thông báo ra ngoài: Google Chat / Slack / webhook khác."""
from __future__ import annotations

from .base import DEFAULT_EVENTS, Notice


class NullNotifier:
    """Không gửi đi đâu — mọi thứ vẫn nằm trong log và trên ticket."""

    configured = False

    def send(self, notice: Notice) -> bool:
        return False


def make(prof):
    kind = prof.notify_cfg("kind")
    if kind == "webhook":
        from .webhook import WebhookNotifier
        return WebhookNotifier(prof.notify_cfg("url_env"))
    return NullNotifier()


def wanted(prof) -> list[str]:
    return list(prof.notify_cfg("events") or DEFAULT_EVENTS)
