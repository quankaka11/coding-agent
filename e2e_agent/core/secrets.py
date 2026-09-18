"""Đọc bí mật từ biến môi trường hoặc .env. Không bao giờ ghi ra log."""
from __future__ import annotations

import os
import re
from pathlib import Path

#: File .env ngoài thư mục hiện tại — `e2ea up --dir X` thêm X/.env vào đây.
EXTRA_ENV_FILES: list[Path] = []


def read_secret(name: str) -> str:
    if value := os.environ.get(name):
        return value.strip()
    for key in (name, name.lower(), name.upper()):
        for path in (Path(".env"), *EXTRA_ENV_FILES):
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    field, _, value = line.partition("=")
                    if field.strip().removeprefix("export ").strip() == key and (v := _clean(value)):
                        return v
    return ""


def _clean(value: str) -> str:
    """Bỏ nháy bao ngoài và comment cùng dòng (` # ...`).

    Một dấu `#` cách một khoảng trắng sau giá trị là ghi chú của người, không phải
    một phần của token — lấy cả vào là URL clone thành rác và token lộ ra trong
    thông báo lỗi.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return re.split(r"\s+#", value, 1)[0].strip()


def scrub(text: str, secret: str) -> str:
    return text.replace(secret, "«redacted»") if secret else text
