"""Đọc bí mật từ biến môi trường hoặc .env — và giữ chúng KHỎI tay agent.

Agent chạy `claude -p` có Bash, và nội dung ticket là đầu vào không tin cậy: một câu
prompt injection trong ticket đủ để nó `env` hay `cat ../../.env` rồi gửi token đi.
Ở đây có hai thứ cho việc đó: môi trường đã gỡ bí mật cho mọi tiến trình con
(`command_env`, `agent_env`), và danh sách file bí mật để sandbox/luật deny chặn đọc
(`secret_files`).
"""
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


#: Tên biến bí mật của CHÍNH hệ thống (token GitLab, API key Backlog, webhook). Hồ sơ
#: khai tên khác thì `protect()` thêm vào. So không phân biệt hoa thường.
_SECRET_NAMES: set[str] = {"gitlab_token", "backlog_api_key", "google_chat_webhook",
                           "notify_webhook_url"}
#: Credential của Claude/Anthropic: `claude` cần chúng, nhưng lệnh test/lint/setup (chạy
#: code agent vừa viết) thì không bao giờ cần.
_AGENT_CREDENTIALS = {"anthropic_api_key", "anthropic_auth_token", "claude_code_oauth_token"}


def protect(*names: str | None) -> None:
    """Đánh dấu thêm tên biến là bí mật (vd `token_env` khai trong hồ sơ repo)."""
    _SECRET_NAMES.update(n.lower() for n in names if n)


def _without(env: dict[str, str], names: set[str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k.lower() not in names}


def command_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Môi trường cho lệnh gate/test/lint/setup: không token hệ thống, không credential Claude."""
    return {**_without(dict(os.environ), _SECRET_NAMES | _AGENT_CREDENTIALS), **(extra or {})}


def agent_env(scrub: bool = False) -> dict[str, str]:
    """Môi trường cho `claude -p`: giữ credential của nó, gỡ token hệ thống.

    `scrub=True` → CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1: chính `claude` gỡ credential của nó
    khỏi môi trường của Bash/hook con và chạy Bash trong PID namespace riêng. CHỈ bật khi
    sandbox chạy được: cờ này bắt buộc sandbox (thiếu socat là Bash hỏng hẳn — đã thấy thật)
    và ép permission mode về `default`.
    """
    env = _without(dict(os.environ), _SECRET_NAMES)
    if scrub:
        env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
    else:
        env.pop("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", None)
    return env


def secret_files() -> list[Path]:
    """File chứa bí mật mà agent tuyệt đối không được đọc: các .env đang dùng và kho
    `.e2ea/` (bản cất .env của từng repo) nằm cạnh chúng."""
    out: list[Path] = []
    for env in (Path(".env"), *EXTRA_ENV_FILES):
        env = env.resolve()
        for path in (env, env.parent / ".e2ea"):
            if path not in out:
                out.append(path)
    return out
