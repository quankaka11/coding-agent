"""Forge — nơi chứa CODE. Tách hẳn khỏi tracker, nơi chứa TICKET.

Hai thứ này trước đây bị gộp làm một; thực tế ticket nằm ở Backlog còn code
nằm ở GitLab, và chúng thay đổi độc lập với nhau.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MR_LABEL = "agent-generated"
#: GitLab (và GitHub qua UI) nhận MR có tiêu đề bắt đầu bằng tiền tố này là Draft:
#: không merge được cho tới khi người bỏ tiền tố.
DRAFT_PREFIX = "Draft: "


@dataclass
class MergeRequest:
    id: str
    url: str
    source_branch: str


class Forge(Protocol):
    def push_branch(self, repo: Path, branch: str) -> None: ...
    def create_mr(self, source_branch: str, target_branch: str, title: str,
                  body: str, labels: list[str], draft: bool = False) -> MergeRequest: ...
