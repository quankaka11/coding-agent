"""Git — chỉ những thao tác đọc mà gate/anti-gaming cần."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def head_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def remote_tip(repo: Path, branch: str, remote: str = "origin", timeout: int = 120) -> str | None:
    """Fetch `branch` từ remote và trả về sha đầu nhánh ở đó. Không có remote thì None.

    Base của mọi run phải là đầu nhánh TRÊN REMOTE, không phải HEAD của checkout
    local: máy chạy watch không ai pull thì agent lập plan trên code cũ, MR mở
    lên nhánh gốc với base lệch, và stale-check của Phase B so với một HEAD cũng
    lạc hậu y như vậy. Dùng FETCH_HEAD để không phụ thuộc refspec của remote.
    """
    # `remote` là tên remote (origin) hoặc URL có sẵn thông tin đăng nhập do forge
    # cấp — cách sau không lưu token vào .git/config của clone.
    is_url = "://" in remote or remote.startswith("git@")
    if not is_url and remote not in _git(repo, "remote").split():
        return None
    proc = subprocess.run(["git", "fetch", "--quiet", remote, branch], cwd=repo,
                          capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f"git fetch {redact_url(remote)} {branch}: "
                           f"{redact_url(proc.stderr.strip())}")
    return _git(repo, "rev-parse", "FETCH_HEAD").strip()


def redact_url(text: str) -> str:
    """Che `user:token@` trong mọi URL — lỗi git in nguyên URL fetch."""
    return re.sub(r"://[^/]*@", "://«redacted»@", text)


def is_dirty(repo: Path) -> bool:
    """Chỉ tính thay đổi trên file đang theo dõi.

    File chưa theo dõi (artefact của test, coverage.json…) không nằm trong commit
    lẫn trong diff được review, nên không phải thứ G-5 muốn bắt.
    """
    return bool(_git(repo, "status", "--porcelain", "--untracked-files=no").strip())


def changed_files(repo: Path, base: str, head: str = "HEAD") -> list[str]:
    out = _git(repo, "diff", "--name-only", f"{base}..{head}")
    return [line for line in out.splitlines() if line]


def new_lines(repo: Path, base: str, head: str = "HEAD") -> dict[str, set[int]]:
    """{file: {số dòng được thêm}} — dùng cho G-7 và mutation."""
    out = _git(repo, "diff", "-U0", f"{base}..{head}")
    result: dict[str, set[int]] = {}
    current: str | None = None
    lineno = 0
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            result.setdefault(current, set())
        elif (m := _HUNK.match(line)) and current:
            lineno = int(m.group(1))
        elif line.startswith("+") and not line.startswith("+++") and current:
            result[current].add(lineno)
            lineno += 1
    return {f: lines for f, lines in result.items() if lines}


def added_lines_text(repo: Path, base: str, head: str = "HEAD") -> list[tuple[str, int, str]]:
    """[(file, số dòng, nội dung)] của các dòng được thêm — dùng cho G-2."""
    out = _git(repo, "diff", "-U0", f"{base}..{head}")
    rows: list[tuple[str, int, str]] = []
    current, lineno = None, 0
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif m := _HUNK.match(line):
            lineno = int(m.group(1))
        elif line.startswith("+") and not line.startswith("+++") and current:
            rows.append((current, lineno, line[1:]))
            lineno += 1
    return rows


def diff_stat(repo: Path, base: str, head: str = "HEAD") -> dict:
    """{files, insertions, deletions} của base..head — cho mô tả MR."""
    out = _git(repo, "diff", "--numstat", f"{base}..{head}")
    files = ins = dels = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        ins += int(parts[0]) if parts[0].isdigit() else 0
        dels += int(parts[1]) if parts[1].isdigit() else 0
    return {"files": files, "insertions": ins, "deletions": dels}


def file_at(repo: Path, ref: str, path: str) -> str | None:
    try:
        return _git(repo, "show", f"{ref}:{path}")
    except RuntimeError:
        return None
