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


#: Bí mật trong thư mục home mà agent không có lý do gì để đọc: (đường dẫn, là thư mục).
HOME_SECRETS = [("~/.ssh", True), ("~/.git-credentials", False), ("~/.netrc", False),
                ("~/.config/gh", True), ("~/.docker/config.json", False), ("~/.aws", True),
                ("~/.kube", True), ("~/.claude/.credentials.json", False), ("~/.pypirc", False),
                ("~/.npmrc", False), ("~/.gnupg", True)]
#: Registry gói của các hệ sinh thái phổ biến — Bash của agent (trong sandbox) chỉ gọi
#: được những domain này. Hồ sơ thêm được qua `agent.allowed_domains`.
DEFAULT_ALLOWED_DOMAINS = [
    "registry.npmjs.org", "registry.yarnpkg.com", "pypi.org", "files.pythonhosted.org",
    "proxy.golang.org", "sum.golang.org", "repo.maven.apache.org", "repo1.maven.org",
    "plugins.gradle.org", "services.gradle.org", "crates.io", "index.crates.io",
    "static.crates.io", "rubygems.org", "repo.packagist.org", "packagist.org",
    "github.com", "codeload.github.com", "objects.githubusercontent.com",
]
#: Chỗ ghi ngoài worktree mà toolchain cần (cache build của go, npm, maven, gradle…).
SANDBOX_WRITABLE = ["/tmp", "~/.cache", "~/.npm", "~/.m2", "~/.gradle", "~/go", "~/.cargo"]

_SANDBOX_CHECK: tuple[str | None, str] | None = None


def sandbox_level() -> tuple[str | None, str]:
    """Mức sandbox máy này chạy được: ("full" | "weak" | None, lý do). Cache một lần.

    full: bubblewrap tạo được PID namespace và mount /proc — máy thật có bubblewrap + socat.
          Chạy được cả CLAUDE_CODE_SUBPROCESS_ENV_SCRUB.
    weak: chỉ bind-mount được (trong Docker với seccomp/apparmor unconfined) — Claude Code
          chạy sandbox ở chế độ enableWeakerNestedSandbox: vẫn chặn đọc file bí mật và chặn
          mạng, nhưng KHÔNG chạy được scrub (đã thử thật: "Can't mount proc").
    """
    global _SANDBOX_CHECK
    if _SANDBOX_CHECK is None:
        _SANDBOX_CHECK = _probe()
    return _SANDBOX_CHECK


def sandbox_available() -> tuple[bool, str]:
    level, why = sandbox_level()
    return level is not None, why


def _probe() -> tuple[str | None, str]:
    if sys.platform == "darwin":
        return "full", "macOS sandbox-exec"
    if not sys.platform.startswith("linux"):
        return None, f"không hỗ trợ trên {sys.platform}"
    if missing := [b for b in ("bwrap", "socat") if not shutil.which(b)]:
        return None, f"thiếu {', '.join(missing)} (apt install bubblewrap socat)"

    def bwrap(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", *args, "true"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if bwrap("--proc", "/proc", "--unshare-pid").returncode == 0:
        return "full", "bubblewrap"
    weak = bwrap()
    if weak.returncode == 0:
        return "weak", "bubblewrap, chế độ nested (container)"
    return None, (f"bwrap không tạo được namespace: {weak.stderr.strip()[:120]} — trong Docker cần "
                  "security_opt seccomp:unconfined và apparmor:unconfined")


def agent_settings(prof, hook: str) -> dict:
    """`.claude/settings.json` cho worktree: hook + luật deny + sandbox.

    Luật deny (Read/Edit) chạy ở MỌI máy nhưng chỉ chặn tool Read/Edit của agent. Sandbox
    chặn cả Bash (`cat ../../.env`, `curl` ra ngoài) ở mức OS — bật khi máy có.
    """
    from ..core.secrets import secret_files
    deny: list[str] = []
    abs_read: list[str] = []
    for path in secret_files():
        is_dir = path.is_dir() or path.name == ".e2ea"
        pattern = f"//{path.as_posix().lstrip('/')}" + ("/**" if is_dir else "")
        deny += [f"Read({pattern})", f"Edit({pattern})"]
        abs_read.append(str(path))
    for path, is_dir in HOME_SECRETS:
        pattern = path + ("/**" if is_dir else "")
        deny += [f"Read({pattern})", f"Edit({pattern})"]
    settings: dict = {
        "hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit",
                                  "hooks": [{"type": "command", "command": hook}]}]},
        "permissions": {"deny": deny},
    }
    mode = prof.agent_cfg("sandbox") if prof is not None else "auto"
    level, _ = sandbox_level()
    if mode == "on" or (mode == "auto" and level):
        settings["sandbox"] = {
            "enabled": True,
            "failIfUnavailable": mode == "on",
            # Không cho agent xin chạy lệnh ngoài sandbox: headless không có ai để từ chối.
            "allowUnsandboxedCommands": False,
            # Trong container, bubblewrap không mount được /proc — chế độ nested của Claude Code.
            "enableWeakerNestedSandbox": level == "weak",
            "filesystem": {"denyRead": abs_read + [p for p, _ in HOME_SECRETS],
                           "allowWrite": list(SANDBOX_WRITABLE)},
            "network": {"allowedDomains": DEFAULT_ALLOWED_DOMAINS
                        + list(prof.agent_cfg("allowed_domains") if prof is not None else [])},
        }
    return settings


def install_guardrails(work: Path, profile_path: Path, package_root: Path | None = None) -> None:
    """Cài hook + luật deny + sandbox vào worktree, và giấu .claude khỏi git.

    Hook là lưới thứ nhất cho forbidden_paths (chặn lúc agent định ghi); G-3 vẫn là lưới
    thứ hai. Luật deny và sandbox giữ token (.env, .e2ea/, ~/.ssh…) khỏi tay agent.
    """
    from ..config.profile import ProfileError, load
    try:
        prof = load(profile_path)
    except (ProfileError, OSError):
        prof = None
    claude_dir = work / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings = agent_settings(prof, hook_command(profile_path, package_root))
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


#: Artefact của tool test/coverage không bao giờ được vào commit của agent. Nếu
#: repo đích không gitignore chúng, `git add -A` gom hết và G-3 báo "ngoài
#: allowed_paths" — một run đúng bị kết NO_MR/antigaming oan.
ARTEFACT_EXCLUDES = [
    ":(exclude)coverage.json", ":(exclude).coverage", ":(exclude,glob).coverage.*",
    ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/.pytest_cache/**",
    ":(exclude,glob)**/htmlcov/**", ":(exclude,glob)**/.claude/**",
    ":(exclude,glob)**/node_modules/**",
    # Ngôn ngữ khác: thư mục build/cache mà `commands.setup`/test đẻ ra trong worktree.
    ":(exclude,glob)**/target/**", ":(exclude,glob)**/.gradle/**",
    ":(exclude,glob)**/.next/**", ":(exclude,glob)**/.nyc_output/**",
    ":(exclude,glob)**/coverage/**",
]


def restore_files(work: Path, sha: str, files: list[str]) -> list[str]:
    """Đưa các file về đúng nội dung tại `sha`; file chưa tồn tại ở đó thì xoá.

    Khắc phục G-9 không cần LLM: test đã đóng băng thì bản đóng băng là sự thật.
    Trả về danh sách file đã đụng (để ghi log/commit).
    """
    touched: list[str] = []
    for rel in files:
        exists = subprocess.run(["git", "cat-file", "-e", f"{sha}:{rel}"], cwd=work,
                                capture_output=True).returncode == 0
        if exists:
            subprocess.run(["git", "checkout", sha, "--", rel], cwd=work,
                           capture_output=True, text=True, check=False)
        else:
            subprocess.run(["git", "rm", "-q", "-f", "--ignore-unmatch", "--", rel], cwd=work,
                           capture_output=True, text=True, check=False)
            (work / rel).unlink(missing_ok=True)
        touched.append(rel)
    return touched


def commit_all(work: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A", "--", ".", *ARTEFACT_EXCLUDES],
                   cwd=work, capture_output=True, text=True, check=False)
    subprocess.run(["git", "commit", "-m", message, "--allow-empty"],
                   cwd=work, capture_output=True, text=True, check=False)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=work,
                          capture_output=True, text=True).stdout.strip()


def prune(repo: Path, work_root: Path, older_than_days: float, branches: bool = False,
          dry_run: bool = False) -> list[str]:
    """Dọn worktree của các run cũ hơn N ngày (run lỗi/NO_MR được giữ lại để xem).

    `branches=True` xoá luôn nhánh local `agent/*` không còn worktree — chỉ nên bật khi
    forge là GitLab (nhánh đã được push); forge offline dùng chính nhánh local làm "MR".
    Trả về danh sách thứ đã (hoặc sẽ, nếu dry_run) xoá.
    """
    import time
    root = under(repo, work_root)
    cutoff = time.time() - older_than_days * 86400
    removed: list[str] = []
    if root.is_dir():
        for work in sorted(root.iterdir()):
            if work.is_dir() and work.stat().st_mtime < cutoff:
                removed.append(str(work))
                if not dry_run:
                    remove(repo, work)
    if not dry_run:
        subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True, check=False)
    if branches:
        live = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo,
                              capture_output=True, text=True).stdout
        in_use = {line.split("refs/heads/", 1)[1] for line in live.splitlines()
                  if line.startswith("branch refs/heads/")}
        refs = subprocess.run(["git", "for-each-ref", "--format=%(refname:short) %(committerdate:unix)",
                               "refs/heads/agent/"], cwd=repo, capture_output=True, text=True).stdout
        for line in refs.splitlines():
            name, _, stamp = line.partition(" ")
            if name in in_use or not stamp.isdigit() or int(stamp) >= cutoff:
                continue
            removed.append(f"branch {name}")
            if not dry_run:
                subprocess.run(["git", "branch", "-D", name], cwd=repo, capture_output=True, check=False)
    return removed
