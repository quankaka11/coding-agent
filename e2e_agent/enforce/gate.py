"""Gate — build, test, lint, typecheck, secret_scan, sast.

Coverage KHÔNG nằm ở đây: theo quyết định B3 nó là một check duy nhất ở G-7.
Check không chạy được thì ghi out_of_scope kèm lý do, không bao giờ ghi pass.

Test fail được phân loại TẤT ĐỊNH (skill e2e-agent, bước 8), agent không tự phân
loại lại:
  PRE_EXISTING      đã fail ở baseline → bỏ qua
  AGENT_INTRODUCED  mới, fail ở MỌI lần chạy lại → lỗi của agent, agent sửa
  FLAKY             mới, nhưng các lần chạy lại không nhất quán → giao người, không
                    quarantine/skip/xoá
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
            entry.update(_test_vs_baseline(ctx, prof, repo, junit_path, base))
        else:
            entry["output"] = _tail(res.stdout + res.stderr) if not res.ok else ""
        report["checks"][name] = entry
        ctx.emit("gate.check", check=name, status=entry["status"], exit_code=res.exit_code,
                 duration_ms=res.duration_ms, level="info" if entry["status"] == "pass" else "warn",
                 **{k: v for k, v in entry.items() if k in ("new_failures", "total", "flaky")})

    failed = [n for n, e in report["checks"].items() if e["status"] == "fail"]
    report["verdict"] = "FAIL" if failed else "PASS"
    report["failed_checks"] = failed
    report["flaky"] = list(report["checks"].get("test", {}).get("flaky") or [])
    (ctx.run_dir / "gate-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.emit("gate.verdict", verdict=report["verdict"], failed_checks=failed,
             flaky=report["flaky"][:5], head_sha=report["head_sha"][:8],
             level="info" if not failed else "warn")
    return report


def _test_vs_baseline(ctx: RunContext, prof: Profile, repo: Path, junit_path: Path,
                      base: Baseline | None) -> dict:
    """MUST PASS *so với baseline*: lỗi sẵn có không tính, lỗi mới thì tính.

    Lỗi mới được chạy lại `flaky_rerun - 1` lần nữa (chỉ khi có lỗi mới — suite
    xanh thì không tốn thêm). Fail ở mọi lần → AGENT_INTRODUCED; không nhất quán
    → FLAKY.
    """
    rep = parsers.junit(junit_path)
    known = set(base.failed_tests) if base else set()
    candidates = set(rep.failed) - known
    entry = {"total": rep.total, "failed": len(rep.failed),
             "pre_existing": sorted(set(rep.failed) & known)}

    introduced, flaky = sorted(candidates), []
    reruns = int(prof.limit("flaky_rerun")) - 1
    if candidates and reruns > 0:
        fail_sets = [candidates]
        for i in range(reruns):
            path = ctx.run_dir / f"gate-rerun-{i + 1}-junit.xml"
            ctx.cmd(prof.commands["test"].format(junit=str(path)), cwd=repo,
                    timeout=prof.limit("cmd_timeout_sec"))
            fail_sets.append(set(parsers.junit(path).failed) - known)
        always = set.intersection(*fail_sets)
        ever = set.union(*fail_sets)
        introduced, flaky = sorted(always), sorted(ever - always)
        ctx.emit("gate.rerun", reruns=reruns, agent_introduced=len(introduced), flaky=len(flaky))

    entry["new_failures"] = introduced
    entry["flaky"] = flaky
    entry["classification"] = {"PRE_EXISTING": entry["pre_existing"],
                               "AGENT_INTRODUCED": introduced, "FLAKY": flaky}
    entry["status"] = "fail" if introduced or flaky else "pass"
    entry["needs_human"] = bool(flaky)
    if entry["pre_existing"]:
        ctx.emit("classify", level="debug", verdict="PRE_EXISTING", tests=entry["pre_existing"][:10])
    if introduced:
        ctx.emit("classify", level="warn", verdict="AGENT_INTRODUCED", tests=introduced[:10])
    if flaky:
        ctx.emit("classify", level="warn", verdict="FLAKY", tests=flaky[:10])
    return entry


def _tail(text: str, lines: int = 40) -> str:
    rows = text.strip().splitlines()
    return "\n".join(rows[-lines:])


def assert_all_declared(prof: Profile) -> list[str]:
    """Check nào chưa khai trong hồ sơ repo — dùng cho doctor."""
    return [c for c in CHECKS if c not in prof.checks]
