"""Baseline — 4 nhánh theo quyết định B1."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core import gitutil, parsers
from ..config.profile import Profile
from ..core.reasons import Reason
from ..core.run import RunContext


@dataclass
class Baseline:
    base_sha: str
    runs: int
    build: str = "out_of_scope"
    lint: str = "out_of_scope"
    total: int = 0
    failed_tests: list[str] = field(default_factory=list)
    skipped: int = 0
    stable: bool = True
    coverage_pct: float | None = None

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> "Baseline":
        return Baseline(**json.loads(path.read_text(encoding="utf-8")))


def _run_check(ctx: RunContext, prof: Profile, repo: Path, name: str, **fmt: str) -> tuple[str, object]:
    if not prof.is_available(name):
        ctx.emit("gate.check", check=name, status="out_of_scope", reason=prof.reason(name))
        return "out_of_scope", None
    cmd = prof.commands[name].format(**fmt)
    res = ctx.cmd(cmd, cwd=repo, timeout=prof.limit("cmd_timeout_sec"))
    status = "pass" if res.ok else "fail"
    ctx.emit("gate.check", check=name, status=status, exit_code=res.exit_code,
             duration_ms=res.duration_ms, level="info" if res.ok else "warn")
    return status, res


def capture(ctx: RunContext, prof: Profile, repo: Path, scope: list[str] | None = None,
            strict: bool = True) -> Baseline:
    """Chụp baseline.

    strict=True (Phase B): baseline không dùng được thì decide() và thoát.
    strict=False (CI đối chứng): chỉ ghi nhận, vì ở đó baseline là dữ liệu để
    đối chiếu chứ không phải điều kiện để đi tiếp.
    """
    scope = scope or []
    base = Baseline(base_sha=gitutil.head_sha(repo), runs=int(prof.limit("flaky_rerun")))

    base.build, _ = _run_check(ctx, prof, repo, "build")
    if base.build == "fail":
        if strict:
            ctx.decide(Reason.NOMR_BASELINE_RED, "build fail trên code chưa sửa", check="build")
    base.lint, _ = _run_check(ctx, prof, repo, "lint")
    if base.lint == "fail":
        if strict:
            ctx.decide(Reason.NOMR_BASELINE_RED, "lint fail trên code chưa sửa", check="lint")

    seen: list[set[str]] = []
    report = parsers.TestReport()
    for attempt in range(base.runs):
        junit_path = ctx.run_dir / f"baseline-junit-{attempt}.xml"
        _run_check(ctx, prof, repo, "test", junit=str(junit_path))
        report = parsers.junit(junit_path)
        seen.append(set(report.failed))
        ctx.emit("baseline.attempt", attempt=attempt + 1, total=report.total,
                 failed=len(report.failed), skipped=len(report.skipped))

    base.total, base.skipped = report.total, len(report.skipped)
    base.stable = all(s == seen[0] for s in seen)
    base.failed_tests = sorted(seen[0])

    if not base.stable and strict:
        unstable = sorted(set.union(*seen) - set.intersection(*seen))
        ctx.decide(Reason.NOMR_BASELINE_RED,
                   "test fail không tái lập ổn định — không phân biệt được lỗi sẵn có với lỗi agent",
                   unstable_tests=unstable[:10])

    if base.failed_tests:
        if scope and not any(report.files.get(t) for t in base.failed_tests):
            ctx.emit("baseline.no_file_info", level="warn", scope=scope,
                     note="junit không cho biết file của test (vd node --test) nên không "
                          "kiểm được lỗi sẵn có có nằm trong phạm vi sắp sửa hay không")
        in_scope = [t for t in base.failed_tests
                    if _in_scope(report.files.get(t, ""), scope)]
        if in_scope and strict:
            ctx.decide(Reason.NOMR_BASELINE_RED,
                       "có lỗi sẵn có nằm trong chính phạm vi sắp sửa",
                       failed_in_scope=in_scope[:10], scope=scope)
        ctx.emit("baseline.pre_existing", level="warn", count=len(base.failed_tests),
                 tests=base.failed_tests[:10], note="ngoài scope nên vẫn chạy tiếp")

    base.save(ctx.run_dir / "baseline.json")
    ctx.emit("baseline.ready", total=base.total, failed=len(base.failed_tests),
             skipped=base.skipped, stable=base.stable, base_sha=base.base_sha[:8])
    return base


def _in_scope(test_file: str, scope: list[str]) -> bool:
    """So khớp theo tiền tố thư mục. Chấp nhận cả đường dẫn không đuôi file
    vì junit xunit2 chỉ cho biết module, không cho biết file."""
    if not test_file:
        return False
    stem = test_file.rsplit(".", 1)[0] if test_file.endswith(".py") else test_file
    for entry in scope:
        entry = entry.rstrip("/")
        entry_stem = entry.rsplit(".", 1)[0] if entry.endswith(".py") else entry
        if stem == entry_stem or stem.startswith(entry_stem + "/"):
            return True
    return False
