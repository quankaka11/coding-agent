#!/usr/bin/env python3
"""PreToolUse hook — chặn ghi vào forbidden_paths NGAY LÚC agent định ghi.

Lưới thứ nhất. G-3 vẫn chạy sau như lưới thứ hai, vì hook chỉ thấy tool call,
không thấy `sed -i` chạy qua Bash.

Cấu hình trong .claude/settings.json của sandbox:
    {"hooks": {"PreToolUse": [{"matcher": "Write|Edit|NotebookEdit",
        "hooks": [{"type": "command",
                   "command": "python3 -m e2e_agent.hooks.forbidden_paths --profile p.yaml"}]}]}}
"""
from __future__ import annotations

import argparse
import json
import sys
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
    try:
        rel = str(Path(target).resolve().relative_to(repo))
    except ValueError:
        rel = target

    if pattern := matches(rel, prof.forbidden_paths):
        print(f"CHẶN: {rel} nằm trong forbidden_paths của hồ sơ repo "
              f"{prof.repo_id} (khớp {pattern!r}). Task cần đụng file này thì kết luận NO_MR.",
              file=sys.stderr)
        return 2  # exit 2 = chặn tool call và báo lại cho agent
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
