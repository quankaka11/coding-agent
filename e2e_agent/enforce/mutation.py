"""Mutation check có giới hạn (quyết định C1).

Chỉ đột biến các dòng MỚI của code nguồn, trần số mutant và trần thời gian.
Đây là cách duy nhất chứng minh test không rỗng khi task là T2 (test bù) —
lúc đó không có bug thật nên không có bằng chứng "fail trước pass sau".
"""
from __future__ import annotations

import time
from pathlib import Path

from ..core import gitutil, parsers
from . import astcheck
from ..config.profile import Profile, matches
from ..core.run import RunContext

KILLED, SURVIVED = "killed", "survived"


def run(ctx: RunContext, prof: Profile, repo: Path, base_sha: str,
        targets: dict[str, set[int]] | None = None) -> dict:
    if not prof.mutation_cfg("enabled"):
        ctx.emit("mutation.skip", reason="mutation.enabled = false trong hồ sơ repo")
        return {"status": "out_of_scope", "reason": "tắt trong hồ sơ repo"}

    limit = int(prof.mutation_cfg("max_mutants"))
    budget = float(prof.mutation_cfg("timeout_sec"))
    min_kill = float(prof.mutation_cfg("min_kill_pct"))

    if targets is None:
        targets = {f: lines for f, lines in gitutil.new_lines(repo, base_sha).items()
                   if f.endswith(".py") and not matches(f, prof.test_globs)}
    if not targets:
        ctx.emit("mutation.skip", reason="không có dòng nguồn nào để đột biến")
        return {"status": "out_of_scope", "reason": "không có dòng nguồn để đột biến"}

    started = time.monotonic()
    results: list[dict] = []
    for rel, lines in targets.items():
        path = repo / rel
        original = astcheck.read(path)
        for desc, mutated in astcheck.mutants(original, lines, limit - len(results)):
            if len(results) >= limit or time.monotonic() - started > budget:
                break
            verdict = _try_mutant(ctx, prof, repo, path, original, mutated)
            results.append({"file": rel, "mutation": desc, "verdict": verdict})
            ctx.emit("mutation.mutant", level="debug", file=rel, mutation=desc, verdict=verdict)
        if len(results) >= limit or time.monotonic() - started > budget:
            break

    killed = sum(1 for r in results if r["verdict"] == KILLED)
    pct = round(killed * 100 / len(results), 1) if results else 0.0
    status = "pass" if results and pct >= min_kill else "fail" if results else "out_of_scope"
    out = {"status": status, "kill_pct": pct, "killed": killed, "total": len(results),
           "min_kill_pct": min_kill, "mutants": results,
           "elapsed_sec": round(time.monotonic() - started, 1)}
    ctx.emit("mutation.verdict", status=status, kill_pct=pct, killed=killed,
             total=len(results), level="info" if status != "fail" else "warn")
    return out


def covered_lines(prof: Profile, coverage_json: Path, modules: list[str]) -> dict[str, set[int]]:
    """Dòng nguồn trong phạm vi mà test đang chạy qua — mục tiêu đột biến cho T2.

    T2 không sửa dòng code nào, nên "dòng mới" là tập rỗng và cách chọn mục tiêu
    của T1/T3 không dùng được. Thứ cần thử thách ở đây là chính đoạn code cũ mà
    test bù vừa tuyên bố là đang kiểm: đột biến nó mà test vẫn xanh thì lời tuyên
    bố đó rỗng.
    """
    cov = parsers.coverage_json(coverage_json)
    if cov is None:
        return {}
    scope = [m.rstrip("/") for m in modules]
    out: dict[str, set[int]] = {}
    for rel, lines in cov.executed.items():
        if not rel.endswith(".py") or matches(rel, prof.test_globs) or not lines:
            continue
        if scope and not any(rel == m or rel.startswith(m + "/") for m in scope):
            continue
        out[rel] = set(lines)
    return out


def _try_mutant(ctx: RunContext, prof: Profile, repo: Path, path: Path,
                original: str, mutated: str) -> str:
    """Ghi mutant, chạy test, LUÔN trả lại nguyên trạng."""
    try:
        path.write_text(mutated, encoding="utf-8")
        cmd = prof.commands["test"].format(junit=str(ctx.run_dir / "mutation-junit.xml"))
        res = ctx.cmd(cmd, cwd=repo,
                      timeout=int(prof.mutation_cfg("per_mutant_timeout_sec")))
        return KILLED if not res.ok else SURVIVED
    finally:
        path.write_text(original, encoding="utf-8")
