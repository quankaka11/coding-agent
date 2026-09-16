"""Worktree riêng cho mỗi run (R-2: không credential, không dữ liệu production)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HOOK_MODULE = "e2e_agent.hooks.forbidden_paths"


def create(repo: Path, work_root: Path, branch: str, base: str) -> Path:
    """Dựng worktree mới từ base. Trả về đường dẫn worktree."""
    work = work_root / branch.replace("/", "_")
    if work.exists():
        remove(repo, work)
    subprocess.run(["git", "worktree", "add", "-f", "-b", branch, str(work), base],
                   cwd=repo, capture_output=True, text=True, check=True)
    return work


def create_detached(repo: Path, work: Path, sha: str) -> Path:
    """Worktree tách rời tại một commit — dùng để dựng lại baseline, không đẻ nhánh."""
    if work.exists():
        remove(repo, work)
    subprocess.run(["git", "worktree", "add", "--detach", "-f", str(work), sha],
                   cwd=repo, capture_output=True, text=True, check=True)
    return work


def remove(repo: Path, work: Path) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(work)],
                   cwd=repo, capture_output=True, text=True, check=False)
    shutil.rmtree(work, ignore_errors=True)


def install_guardrails(work: Path, profile_path: Path, package_root: Path | None = None) -> None:
    """Cài hook chặn forbidden_paths vào worktree, và giấu .claude khỏi git.

    Hook là lưới thứ nhất (chặn lúc agent định ghi); G-3 vẫn là lưới thứ hai.
    """
    claude_dir = work / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    command = f"{sys.executable} -m {HOOK_MODULE} --profile {profile_path.resolve()}"
    settings = {"hooks": {"PreToolUse": [{
        "matcher": "Write|Edit|NotebookEdit",
        "hooks": [{"type": "command", "command": command}]}]}}
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")

    exclude = work / ".git" / "info" / "exclude"
    if not exclude.exists():                       # worktree: .git là file, không phải thư mục
        common = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"],
                                cwd=work, capture_output=True, text=True)
        exclude = Path(common.stdout.strip())
        if not exclude.is_absolute():
            exclude = work / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    body = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if ".claude/" not in body:
        exclude.write_text(body + "\n.claude/\n", encoding="utf-8")


def commit_all(work: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=work, capture_output=True, text=True, check=False)
    subprocess.run(["git", "commit", "-m", message, "--allow-empty"],
                   cwd=work, capture_output=True, text=True, check=False)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=work,
                          capture_output=True, text=True).stdout.strip()
