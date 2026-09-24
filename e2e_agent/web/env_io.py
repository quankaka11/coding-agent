"""Ghi .env từ giao diện. Giữ nguyên mọi dòng không liên quan.

Ba luật không có ngoại lệ:
  - file luôn `0600`, kể cả lúc còn đang ghi dở;
  - giá trị không bao giờ đi ngược ra trình duyệt (xem `workspace.env_view`);
  - `agent_model`/`agent_effort` chỉ đặt bằng tay trong .env.
"""
from __future__ import annotations

import os
from pathlib import Path

from .workspace import EDITABLE_KEYS, READONLY_KEYS


class EnvError(ValueError):
    pass


def write(env_path: Path, updates: dict[str, str]) -> list[str]:
    """Đặt hoặc xoá khoá. Giá trị rỗng nghĩa là xoá dòng đó.

    Trả về danh sách khoá đã đổi. Ghi qua file tạm rồi thay chỗ, để `serve` chết
    giữa chừng không để lại .env cụt — mất token là mất luôn cả vòng tự động.
    """
    for key in updates:
        if key in READONLY_KEYS:
            raise EnvError(f"{key} chỉ đặt được trong .env, không sửa qua giao diện")
        if key not in EDITABLE_KEYS:
            raise EnvError(f"không có khoá {key}")
        if "\n" in updates[key] or "\r" in updates[key]:
            raise EnvError(f"giá trị của {key} không được xuống dòng")

    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    out: list[str] = []
    seen: set[str] = set()
    changed: list[str] = []

    for line in lines:
        key = _key_of(line)
        if key is None or key not in updates:
            out.append(line)
            continue
        seen.add(key)
        value = updates[key].strip()
        if not value:                       # xoá: người để trống là người muốn bỏ
            changed.append(key)
            continue
        if line.strip() != f"{key}={value}":
            changed.append(key)
        out.append(f"{key}={value}")

    for key, value in updates.items():
        value = value.strip()
        if key in seen or not value:
            continue
        out.append(f"{key}={value}")
        changed.append(key)

    _write_private(env_path, "\n".join(out).rstrip("\n") + "\n")
    return changed


def _key_of(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.partition("=")[0].removeprefix("export ").strip()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def overridden_by_process() -> list[str]:
    """Khoá đang bị biến môi trường của tiến trình che.

    `read_secret` đọc os.environ trước .env, nên nếu không nói ra thì người lưu
    xong sẽ thấy giao diện vẫn hiện giá trị cũ mà không hiểu vì sao.
    """
    return [k for k in EDITABLE_KEYS + READONLY_KEYS if os.environ.get(k)]
