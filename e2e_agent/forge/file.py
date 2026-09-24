"""Forge trên đĩa — chạy trọn pipeline không cần mạng."""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from .base import DRAFT_PREFIX, MergeRequest

#: Đánh số MR theo số file đang có — hai run song song phải lần lượt, không thì trùng số.
_NUMBER_LOCK = threading.Lock()


class FileForge:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        (self.root / "mrs").mkdir(parents=True, exist_ok=True)

    def push_branch(self, repo: Path, branch: str) -> None:
        """Không có remote: giữ nhánh lại trong repo gốc để người xem được."""
        subprocess.run(["git", "branch", "-f", branch, "HEAD"], cwd=repo,
                       capture_output=True, text=True, check=False)

    def create_mr(self, source_branch: str, target_branch: str, title: str,
                  body: str, labels: list[str], draft: bool = False) -> MergeRequest:
        if draft and not title.startswith(DRAFT_PREFIX):
            title = DRAFT_PREFIX + title
        with _NUMBER_LOCK:
            n = len(list((self.root / "mrs").glob("*.md"))) + 1
            path = self.root / "mrs" / f"mr-{n:03d}.md"
            path.write_text(f"# {title}\n\n`{source_branch}` → `{target_branch}` · labels: "
                            f"{', '.join(labels)}\n\n{body}\n", encoding="utf-8")
        return MergeRequest(id=f"mr-{n:03d}", url=str(path), source_branch=source_branch)
