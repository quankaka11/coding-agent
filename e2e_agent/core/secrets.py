"""Đọc bí mật từ biến môi trường hoặc .env. Không bao giờ ghi ra log."""
from __future__ import annotations

import os
from pathlib import Path


def read_secret(name: str) -> str:
    if value := os.environ.get(name):
        return value.strip()
    for key in (name, name.lower(), name.upper()):
        for path in (Path(".env"), Path.cwd() / ".env"):
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    field, _, value = line.partition("=")
                    if field.strip() == key and value.strip():
                        return value.strip()
    return ""


def scrub(text: str, secret: str) -> str:
    return text.replace(secret, "«redacted»") if secret else text
