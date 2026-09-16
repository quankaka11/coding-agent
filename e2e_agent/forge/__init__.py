"""Forge: nơi chứa code — push nhánh và mở MR."""
from __future__ import annotations

from pathlib import Path


def make(prof, base_dir: Path):
    kind = prof.forge_cfg("kind")
    if kind == "file":
        root = Path(prof.forge_cfg("root"))
        from .file import FileForge
        return FileForge(root if root.is_absolute() else base_dir / root)
    from .gitlab import GitLabForge
    return GitLabForge(prof.forge_cfg("url"), prof.forge_cfg("project"),
                       prof.forge_cfg("token_env"))
