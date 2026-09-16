"""Tracker: nơi chứa TICKET (Backlog, GitLab issues, hoặc thư mục YAML)."""
from __future__ import annotations

from pathlib import Path


def make(prof, base_dir: Path):
    """Dựng tracker từ hồ sơ repo. Orchestrator không biết backend nào đang chạy."""
    kind = prof.tracker_cfg("kind")
    if kind == "file":
        root = Path(prof.tracker_cfg("root"))
        from .file import FileTracker
        return FileTracker(root if root.is_absolute() else base_dir / root)
    if kind == "backlog":
        from .backlog import BacklogTracker
        return BacklogTracker(prof.tracker_cfg("space"), prof.tracker_cfg("project"),
                              prof.tracker_cfg("api_key_env"), prof.tracker_cfg("issue_type_id"))
    from .gitlab import GitLabTracker
    return GitLabTracker(prof.tracker_cfg("url"), prof.tracker_cfg("project"),
                         prof.tracker_cfg("token_env"))
