"""Hồ sơ repo — file DUY NHẤT chứa tri thức về một dự án cụ thể (nguyên tắc N-1)."""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CHECKS = ("build", "test", "lint", "typecheck", "secret_scan", "sast")
#: Khai được nhưng không bắt buộc khai — thiếu thì luật dùng tới nó ghi out_of_scope.
OPTIONAL_CHECKS = ("coverage",)
ALL_CHECKS = CHECKS + OPTIONAL_CHECKS
STATUSES = ("available", "out_of_scope")
#: Nơi lệnh coverage phải ghi kết quả, và nơi G-7 đọc. Một quy ước duy nhất.
COVERAGE_FILE = "coverage.json"
_TOP = {"repo_id", "base_branch", "pilot_type", "allowed_paths", "forbidden_paths",
        "test_globs", "commands", "checks", "limits", "conventions", "mutation",
        "tracker", "forge", "agent", "notify"}

DEFAULT_LIMITS = {
    "gate_fix_rounds": 2, "same_error_limit": 3,
    "flaky_rerun": 3, "run_timeout_min": 60, "run_cost_cap_usd": 15,
    "cmd_timeout_sec": 900, "coverage_new_line_pct": 80,
    #: Ticket ở `agent:running` quá số phút này mà không chốt kết cục thì coi là
    #: tiến trình đã chết và giao cho người. None = gấp đôi run_timeout_min.
    "running_stale_min": None,
}
#: `timeout_sec` là trần cho CẢ bước mutation; `per_mutant_timeout_sec` là trần cho
#: một lần chạy test. Dùng chung một số thì một mutant chậm nuốt trọn ngân sách.
DEFAULT_MUTATION = {"enabled": False, "max_mutants": 20, "timeout_sec": 300,
                    "per_mutant_timeout_sec": 120, "min_kill_pct": 70}
DEFAULT_TRACKER = {"kind": "file", "root": "backlog", "url": "", "project": "",
                   "token_env": "GITLAB_TOKEN", "space": "", "api_key_env": "BACKLOG_API_KEY",
                   "issue_type_id": None}
DEFAULT_FORGE = {"kind": "file", "root": "backlog", "url": "", "project": "",
                 "token_env": "GITLAB_TOKEN"}
DEFAULT_NOTIFY = {"kind": "none", "url_env": "NOTIFY_WEBHOOK_URL", "events": None}
DEFAULT_AGENT = {"binary": "claude", "model": None, "permission_mode": "acceptEdits",
                 "allowed_tools": "Read,Write,Edit,Glob,Grep,Bash", "timeout_sec": 1800}


class ProfileError(ValueError):
    """Hồ sơ repo sai — dừng ngay, không đoán."""


@dataclass
class Profile:
    repo_id: str
    base_branch: str = "main"
    pilot_type: str = "infrastructure"
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    test_globs: list[str] = field(default_factory=lambda: ["tests/**", "**/test_*.py", "**/*_test.py"])
    commands: dict[str, str] = field(default_factory=dict)
    checks: dict[str, dict] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    conventions: dict[str, Any] = field(default_factory=dict)
    mutation: dict[str, Any] = field(default_factory=dict)
    tracker: dict[str, Any] = field(default_factory=dict)
    forge: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)
    notify: dict[str, Any] = field(default_factory=dict)
    source: Path | None = None

    # -- truy vấn --------------------------------------------------------
    def limit(self, name: str) -> Any:
        return self.limits.get(name, DEFAULT_LIMITS[name])

    def status(self, check: str) -> str:
        return self.checks.get(check, {}).get("status", "out_of_scope")

    def reason(self, check: str) -> str:
        return self.checks.get(check, {}).get("reason", "chưa khai trong hồ sơ repo")

    def is_available(self, check: str) -> bool:
        return self.status(check) == "available"

    def mutation_cfg(self, key: str) -> Any:
        return self.mutation.get(key, DEFAULT_MUTATION[key])

    def tracker_cfg(self, key: str) -> Any:
        return self.tracker.get(key, DEFAULT_TRACKER[key])

    def forge_cfg(self, key: str) -> Any:
        return self.forge.get(key, DEFAULT_FORGE[key])

    def agent_cfg(self, key: str) -> Any:
        return self.agent.get(key, DEFAULT_AGENT[key])

    def notify_cfg(self, key: str) -> Any:
        return self.notify.get(key, DEFAULT_NOTIFY[key])


def load(path: str | Path) -> Profile:
    path = Path(path)
    if not path.is_file():
        raise ProfileError(f"không thấy hồ sơ repo: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ProfileError(f"{path}: nội dung phải là một mapping YAML")
    if unknown := set(raw) - _TOP:
        raise ProfileError(f"{path}: khoá lạ {sorted(unknown)} — có thể gõ nhầm")
    if not raw.get("repo_id"):
        raise ProfileError(f"{path}: thiếu repo_id")
    prof = Profile(**{k: v for k, v in raw.items() if k in _TOP}, source=path)
    _validate(prof)
    return prof


def _validate(p: Profile) -> None:
    errs: list[str] = []
    for name, spec in p.checks.items():
        if name not in ALL_CHECKS:
            errs.append(f"check lạ: {name}")
        elif not isinstance(spec, dict) or spec.get("status") not in STATUSES:
            errs.append(f"check {name}: status phải là một trong {STATUSES}")
        elif spec["status"] == "out_of_scope" and not spec.get("reason"):
            errs.append(f"check {name}: out_of_scope bắt buộc kèm reason")
        elif spec["status"] == "available" and name not in p.commands:
            errs.append(f"check {name}: khai available nhưng thiếu commands.{name}")
    for must in ("test", "lint"):
        if p.status(must) != "available":
            errs.append(f"check {must} bắt buộc available (MUST PASS theo mục 6)")
    # build được phép out_of_scope (ngôn ngữ thông dịch không có bước build),
    # nhưng phải khai lý do — im lặng bỏ qua thì không.
    if "build" not in p.checks:
        errs.append("check build phải khai rõ available hoặc out_of_scope kèm lý do")
    if unknown := set(p.limits) - set(DEFAULT_LIMITS):
        errs.append(f"limits có khoá lạ {sorted(unknown)}")
    if unknown := set(p.tracker) - set(DEFAULT_TRACKER):
        errs.append(f"tracker có khoá lạ {sorted(unknown)}")
    if unknown := set(p.forge) - set(DEFAULT_FORGE):
        errs.append(f"forge có khoá lạ {sorted(unknown)}")
    if unknown := set(p.agent) - set(DEFAULT_AGENT):
        errs.append(f"agent có khoá lạ {sorted(unknown)}")
    if unknown := set(p.notify) - set(DEFAULT_NOTIFY):
        errs.append(f"notify có khoá lạ {sorted(unknown)}")
    if p.notify_cfg("kind") not in ("none", "webhook"):
        errs.append("notify.kind phải là none hoặc webhook")
    if p.tracker_cfg("kind") not in ("file", "gitlab", "backlog"):
        errs.append("tracker.kind phải là file, gitlab hoặc backlog")
    if p.tracker_cfg("kind") == "gitlab" and not (p.tracker_cfg("url") and p.tracker_cfg("project")):
        errs.append("tracker gitlab cần cả url và project")
    if p.tracker_cfg("kind") == "backlog" and not (p.tracker_cfg("space") and p.tracker_cfg("project")):
        errs.append("tracker backlog cần cả space và project (projectKey hoặc id)")
    if p.forge_cfg("kind") not in ("file", "gitlab"):
        errs.append("forge.kind phải là file hoặc gitlab")
    if p.forge_cfg("kind") == "gitlab" and not (p.forge_cfg("url") and p.forge_cfg("project")):
        errs.append("forge gitlab cần cả url và project")
    if not p.forbidden_paths:
        errs.append("forbidden_paths rỗng — ít nhất phải cấm file cấu hình CI")
    if errs:
        raise ProfileError(f"{p.source}:\n  - " + "\n  - ".join(errs))


def doctor(p: Profile, repo: Path, package_root: Path | None = None) -> list[tuple[str, bool, str]]:
    """Soát hồ sơ với repo thật. Trả về [(mục, đạt, ghi chú)] — không raise."""
    out: list[tuple[str, bool, str]] = []
    out.append(("repo là git repo", (repo / ".git").exists(), str(repo)))

    from ..pipeline.sandbox import hook_importable
    ok = hook_importable(package_root)
    out.append(("hook chặn file cấm import được", ok,
                "sẽ chặn thật" if ok else
                "interpreter chạy hook không import được e2e_agent — hook sẽ thoát mã 1 "
                "và KHÔNG chặn gì cả; cài gói hoặc chạy e2ea từ thư mục chứa gói"))

    for pattern in p.allowed_paths:
        hits = list(repo.glob(pattern))
        out.append((f"allowed_paths: {pattern}", bool(hits),
                    f"khớp {len(hits)} mục" if hits else "không khớp gì — sai đường dẫn?"))
    for pattern in p.forbidden_paths:
        hits = list(repo.glob(pattern))
        # không khớp gì là bình thường: repo có thể không có file đó
        out.append((f"forbidden_paths: {pattern}", True, f"khớp {len(hits)} mục"))

    for name in CHECKS:
        status = p.status(name)
        if status == "out_of_scope":
            out.append((f"check {name}", True, f"out_of_scope — {p.reason(name)}"))
            continue
        cmd = p.commands.get(name, "")
        tokens = cmd.split()
        env_assign = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
        while tokens and env_assign.match(tokens[0]):
            tokens.pop(0)
        tool = tokens[0] if tokens else ""
        found = bool(tool) and (shutil.which(tool) is not None or tool in ("python", "python3"))
        out.append((f"check {name}", found, cmd if found else f"không thấy lệnh {tool!r} trong PATH"))

    kind = p.notify_cfg("kind")
    if kind == "none":
        out.append(("notify", True, "tắt — plan sẵn sàng sẽ không có ai được báo"))
    else:
        from ..core.secrets import read_secret
        ok = bool(read_secret(p.notify_cfg("url_env")))
        out.append((f"notify {kind}", ok,
                    f"đọc từ {p.notify_cfg('url_env')}" if ok
                    else f"không thấy {p.notify_cfg('url_env')} trong env hoặc .env"))

    if "{junit}" not in p.commands.get("test", ""):
        out.append(("commands.test có {junit}", False,
                    "thiếu placeholder {junit} — không phân loại được test fail theo tên"))
    else:
        out.append(("commands.test có {junit}", True, "ok"))
    if p.is_available("coverage"):
        out.append(("commands.coverage có {coverage_json}",
                    "{coverage_json}" in p.commands.get("coverage", ""),
                    p.commands.get("coverage", "chưa khai commands.coverage")))
    else:
        out.append(("check coverage", True,
                    f"out_of_scope — {p.reason('coverage')}; G-7 sẽ không chấm"))
    return out


# -- so khớp glob (dùng chung cho G-3 và hook) -----------------------------
import re as _re  # noqa: E402

_GLOB_CACHE: dict[str, "_re.Pattern[str]"] = {}


def _compile_glob(pattern: str) -> "_re.Pattern[str]":
    if pattern not in _GLOB_CACHE:
        out, i = "", 0
        while i < len(pattern):
            ch = pattern[i]
            if pattern.startswith("**/", i):
                out, i = out + "(?:.*/)?", i + 3
            elif pattern.startswith("**", i):
                out, i = out + ".*", i + 2
            elif ch == "*":
                out, i = out + "[^/]*", i + 1
            elif ch == "?":
                out, i = out + "[^/]", i + 1
            else:
                out, i = out + _re.escape(ch), i + 1
        _GLOB_CACHE[pattern] = _re.compile("^" + out + "$")
    return _GLOB_CACHE[pattern]


def matches(path: str, patterns: list[str]) -> str | None:
    """Trả về pattern đầu tiên khớp, hoặc None. So trên đường dẫn đã resolve,
    không so trên chuỗi glob (yêu cầu của G-3)."""
    path = path.removeprefix("./")  # KHÔNG dùng lstrip: nó ăn mất dấu chấm của .gitlab-ci.yml
    for pattern in patterns:
        if _compile_glob(pattern).match(path):
            return pattern
        # thư mục cấm thì mọi thứ bên trong cũng cấm
        if pattern.endswith("/**") and _compile_glob(pattern[:-3]).match(path):
            return pattern
    return None
