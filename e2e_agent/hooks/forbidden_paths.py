#!/usr/bin/env python3
"""PreToolUse hook — chặn ghi vào forbidden_paths, và ra NGOÀI worktree, ngay lúc agent định ghi.

Lưới thứ nhất. G-3 vẫn chạy sau như lưới thứ hai, vì hook chỉ thấy tool call,
không thấy `sed -i` chạy qua Bash (việc đó là của sandbox).

Ghi ra ngoài worktree (`~/.bashrc`, hook git của repo gốc, file của run khác…) không
bao giờ là một phần của việc sửa ticket — trước đây hook để lọt vì đường dẫn tuyệt đối
không khớp pattern nào trong forbidden_paths. Ngoại lệ duy nhất là /tmp.

Cấu hình trong .claude/settings.json của sandbox:
    {"hooks": {"PreToolUse": [{"matcher": "Write|Edit|NotebookEdit",
        "hooks": [{"type": "command",
                   "command": "python3 -m e2e_agent.hooks.forbidden_paths --profile p.yaml"}]}]}}
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from ..config.profile import load, matches


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    args = ap.parse_args()

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # không đọc được payload thì không chặn nhầm

    tool_input = payload.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not target:
        return 0

    prof = load(args.profile)
    repo = Path(payload.get("cwd") or ".").resolve()
    path = (repo / target).resolve()          # tương đối thì tính từ worktree
    try:
        rel = str(path.relative_to(repo))
    except ValueError:
        # Thư mục làm việc (chứa profiles/, .env, runs/) không bao giờ được chạm, kể cả khi
        # nó nằm dưới /tmp — nếu không, ngoại lệ /tmp thành đường ghi đè ../../.env.
        workspace = _workspace(Path(args.profile).resolve())
        in_tmp = _inside(path, Path(tempfile.gettempdir()).resolve()) or _inside(path, Path("/tmp"))
        if in_tmp and not _inside(path, workspace):
            return 0
        print(f"CHẶN: {path} nằm ngoài worktree {repo}. Agent chỉ được ghi trong repo đang sửa "
              f"(và /tmp).", file=sys.stderr)
        return 2

    if pattern := matches(rel, prof.forbidden_paths):
        print(f"CHẶN: {rel} nằm trong forbidden_paths của hồ sơ repo "
              f"{prof.repo_id} (khớp {pattern!r}). Task cần đụng file này thì kết luận NO_MR.",
              file=sys.stderr)
        return 2  # exit 2 = chặn tool call và báo lại cho agent
    return 0


def _workspace(profile: Path) -> Path:
    """`<thư mục làm việc>/profiles/<tên>.yaml` → thư mục làm việc; hồ sơ đặt chỗ khác → thư mục của nó."""
    return profile.parent.parent if profile.parent.name == "profiles" else profile.parent


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
