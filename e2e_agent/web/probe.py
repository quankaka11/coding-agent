"""Thử kết nối trước khi ghi bất cứ thứ gì.

Điểm đau của `e2ea up` là gõ .env xong, clone vài phút rồi mới biết token sai.
Ở đây mỗi kết nối thử được riêng, trong vài giây, và không để lại dấu vết nào:
không ghi .env, không clone, không tạo category.
"""
from __future__ import annotations

import subprocess

from ..core import gitutil
from ..tracker.base import ACTIVE_LABELS

# Khoá người vừa gõ được truyền thẳng vào client, KHÔNG đặt vào os.environ:
# `read_secret` đọc os.environ trước .env, nên đặt tạm ở đó là vòng quét đang
# chạy song song dùng luôn khoá chưa lưu trong mấy giây probe.


def code(repo_url: str, token: str = "") -> dict:
    """Đọc thử danh sách nhánh. Không clone, không ghi gì."""
    from ..config.autoprofile import parse_repo_url
    repo_url = (repo_url or "").strip()
    if not repo_url:
        return _no("Chưa có địa chỉ repo.")
    if "://" not in repo_url and not repo_url.startswith("git@"):
        from pathlib import Path
        path = Path(repo_url).expanduser()
        if (path / ".git").exists():
            return _yes(f"Repo trên đĩa: {path}", kind="file", name=path.name)
        return _no(f"Không thấy repo git ở {path}.")

    try:
        forge_url, project, name = parse_repo_url(repo_url)
    except Exception as exc:
        return _no(f"Không đọc được địa chỉ repo: {exc}")
    if not token:
        return _no("Repo GitLab cần token có vai trò Developer trở lên.")

    host = forge_url.split("://", 1)[-1]
    auth = f"https://oauth2:{token}@{host}/{project}.git"
    try:
        proc = subprocess.run(["git", "ls-remote", "--heads", auth], capture_output=True,
                              text=True, timeout=40, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return _no(f"Quá 40 giây không nối được tới {host}.")
    except FileNotFoundError:
        return _no("Máy chưa có git.")
    if proc.returncode != 0:
        detail = gitutil.redact_url(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "")
        if "authentication" in detail.lower() or "403" in detail or "401" in detail:
            detail = "token không đọc được repo — cần vai trò Developer trở lên"
        return _no(detail or "không nối được tới repo")
    branches = len([ln for ln in proc.stdout.splitlines() if ln.strip()])
    return _yes(f"Đọc được {branches} nhánh của {project}", kind="gitlab", name=name,
                project=project, forge_url=forge_url)


def _state_field() -> str:
    """Hồ sơ chưa có (đang cấu hình lần đầu) thì theo mặc định của hồ sơ mới."""
    from ..config.profile import DEFAULT_TRACKER
    return DEFAULT_TRACKER["state_field"]


def tracker(space: str, project: str, api_key: str) -> dict:
    """Đọc thử project và trạng thái agent. Chỉ đọc — không tạo gì ở bước này."""
    space, project = (space or "").strip(), (project or "").strip()
    if not space and not project:
        return _yes("Để trống: ticket và MR ghi vào đĩa, chạy offline được ngay.", kind="file")
    if not (space and project):
        return _no("Cần cả nơi chứa ticket lẫn mã dự án, hoặc để trống cả hai.")
    if not api_key:
        return _no("Thiếu khoá đọc/ghi ticket.")

    from ..tracker.backlog import BacklogTracker
    try:
        tr = BacklogTracker(space, project, "backlog_api_key", api_key=api_key,
                            state_field=_state_field())
        info = tr.info
        missing = tr.missing_states(ACTIVE_LABELS)
    except Exception as exc:
        return _no(_clean(exc))
    md_ok, md_note = tr.formatting()
    return _yes(f"Đọc được dự án {info.get('projectKey') or project}"
                + ("" if md_ok else f". ⚠ {md_note}"), kind="backlog",
                missing_labels=missing, markdown=md_ok)


def notify(webhook: str) -> dict:
    """Gửi một tin thật. Không có cách nào thử webhook mà không gửi gì."""
    webhook = (webhook or "").strip()
    if not webhook:
        return _yes("Để trống: không gửi thông báo, mọi thứ khác vẫn chạy.", kind=None)
    from ..notify.base import Notice, PLAN_READY
    from ..notify.webhook import WebhookNotifier
    notifier = WebhookNotifier("google_chat_webhook")
    notifier.url = webhook
    sent = notifier.send(Notice(
        event=PLAN_READY, title="e2ea đã nối được kênh này",
        body="Tin thử từ màn hình cấu hình. Plan chờ duyệt sẽ báo về đây."))
    return _yes("Đã gửi tin thử — mở kênh ra xem.") if sent else _no(
        "Không gửi được: kiểm tra lại URL webhook.")


def _yes(detail: str, **extra) -> dict:
    return {"ok": True, "detail": detail, **extra}


def _no(detail: str) -> dict:
    return {"ok": False, "detail": detail}


def _clean(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:300] if text else type(exc).__name__
