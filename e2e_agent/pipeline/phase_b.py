"""Phase B — Baseline → Test trước → Implement → Gate → Anti-gaming → MR.

Chạy trên ticket chi tiết. KHÔNG đọc lại ticket gốc: cái người duyệt và cái agent
implement phải là một.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..agent import spec as spec_mod
from ..config.profile import Profile
from ..core import gitutil, parsers
from ..core.reasons import Reason
from ..core.run import RunContext
from ..enforce import antigaming, baseline as baseline_mod, gate as gate_mod, mutation
from ..forge.base import MR_LABEL
from ..tracker.base import Ticket
from . import phase_a, sandbox


def run(ctx: RunContext, prof: Profile, repo: Path, work: Path, ticket: Ticket,
        spec: spec_mod.Spec, plan: str, agent, forge) -> dict:
    ctx.phase = "B"
    evidence: dict = {}

    with ctx.stage("baseline"):
        base = baseline_mod.capture(ctx, prof, work, scope=spec.modules)

    with ctx.stage("test_first"):
        _ask(ctx, agent, work, "test_gen",
             phase_a._skill("test_gen") + phase_a._spec_block(spec)
             + f"\n## Convention\n\n{_head(plan)}\n")
        junit = ctx.run_dir / "testfirst-junit.xml"
        res = ctx.cmd(prof.commands["test"].format(junit=str(junit)), cwd=work,
                      timeout=prof.limit("cmd_timeout_sec"))
        report = parsers.junit(junit)
        new_failures = sorted(set(report.failed) - set(base.failed_tests))
        ctx.emit("testfirst.result", total=report.total, new_failures=new_failures[:10],
                 level="info" if new_failures else "warn")
        if not new_failures:
            ctx.decide(Reason.NOMR_TEST_PASSES_PRE,
                       "test viết trước đã pass trên code chưa sửa — không có gì để sửa",
                       total=report.total)
        evidence["fail_before_pass_after"] = {"failed_before": True, "tests": new_failures}
        sandbox.commit_all(work, f"test: {spec.objective[:60]}")

    with ctx.stage("implement"):
        gate_report = _implement_loop(ctx, prof, work, spec, plan, base, agent)
        evidence["fail_before_pass_after"]["passed_after"] = True

    if prof.mutation_cfg("enabled") and spec.task_type in ("T2", "T3"):
        with ctx.stage("mutation"):
            evidence["mutation"] = mutation.run(ctx, prof, work, base.base_sha)

    (ctx.run_dir / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    with ctx.stage("antigaming"):
        ag = antigaming.run(ctx, prof, work, base.base_sha, base, gate_report,
                            task_type=spec.task_type, acceptance_ids=spec.ac_ids)
    if ag["verdict"] == "FAIL":
        ctx.decide(Reason.HUMAN_ANTIGAMING,
                   f"vi phạm {', '.join(ag['failed_rules'])} — giữ nguyên hiện trường, không tự sửa",
                   failed_rules=ag["failed_rules"])

    with ctx.stage("create_mr"):
        mr = _create_mr(ctx, prof, work, ticket, spec, plan, base, gate_report, ag, forge)

    ctx.decide(Reason.OK, f"MR đã mở: {mr.url}", mr=mr.url)
    return {"mr": mr}


def _implement_loop(ctx: RunContext, prof: Profile, work: Path, spec, plan: str,
                    base, agent) -> dict:
    """Sửa → gate → sửa lại. Trần vòng lấy từ hồ sơ repo, không hardcode."""
    rounds = int(prof.limit("gate_fix_rounds"))
    same_error_limit = int(prof.limit("same_error_limit"))
    seen_errors: list[str] = []
    feedback = ""

    for attempt in range(rounds + 1):
        prompt = (phase_a._skill("implement") + phase_a._spec_block(spec)
                  + f"\n## Plan đã được duyệt\n\n{plan}\n"
                  + (f"\n## Gate vừa fail, sửa tiếp\n\n{feedback}\n" if feedback else ""))
        _ask(ctx, agent, work, f"implement-{attempt + 1}", prompt)
        sandbox.commit_all(work, f"fix: {spec.objective[:60]}"
                           if attempt == 0 else f"fix: vòng {attempt + 1}")

        gate_report = gate_mod.run(ctx, prof, work, base)
        if gate_report["verdict"] == "PASS":
            ctx.emit("implement.done", rounds=attempt + 1)
            return gate_report

        signature = ",".join(sorted(gate_report["failed_checks"])) + "|" + ",".join(
            sorted(gate_report["checks"].get("test", {}).get("new_failures", []))[:5])
        seen_errors.append(signature)
        if seen_errors.count(signature) >= same_error_limit:
            ctx.decide(Reason.NOMR_LOOP_LIMIT,
                       f"cùng một lỗi lặp {same_error_limit} lần, dừng để khỏi đốt token",
                       signature=signature[:200])
        feedback = _feedback(gate_report)
        ctx.emit("implement.retry", level="warn", attempt=attempt + 1,
                 failed_checks=gate_report["failed_checks"])

    ctx.decide(Reason.NOMR_GATE_FAIL, f"gate vẫn fail sau {rounds} vòng tự sửa",
               failed_checks=gate_report["failed_checks"])


def _feedback(gate_report: dict) -> str:
    lines = []
    for name, entry in gate_report["checks"].items():
        if entry["status"] == "fail":
            lines.append(f"- `{name}` fail (exit {entry.get('exit_code')})")
            for test in entry.get("new_failures", [])[:10]:
                lines.append(f"  - test fail: {test}")
    return "\n".join(lines)


def _create_mr(ctx, prof, work, ticket, spec, plan, base, gate_report, ag, forge):
    branch = f"agent/{ticket.id}"
    head = gitutil.head_sha(work)
    body = _mr_body(ticket, spec, plan, base, gate_report, ag, head, ctx)
    (ctx.run_dir / "mr-body.md").write_text(body, encoding="utf-8")
    forge.push_branch(work, branch)
    mr = forge.create_mr(branch, prof.base_branch, f"[{ticket.id}] {spec.objective[:70]}",
                           body, [MR_LABEL])
    ctx.emit("mr.created", url=mr.url, branch=branch, head_sha=head[:8])
    return mr


def _mr_body(ticket, spec, plan, base, gate_report, ag, head, ctx) -> str:
    checks = "\n".join(
        f"| {name} | {entry['status']} | {entry.get('reason', '')} |"
        for name, entry in gate_report["checks"].items())
    rules = "\n".join(
        f"| {rule} | {data['status']} | {data['why']} |"
        for rule, data in ag["rules"].items())
    acs = "\n".join(f"- **{ac['id']}**: {ac['text']}" for ac in spec.acceptance_criteria)
    return f"""## Vấn đề và cách giải

{spec.objective}

{_head(plan, 40)}

## Acceptance criteria

{acs}

## Bằng chứng gate

| Check | Kết quả | Ghi chú |
|---|---|---|
{checks}

Baseline: {base.total} test, {len(base.failed_tests)} lỗi sẵn có, ổn định {base.runs}/{base.runs} lần.

## Anti-gaming

| Luật | Kết quả | Ghi chú |
|---|---|---|
{rules}

## Truy vết

- Ticket chi tiết: {ticket.id}
- Ticket gốc: {ticket.parent or "—"}
- Commit cuối: `{head[:12]}`
- Run: `{ctx.run_id}` — log đầy đủ trong `{ctx.run_dir}/events.jsonl`

---
*MR do agent tạo. Gate và anti-gaming là script tất định, CI chạy lại toàn bộ trên MR này.*
"""


def _ask(ctx, agent, cwd, name, prompt):
    result = agent.run(ctx, prompt, cwd, name)
    if not result.ok:
        ctx.decide(Reason.ERROR, f"agent {name} lỗi: {result.error}", agent=name)
    return result


def _head(text: str, lines: int = 25) -> str:
    rows = text.strip().splitlines()
    return "\n".join(rows[:lines]) + ("\n…" if len(rows) > lines else "")
