"""Gate — build, test, lint, typecheck, secret_scan, sast.

Coverage KHÔNG nằm ở đây: theo quyết định B3 nó là một check duy nhất ở G-7.
Check không chạy được thì ghi out_of_scope kèm lý do, không bao giờ ghi pass.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..core import gitutil, parsers
from .baseline import Baseline
from ..config.profile import CHECKS, Profile
from ..core.run import RunContext

GATE_ORDER = ("build", "lint", "test", "typecheck", "secret_scan", "sast")


def run(ctx: RunContext, prof: Profile, repo: Path, base: Baseline | None = None) -> dict:
    """Chạy toàn bộ gate, trả về gate report (đã ghi ra run_dir)."""
    report: dict = {
        "head_sha": gitutil.head_sha(repo),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dirty": gitutil.is_dirty(repo),
        "checks": {},
    }
    junit_path = ctx.run_dir / "gate-junit.xml"

    for name in GATE_ORDER:
        if not prof.is_available(name):
            report["checks"][name] = {"status": "out_of_scope", "reason": prof.reason(name)}
            ctx.emit("gate.check", check=name, status="out_of_scope", reason=prof.reason(name))
            continue
        cmd = prof.commands[name].format(junit=str(junit_path))
        res = ctx.cmd(cmd, cwd=repo, timeout=prof.limit("cmd_timeout_sec"))
        entry: dict = {"status": "pass" if res.ok else "fail", "exit_code": res.exit_code}
        if name == "test":
            entry.update(_test_vs_baseline(ctx, junit_path, base))
        report["checks"][name] = entry
        ctx.emit("gate.check", check=name, status=entry["status"], exit_code=res.exit_code,
                 duration_ms=res.duration_ms, level="info" if entry["status"] == "pass" else "warn",
                 **{k: v for k, v in entry.items() if k in ("new_failures", "total")})

    failed = [n for n, e in report["checks"].items() if e["status"] == "fail"]
    report["verdict"] = "FAIL" if failed else "PASS"
    report["failed_checks"] = failed
    (ctx.run_dir / "gate-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.emit("gate.verdict", verdict=report["verdict"], failed_checks=failed,
             head_sha=report["head_sha"][:8], level="info" if not failed else "warn")
    return report


def _test_vs_baseline(ctx: RunContext, junit_path: Path, base: Baseline | None) -> dict:
    """MUST PASS *so với baseline*: lỗi sẵn có không tính, lỗi mới thì tính."""
    rep = parsers.junit(junit_path)
    known = set(base.failed_tests) if base else set()
    new_failures = sorted(set(rep.failed) - known)
    entry = {"total": rep.total, "failed": len(rep.failed),
             "new_failures": new_failures, "pre_existing": sorted(set(rep.failed) & known)}
    entry["status"] = "fail" if new_failures else "pass"
    if entry["pre_existing"]:
        ctx.emit("classify", level="debug", verdict="PRE_EXISTING", tests=entry["pre_existing"][:10])
    if new_failures:
        ctx.emit("classify", level="warn", verdict="AGENT_INTRODUCED", tests=new_failures[:10])
    return entry


def assert_all_declared(prof: Profile) -> list[str]:
    """Check nào chưa khai trong hồ sơ repo — dùng cho doctor."""
    return [c for c in CHECKS if c not in prof.checks]
