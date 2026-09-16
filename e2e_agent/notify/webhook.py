"""Webhook JSON — dùng được cho Google Chat, Slack, Mattermost, Teams…

Cả ba đều nhận `{"text": "..."}` nên một backend là đủ; khác biệt nằm ở URL.
URL chứa token nên đọc từ biến môi trường, không để trong hồ sơ repo.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..core.secrets import read_secret
from .base import Notice


class WebhookNotifier:
    def __init__(self, url_env: str, timeout: int = 15) -> None:
        self.url = read_secret(url_env)
        self.url_env = url_env
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def send(self, notice: Notice) -> bool:
        if not self.configured:
            return False
        payload = json.dumps({"text": notice.as_text()}).encode()
        req = urllib.request.Request(self.url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout):
                return True
        except (urllib.error.URLError, OSError):
            return False   # thông báo hỏng không được làm đổ cả pipeline
