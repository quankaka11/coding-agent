"""Baseline — 4 nhánh theo quyết định B1."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core import gitutil, parsers
from ..config.profile import COVERAGE_FILE, Profile
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
    runner_broken = True
    for attempt in range(base.runs):
        junit_path = ctx.run_dir / f"baseline-junit-{attempt}.xml"
        status, _ = _run_check(ctx, prof, repo, "test", junit=str(junit_path))
        report = parsers.junit(junit_path)
        if not (status == "fail" and report.total == 0):
            runner_broken = False
        seen.append(set(report.failed))
        ctx.emit("baseline.attempt", attempt=attempt + 1, total=report.total,
                 failed=len(report.failed), skipped=len(report.skipped))

    # Lệnh test thoát khác 0 mà không test nào chạy: runner chưa khởi động được
    # (sai thư mục, thiếu thư viện, không gom được test). Đây KHÔNG phải baseline
    # đỏ — code chưa hề bị phán xét. Đi tiếp thì bước test-trước sẽ so 0 với 0 rồi
    # kết luận "không có bug", một kết luận sai xuất phát từ hạ tầng hỏng.
    if runner_broken and strict:
        ctx.decide(Reason.ERROR,
                   "lệnh test không chạy được trên code chưa sửa: thoát khác 0 nhưng không "
                   "gom được test nào — kiểm lại commands.test trong hồ sơ repo và thư mục chạy",
                   cwd=str(repo), command=prof.commands.get("test", ""))

    base.total, base.skipped = report.total, len(report.skipped)
    base.stable = all(s == seen[0] for s in seen)
    base.failed_tests = sorted(seen[0])

    if not base.stable and strict:
        unstable = sorted(set.union(*seen) - set.intersection(*seen))
        ctx.decide(Reason.HUMAN_FLAKY,
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

    base.coverage_pct = measure_coverage(ctx, prof, repo)
    base.save(ctx.run_dir / "baseline.json")
    ctx.emit("baseline.ready", total=base.total, failed=len(base.failed_tests),
             skipped=base.skipped, stable=base.stable, base_sha=base.base_sha[:8])
    return base


def measure_coverage(ctx: RunContext, prof: Profile, repo: Path) -> float | None:
    """Chạy lệnh coverage của hồ sơ và trả về % toàn repo.

    Không khai thì trả None và G-7 ghi out_of_scope — đúng luật "không chạy được
    thì nói là không chạy được, đừng ghi pass".
    """
    if not prof.is_available("coverage"):
        return None
    path = repo / COVERAGE_FILE
    cmd = prof.commands["coverage"].format(coverage_json=str(path),
                                           junit=str(ctx.run_dir / "coverage-junit.xml"))
    res = ctx.cmd(cmd, cwd=repo, timeout=prof.limit("cmd_timeout_sec"))
    cov = parsers.coverage_json(path)
    ctx.emit("coverage.measured", total_pct=cov.total_pct if cov else None,
             exit_code=res.exit_code, level="info" if cov else "warn")
    return cov.total_pct if cov else None


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
