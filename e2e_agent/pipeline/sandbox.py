"""Worktree riêng cho mỗi run (R-2: không credential, không dữ liệu production)."""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HOOK_MODULE = "e2e_agent.hooks.forbidden_paths"


def under(repo: Path, work_root: Path) -> Path:
    """Neo work_root vào repo.

    work_root tương đối là tương đối với REPO, không phải với thư mục đang chạy
    lệnh: `git worktree add` hiểu đường dẫn theo repo, còn subprocess sau đó lại
    hiểu cwd theo tiến trình. Hai cách hiểu lệch nhau thì worktree nằm một nơi mà
    gate chạy một nơi khác, và mọi kết luận phía sau đều sai.
    """
    return (repo / work_root).resolve()


def create(repo: Path, work_root: Path, branch: str, base: str) -> Path:
    """Dựng worktree mới từ base. Trả về đường dẫn worktree (tuyệt đối)."""
    work = under(repo, work_root) / branch.replace("/", "_")
    if work.exists():
        remove(repo, work)
    subprocess.run(["git", "worktree", "add", "-f", "-b", branch, str(work), base],
                   cwd=repo, capture_output=True, text=True, check=True)
    return _assert_worktree(work)


def create_detached(repo: Path, work: Path, sha: str) -> Path:
    """Worktree tách rời tại một commit — dùng để dựng lại baseline, không đẻ nhánh."""
    work = work.resolve()
    if work.exists():
        remove(repo, work)
    subprocess.run(["git", "worktree", "add", "--detach", "-f", str(work), sha],
                   cwd=repo, capture_output=True, text=True, check=True)
    return _assert_worktree(work)


def _assert_worktree(work: Path) -> Path:
    """Chốt rằng thư mục vừa dựng đúng là gốc của một worktree.

    Nếu đường dẫn bị hiểu lệch, chỗ này thấy toplevel là repo khác (hoặc không
    phải repo nào) và dừng ngay, thay vì để gate chạy trên thư mục rỗng rồi kết
    luận "không có test nào".
    """
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=work,
                          capture_output=True, text=True)
    top = Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None
    if top != work:
        raise RuntimeError(
            f"worktree hỏng: {work} không phải gốc worktree (git thấy gốc là {top})")
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
    command = hook_command(profile_path, package_root)
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


def hook_command(profile_path: Path, package_root: Path | None = None) -> str:
    """Lệnh hook, có chỉ đường import nếu gói chưa được cài vào interpreter này.

    Hook không import được thì nó thoát mã 1, mà chỉ mã 2 mới chặn tool call —
    lưới thứ nhất biến mất mà không ai hay. `package_root` trước đây được nhận vào
    rồi bỏ quên ở đây, chính là chỗ lẽ ra nó phải được dùng.
    """
    parts = []
    if package_root is not None:
        parts.append(f"PYTHONPATH={shlex.quote(str(Path(package_root).resolve()))}")
    parts += [shlex.quote(sys.executable), "-m", HOOK_MODULE,
              "--profile", shlex.quote(str(profile_path.resolve()))]
    return " ".join(parts)


def hook_importable(package_root: Path | None = None) -> bool:
    """Interpreter sẽ chạy hook có import được gói không — dùng cho doctor."""
    import os
    env = dict(os.environ)
    if package_root is not None:
        env["PYTHONPATH"] = str(Path(package_root).resolve())
    proc = subprocess.run([sys.executable, "-c", f"import {HOOK_MODULE}"],
                          capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL)
    return proc.returncode == 0


def commit_all(work: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=work, capture_output=True, text=True, check=False)
    subprocess.run(["git", "commit", "-m", message, "--allow-empty"],
                   cwd=work, capture_output=True, text=True, check=False)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=work,
                          capture_output=True, text=True).stdout.strip()
