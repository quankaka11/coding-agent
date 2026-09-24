"""Sinh hồ sơ repo bằng cách soi repo thật, thay cho việc viết tay.

Hồ sơ vẫn là file duy nhất chứa tri thức về dự án (N-1); đây chỉ là cách tạo ra
bản nháp đầu tiên. Mọi thứ đoán được đều ghi thành ghi chú ở đầu file để người
đọc biết cái gì là suy luận, cái gì là chắc chắn. Không đoán được thì ghi TODO
và để `doctor` chặn — không bao giờ im lặng điền một lệnh không chạy được.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

#: Thư mục không bao giờ là mã nguồn cần sửa.
_SKIP_DIRS = {".git", ".venv", "venv", "env", ".env", "node_modules", "build", "dist",
              "docs", "doc", "scripts", "bin", "tools", "__pycache__", ".github", "ci",
              "work", "runs", "migrations", "alembic", "notebooks", "examples", "prompts"}
_TEST_DIRS = ("tests", "test", "spec")
#: Luôn cấm dù repo có hay không: cấu hình CI, hạ tầng, bí mật. File dependency KHÔNG
#: nằm ở đây nữa — chúng thuộc `dependency_files` (mặc định của hồ sơ): relaxed cho sửa
#: kèm cờ review (G-11), strict thì G-11 chặn.
_ALWAYS_FORBIDDEN = ["**/.gitlab-ci.yml", ".github/workflows/**", "ci/**",
                     "Dockerfile*", "docker-compose*", ".env*", "prompts/**"]
_PY_MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile")
_OTHER_MANIFESTS = ("package.json", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
                    "Cargo.toml", "composer.json", "Gemfile", "mix.exs", "pubspec.yaml")


@dataclass
class Draft:
    profile: dict
    notes: list[str] = field(default_factory=list)
    #: Điều bắt buộc người phải sửa trước khi chạy — doctor cũng sẽ chặn.
    todos: list[str] = field(default_factory=list)

    def dumps(self) -> str:
        head = ["# Hồ sơ repo SINH TỰ ĐỘNG bởi `e2ea init`. Sửa tay tuỳ ý — lần sau init không ghi đè.",
                "# Khoá KHÔNG nằm trong file này; chúng đọc từ .env theo tên biến khai bên dưới.", "#"]
        head += [f"# ĐOÁN: {n}" for n in self.notes]
        head += [f"# TODO: {t}" for t in self.todos]
        return "\n".join(head) + "\n\n" + yaml.safe_dump(self.profile, allow_unicode=True,
                                                          sort_keys=False, width=100)


def parse_repo_url(url: str) -> tuple[str, str, str]:
    """(forge_url, project_path, repo_name) từ URL clone HTTPS hoặc SSH của GitLab."""
    url = url.strip()
    if url.startswith("git@"):                       # git@host:group/name.git
        host, _, path = url[4:].partition(":")
        scheme = "https"
    else:
        u = urlparse(url)
        host, path, scheme = u.netloc, u.path, u.scheme or "https"
        if "@" in host:                              # bỏ user:token@ nếu có
            host = host.rsplit("@", 1)[1]
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not host or "/" not in path:
        raise ValueError(f"không đọc được URL repo: {url!r} — cần dạng https://host/nhom/ten.git")
    return f"{scheme}://{host}", path, path.rsplit("/", 1)[1]


def base_branch(repo: Path) -> str | None:
    for args in (("symbolic-ref", "--short", "refs/remotes/origin/HEAD"),
                 ("rev-parse", "--abbrev-ref", "HEAD")):
        proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip() and proc.stdout.strip() != "HEAD":
            return proc.stdout.strip().removeprefix("origin/")
    return None


def is_python_repo(repo: Path) -> bool:
    """Manifest quyết trước, đếm file .py sau.

    Chỉ đếm .py thì một repo Node có vài script Python ở gốc bị nhận nhầm là Python,
    và cả luồng venv/pytest chạy trên một repo không có lấy một test pytest.
    """
    if any((repo / f).exists() for f in _PY_MANIFESTS):
        return True
    if any((repo / f).exists() for f in _OTHER_MANIFESTS) or any(repo.glob("*.csproj")) \
            or any(repo.glob("*.sln")):
        return False
    return any(p.suffix == ".py" for p in repo.iterdir() if p.is_file()) or any(
        d.is_dir() and not d.name.startswith(".") and d.name not in _SKIP_DIRS and _has_py(d)
        for d in repo.iterdir())


def _has_py(d: Path) -> bool:
    return any(True for _ in d.rglob("*.py"))


def _importable(module: str, python: str = sys.executable) -> bool:
    proc = subprocess.run([python, "-c", f"import {module}"], capture_output=True,
                          stdin=subprocess.DEVNULL)
    return proc.returncode == 0


#: Kết quả gom test của một lần `init`, để `ensure_venv` và `generate` không
#: chạy `pytest --collect-only` hai lần trên cùng một repo (khoá: python, repo).
_COLLECT: dict[tuple[str, str], subprocess.CompletedProcess] = {}

#: pytest thoát 5 khi gom xong mà không có test nào. Khác hẳn 1 (test đỏ) và
#: 2 (gom hỏng): 5 nói repo chưa có test, không nói hạ tầng hỏng.
PYTEST_NO_TESTS = 5


def _pytest_collect(python: str, repo: Path) -> subprocess.CompletedProcess:
    """Gom thử test một lần cho mỗi (python, repo). Import hỏng, thiếu thư viện,
    hay repo chưa có test pytest đều lộ ra ở đây — lúc `init`, không phải mãi
    Phase B."""
    key = (python, str(repo.resolve()))
    if key not in _COLLECT:
        _COLLECT[key] = _run([python, "-m", "pytest", "--collect-only", "-q",
                              "-p", "no:cacheprovider"], cwd=repo, timeout=300)
    return _COLLECT[key]


def _run(argv: list[str], cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(argv, 124, exc.stdout or "", f"timeout {timeout}s")


def _tail(text: str, n: int = 3) -> str:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return " | ".join(lines[-n:])[:300]


def _extras(pyproject: Path) -> list[str]:
    """Tên các nhóm optional-dependencies trong pyproject — để cài cả dev/test."""
    text = _read(pyproject)
    if not text:
        return []
    try:
        import tomllib                     # 3.11+
        data = tomllib.loads(text)
        return sorted((data.get("project") or {}).get("optional-dependencies") or {})
    except Exception:                      # 3.10: đọc thô phần [project.optional-dependencies]
        import re
        m = re.search(r"\[project\.optional-dependencies\]\s*\n(.*?)(?=\n\[|\Z)", text, re.S)
        return sorted(re.findall(r"^\s*([A-Za-z0-9_.-]+)\s*=", m.group(1), re.M)) if m else []


def desired_python(repo: Path) -> tuple[str | None, str]:
    """Phiên bản Python repo muốn ("3.12") và nguồn của kết luận đó.

    Thứ tự tin cậy: .python-version → requires-python trong pyproject → uv.lock →
    `FROM python:X.Y` trong Dockerfile. Không có gì thì None: dùng bản mới nhất
    có trên máy. Tạo venv bằng python3 mặc định của máy là sai ở mọi repo dùng
    cú pháp mới hơn — test không import được và baseline đỏ ở mọi run.
    """
    import re
    pv = _read(repo / ".python-version").strip()
    if m := re.match(r"(\d+\.\d+)", pv):
        return m.group(1), ".python-version"
    for src, text in (("pyproject.toml", _read(repo / "pyproject.toml")),
                      ("uv.lock", _read(repo / "uv.lock")[:2000])):
        if m := re.search(r'requires-python\s*=\s*"([^"]+)"', text):
            spec = m.group(1)
            if v := _min_version(spec):
                return v, f"requires-python {spec} trong {src}"
    for df in sorted(repo.glob("Dockerfile*")):
        if m := re.search(r"^FROM\s+(?:\S+/)?python:(\d+\.\d+)", _read(df), re.M):
            return m.group(1), f"FROM python:{m.group(1)} trong {df.name}"
    return None, "không khai — dùng python mới nhất có trên máy"


def _min_version(spec: str) -> str | None:
    """">=3.11,<4" → "3.11"; "~=3.12.0" → "3.12"; "==3.10.*" → "3.10"."""
    import re
    for m in re.finditer(r"(>=|~=|==|>)\s*(\d+)\.(\d+)", spec):
        op, major, minor = m.groups()
        return f"{major}.{int(minor) + (1 if op == '>' else 0)}"
    return None


def find_python(version: str | None) -> tuple[str | None, str]:
    """Đường dẫn interpreter cho phiên bản mong muốn, thử uv tải về nếu máy chưa có."""
    if version:
        if exe := shutil.which(f"python{version}"):
            return exe, f"python{version} có sẵn trên máy"
        if shutil.which("uv"):
            if _run(["uv", "python", "install", "--quiet", version], timeout=600).returncode == 0:
                proc = _run(["uv", "python", "find", version])
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.strip(), f"python {version} do uv tải về"
        return None, f"máy không có python{version} và không tải được bằng uv"
    for minor in range(14, 9, -1):
        if exe := shutil.which(f"python3.{minor}"):
            return exe, f"python3.{minor} là bản mới nhất trên máy"
    return sys.executable, "python đang chạy e2ea"


def _venv_version(py: Path) -> str:
    proc = _run([str(py), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
    return proc.stdout.strip() if proc.returncode == 0 else ""


def ensure_venv(repo: Path, venv: Path) -> tuple[str, list[str], list[str]]:
    """Tạo venv riêng cho repo và cài mọi thứ gate cần vào đó.

    Trả về (đường dẫn python trong venv, ghi chú, TODO). Gate chạy test bằng môi
    trường của máy watch, nên môi trường đó phải có dependency của repo — cài tay
    là bước người hay quên nhất, và quên thì baseline báo ERROR "lệnh test không
    chạy được" ở mãi Phase B. Cài ở đây để lỗi lộ ra ngay lúc `init`.
    """
    notes: list[str] = []
    todos: list[str] = []
    py = venv / "bin" / "python"
    want, why = desired_python(repo)
    if py.exists() and want and _venv_version(py) != want:
        notes.append(f"venv cũ dùng python {_venv_version(py)}, repo cần {want} ({why}) → tạo lại")
        shutil.rmtree(venv, ignore_errors=True)
    if not py.exists():
        base, how = find_python(want)
        if base is None:
            todos.append(f"repo cần python {want} ({why}) nhưng {how} — cài python{want} rồi chạy lại")
            return "python3", notes, todos
        proc = _run([base, "-m", "venv", str(venv)])
        if proc.returncode != 0:
            todos.append(f"không tạo được venv {venv} bằng {base}: {_tail(proc.stderr)} — cài python3-venv rồi chạy lại")
            return "python3", notes, todos
        notes.append(f"venv riêng cho repo tại {venv} — python {_venv_version(py)} ({how}; repo: {why})")
    pip = [str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check"]

    proc = _run(pip + ["pytest", "pytest-cov", "ruff"])
    if proc.returncode != 0:
        todos.append(f"không cài được pytest/pytest-cov/ruff vào venv: {_tail(proc.stderr)}")

    installed: list[str] = []
    if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
        extras = _extras(repo / "pyproject.toml")
        targets = ([f".[{','.join(extras)}]"] if extras else []) + ["."]
        for target in targets:
            proc = _run(pip + ["-e", target], cwd=repo)
            if proc.returncode == 0:
                installed.append(f"pip install -e {target}")
                break
        else:
            todos.append(f"pip install -e . thất bại: {_tail(proc.stderr)} — cài dependency tay vào {py}")
    reqs = sorted(repo.glob("requirements*.txt")) + sorted((repo / "requirements").glob("*.txt"))
    for req in reqs:
        proc = _run(pip + ["-r", str(req)], cwd=repo)
        if proc.returncode == 0:
            installed.append(f"pip install -r {req.relative_to(repo)}")
        else:
            todos.append(f"pip install -r {req.relative_to(repo)} thất bại: {_tail(proc.stderr)}")
    if (repo / "Pipfile").exists() and not reqs and not installed:
        todos.append(f"repo dùng Pipfile — cài tay: pipenv requirements > r.txt && {py} -m pip install -r r.txt")
    if installed:
        notes.append("đã cài dependency: " + "; ".join(installed))
    elif not todos:
        notes.append("repo không khai dependency (không có pyproject/setup.py/requirements*.txt)")

    # Thử gom test một lần: import hỏng, thiếu thư viện lộ ra ở đây, không phải ở Phase B.
    proc = _pytest_collect(str(py), repo)
    if proc.returncode == PYTEST_NO_TESTS:
        notes.append("pytest chưa gom được test nào — repo chưa có test pytest, baseline sẽ là 0")
    elif proc.returncode != 0:
        todos.append(f"pytest không gom được test trong venv: {_tail(proc.stdout + proc.stderr)}")
    else:
        count = [l for l in proc.stdout.splitlines() if "test" in l and "collected" in l or l.endswith("tests")]
        notes.append(f"pytest gom test OK trong venv{': ' + count[-1].strip() if count else ''}")
    return str(py), notes, todos


def generate(repo: Path, repo_id: str, tracker: dict | None = None, forge: dict | None = None,
             notify: dict | None = None, python: str = "python3", suggest=None) -> Draft:
    """Sinh hồ sơ nháp.

    `suggest(repo) -> (đề xuất | None, lý do)`: dùng cho repo không phải Python — thường là
    `agentprofile.suggest` gắn với một agent thật. Không truyền thì để TODO cho người.
    """
    repo = repo.resolve()
    notes: list[str] = []
    todos: list[str] = []
    if not is_python_repo(repo):
        return _generate_other(repo, repo_id, tracker, forge, notify, suggest)

    branch = base_branch(repo) or "main"
    if branch == "main" and not base_branch(repo):
        notes.append("base_branch: không đọc được từ remote, mặc định main")

    src_dirs = [d.name for d in sorted(repo.iterdir())
                if d.is_dir() and not d.name.startswith(".") and d.name not in _SKIP_DIRS
                and d.name not in _TEST_DIRS and _has_py(d)]
    test_dirs = [t for t in _TEST_DIRS if (repo / t).is_dir()]
    flat_py = any(p.suffix == ".py" for p in repo.iterdir() if p.is_file())

    if "src" in src_dirs:
        src_dirs = ["src"]
        notes.append("bố cục src/: allowed_paths chỉ gồm src/** và thư mục test")
    allowed = [f"{d}/**" for d in src_dirs] + [f"{t}/**" for t in test_dirs]
    if flat_py:
        allowed.append("*.py")
        notes.append("có file .py ở gốc repo → cho phép sửa *.py ở gốc")
    if not allowed:
        allowed = ["**/*.py", "tests/**"]
        todos.append("không nhận ra thư mục mã nguồn — sửa allowed_paths cho đúng")
    else:
        notes.append(f"allowed_paths suy từ thư mục có mã nguồn: {', '.join(src_dirs + test_dirs)}")

    forbidden = list(_ALWAYS_FORBIDDEN)
    for extra in ("migrations", "alembic"):
        if any(repo.rglob(extra)):
            forbidden.append(f"**/{extra}/**")
            notes.append(f"thấy thư mục {extra} → cấm sửa migration (bỏ dòng này nếu task cần)")

    test_globs = [f"{t}/**" for t in test_dirs] + ["**/test_*.py", "**/*_test.py"]

    commands: dict[str, str] = {}
    checks: dict[str, dict] = {
        "build": {"status": "out_of_scope", "reason": "Python thuần, không có bước build riêng"},
        "typecheck": {"status": "out_of_scope",
                      "reason": "không bật tự động: repo có lỗi mypy sẵn là baseline đỏ mãi"},
        "secret_scan": {"status": "out_of_scope", "reason": "chưa cài gitleaks trên máy chạy agent"},
        "sast": {"status": "out_of_scope", "reason": "chưa cài semgrep trên máy chạy agent"},
    }
    # Bố cục src/<gói>/ + `pip install -e .` trong venv chung: .pth trỏ vào src/ của BẢN
    # CLONE, nên `import gói` trong worktree của agent chạy code CŨ của clone chứ không
    # phải code agent vừa sửa. PYTHONPATH đứng trước .pth, và `src` tương đối với cwd
    # (chính worktree) — test chạy đúng code đang sửa.
    pypath = ("PYTHONPATH=src${PYTHONPATH:+:$PYTHONPATH} "
              if (repo / "src").is_dir() and not (repo / "src" / "__init__.py").exists() else "")
    if pypath:
        notes.append("bố cục src/<gói>: lệnh test/coverage đặt PYTHONPATH=src để test chạy code "
                     "trong worktree, không phải bản cài editable của clone")
    pytest_cmd = f"{pypath}{python} -m pytest -q --junitxml={{junit}} -o junit_family=xunit1"
    if _pytest_collect(python, repo).returncode == PYTEST_NO_TESTS:
        # Repo chưa có test pytest là trạng thái HỢP LỆ của pilot "agent viết
        # test" — nhưng baseline đọc "thoát khác 0 mà không gom được test nào"
        # là runner hỏng và dừng ERROR (enforce/baseline.py). Nuốt đúng mã 5
        # để baseline ghi 0 test; mã 1 (test đỏ) và 2 (gom hỏng) vẫn đỏ, nên
        # chốt chặn runner-hỏng không bị vô hiệu.
        commands["test"] = (f"{pytest_cmd}; c=$?; "
                            f"[ $c = {PYTEST_NO_TESTS} ] && exit 0; exit $c")
        stray = sorted({f.name for t in test_dirs
                        for f in (repo / t).rglob("*.py")
                        if f.name.startswith("test_") or f.name.endswith("_test.py")})
        if stray:
            notes.append(
                f"test: {', '.join(t + '/' for t in test_dirs)} có file test "
                f"({', '.join(stray[:3])}{'...' if len(stray) > 3 else ''}) nhưng pytest "
                "không gom được test nào — suite sẵn có không viết theo pytest (script chạy "
                "tay) nên KHÔNG vào baseline; lệnh test coi 'không gom được test nào' là 0 test")
        else:
            notes.append("test: repo chưa có test pytest → lệnh test coi 'không gom được test "
                         "nào' (pytest thoát 5) là baseline 0 test, mã lỗi khác vẫn đỏ")
    else:
        commands["test"] = pytest_cmd
    checks["test"] = {"status": "available"}
    if not _importable("pytest", python):
        todos.append(f"pytest chưa cài vào {python} — gate sẽ báo lệnh test không chạy được")
    compileall = f"{python} -m compileall -q {' '.join(src_dirs + test_dirs) or '.'}"
    has_ruff_cfg = ((repo / "ruff.toml").exists() or (repo / ".ruff.toml").exists()
                    or "[tool.ruff" in _read(repo / "pyproject.toml"))
    ruff_bin = f"{python} -m ruff" if _importable("ruff", python) else (
        "ruff" if shutil.which("ruff") else "")
    if has_ruff_cfg and ruff_bin:
        # Chỉ dùng ruff khi repo ĐANG sạch: lint đỏ sẵn là baseline đỏ ở mọi run.
        proc = _run(ruff_bin.split() + ["check", "-q", "."], cwd=repo, timeout=300)
        if proc.returncode == 0:
            commands["lint"] = f"{ruff_bin} check ."
            notes.append("lint: ruff (repo có cấu hình ruff và hiện đang sạch)")
        else:
            commands["lint"] = compileall
            notes.append("lint: repo có cấu hình ruff nhưng đang báo lỗi sẵn có → tạm dùng "
                         "compileall; dọn lint rồi đổi commands.lint thành ruff check .")
    else:
        commands["lint"] = compileall
        notes.append("lint: repo không cấu hình ruff → chỉ kiểm cú pháp bằng compileall")
    checks["lint"] = {"status": "available"}
    if _importable("pytest_cov", python) and src_dirs:
        cov_target = ",".join(src_dirs)
        commands["coverage"] = (f"{pypath}{python} -m pytest -q --cov={cov_target} "
                                f"--cov-report=json:{{coverage_json}}")
        checks["coverage"] = {"status": "available"}
        notes.append(f"coverage: bật vì có pytest-cov, đo trên {cov_target}")
    else:
        checks["coverage"] = {"status": "out_of_scope",
                              "reason": "chưa cài pytest-cov; cài rồi đổi thành available để G-7 chấm"}
    profile = _skeleton(repo_id, branch, allowed, forbidden, test_globs, commands, checks,
                        tracker, forge, notify, conventions={"ac_marker": "pytest.mark.ac"})
    _offline_note(tracker, notes)
    return Draft(profile=profile, notes=notes, todos=todos)


def _skeleton(repo_id, branch, allowed, forbidden, test_globs, commands, checks,
              tracker, forge, notify, conventions: dict) -> dict:
    return {
        "repo_id": repo_id, "base_branch": branch, "pilot_type": "infrastructure",
        "allowed_paths": allowed, "forbidden_paths": forbidden, "test_globs": test_globs,
        "commands": commands, "checks": {k: checks[k] for k in
                                         ("build", "test", "lint", "typecheck", "secret_scan", "coverage", "sast")},
        "limits": {"gate_fix_rounds": 2, "same_error_limit": 3, "flaky_rerun": 3,
                   "run_timeout_min": 60, "run_cost_cap_usd": 15, "cmd_timeout_sec": 900,
                   "coverage_new_line_pct": 80, "max_parallel": 1},
        # relaxed: ra MR (Draft khi thiếu bằng chứng test) thay vì NO_MR. auto_approve: loại
        # task được bỏ qua bước duyệt plan, vd [T1]. Xem README "Chế độ".
        "conventions": {"mode": "relaxed", "auto_approve": [], **conventions},
        "mutation": {"enabled": False, "max_mutants": 20, "timeout_sec": 300,
                     "per_mutant_timeout_sec": 120, "min_kill_pct": 70},
        "tracker": tracker or {"kind": "file", "root": "backlog"},
        "forge": forge or {"kind": "file", "root": "backlog"},
        "notify": notify or {"kind": "none"},
        "agent": {"binary": "claude", "permission_mode": "auto",
                  "allowed_tools": "Read,Write,Edit,Glob,Grep,Bash", "timeout_sec": 1800},
    }


def _offline_note(tracker: dict | None, notes: list[str]) -> None:
    if (tracker or {}).get("kind", "file") == "file":
        notes.append("tracker/forge trên đĩa (offline) vì .env chưa có backlog_space/backlog_project hoặc repo không phải URL GitLab")


def _generate_other(repo: Path, repo_id: str, tracker, forge, notify, suggest) -> Draft:
    """Repo không phải Python: agent đề xuất lệnh, hệ thống chạy lại để xác nhận."""
    from . import agentprofile
    notes: list[str] = []
    todos: list[str] = []
    branch = base_branch(repo) or "main"
    checks: dict[str, dict] = {
        "typecheck": {"status": "out_of_scope",
                      "reason": "không bật tự động — gộp vào lint/build nếu cần"},
        "secret_scan": {"status": "out_of_scope", "reason": "chưa cài gitleaks trên máy chạy agent"},
        "sast": {"status": "out_of_scope", "reason": "chưa cài semgrep trên máy chạy agent"},
        "coverage": {"status": "out_of_scope",
                     "reason": "G-7 chỉ đọc coverage.json của coverage.py — ngôn ngữ khác chưa đo"},
    }
    proposal, why = suggest(repo) if suggest else (None, "không có agent để soi repo")
    if proposal is None:
        todos.append(f"repo không phải Python và {why}: tự điền commands.test (ghi JUnit vào "
                     "{junit}), commands.lint, commands.setup, allowed_paths, test_globs")
        commands = {"test": "TODO lệnh test xuất JUnit XML vào {junit}",
                    "lint": "TODO lệnh lint, exit 0 khi sạch"}
        checks["build"] = {"status": "out_of_scope", "reason": "chưa khai"}
        allowed, test_globs, forbidden = ["src/**", "test/**"], ["test/**", "tests/**"], list(_ALWAYS_FORBIDDEN)
    else:
        commands = {k: proposal[k] for k in ("setup", "build", "test", "lint") if proposal.get(k)}
        checks["build"] = ({"status": "available"} if proposal.get("build") else
                           {"status": "out_of_scope", "reason": "agent không thấy bước build riêng"})
        allowed, test_globs = proposal["allowed_paths"], proposal["test_globs"]
        forbidden = list(dict.fromkeys(_ALWAYS_FORBIDDEN + proposal["extra_forbidden"]))
        notes.append(f"ngôn ngữ {proposal.get('language') or '?'}: lệnh setup/build/test/lint, "
                     "allowed_paths, test_globs do AGENT đề xuất sau khi soi repo — đọc lại trước khi chạy")
        notes += [f"agent: {n}" for n in proposal["notes"]]
        v_notes, v_todos = agentprofile.verify(repo, commands)
        notes += [f"đã chạy thử — {n}" for n in v_notes]
        todos += v_todos
    checks["test"] = {"status": "available"}
    checks["lint"] = {"status": "available"}
    notes.append("G-6 (assert rỗng) và mutation chỉ soi được Python — ở repo này ghi out_of_scope; "
                 "G-8 tìm mã AC-x ngay trong file test")
    profile = _skeleton(repo_id, branch, allowed, forbidden, test_globs, commands, checks,
                        tracker, forge, notify, conventions={})
    _offline_note(tracker, notes)
    return Draft(profile=profile, notes=notes, todos=todos)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
