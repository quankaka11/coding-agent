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
        spec: spec_mod.Spec, plan: str, agent, forge, branch: str) -> dict:
    ctx.phase = "B"
    evidence: dict = {}
    is_t2 = spec.task_type == "T2"

    # T2 là "viết test cho vùng chưa có coverage": code đang đúng nên không có
    # bằng chứng fail-trước nào cả, và mutation là lưới duy nhất chặn test rỗng
    # (chốt C1). Chạy T2 với mutation tắt là chạy không lưới — nói ra ngay, trước
    # khi tiêu tiền cho agent.
    if is_t2:
        thieu = []
        if not prof.mutation_cfg("enabled"):
            thieu.append("mutation.enabled: true")
        if not prof.is_available("coverage"):
            thieu.append("checks.coverage: available + commands.coverage")
        if thieu:
            ctx.decide(Reason.HUMAN_PROFILE_GAP,
                       "task T2 chỉ kết luận được nhờ coverage và mutation, hồ sơ repo "
                       f"còn thiếu: {', '.join(thieu)}",
                       task_type=spec.task_type, missing=thieu)

    with ctx.stage("baseline"):
        base = baseline_mod.capture(ctx, prof, work, scope=spec.modules)

    with ctx.stage("test_first"):
        # Plan vào NGUYÊN VĂN. Bản cắt 25 dòng từng xén mất chính mục "Test sẽ
        # viết" của những plan chi tiết nhất, nên agent viết test không nhìn thấy
        # trọn thứ người đã duyệt — người duyệt một đằng, agent làm một nẻo.
        _ask(ctx, agent, work, "test_gen",
             phase_a._skill("test_gen") + phase_a._spec_block(spec)
             + f"\n## Plan đã được duyệt\n\n{plan}\n")
        ctx.emit("plan.handed", to="test_gen", lines=len(plan.splitlines()))
        junit = ctx.run_dir / "testfirst-junit.xml"
        res = ctx.cmd(prof.commands["test"].format(junit=str(junit)), cwd=work,
                      timeout=prof.limit("cmd_timeout_sec"))
        report = parsers.junit(junit)
        # File test vừa viết không chạy được (sai import, sai cú pháp) thì cả bước
        # thu thập đổ, và những gì đọc được sau đó không nói gì về code cả. Phải
        # tách khỏi NOMR_GATE_FAIL: ở đó lỗi thuộc về bước sửa code, còn ở đây lỗi
        # thuộc về bước viết test — gộp lại là đổ lỗi sai chỗ trong mọi thống kê.
        new_errors = sorted(set(report.errored) - set(base.failed_tests))
        if new_errors or report.total == 0:
            ctx.decide(Reason.NOMR_TESTGEN_BROKEN,
                       "test vừa viết không chạy được nên chưa phán xét gì được về code"
                       + (f": {', '.join(new_errors[:3])}" if new_errors
                          else " — không thu được test nào"),
                       errors=new_errors[:10], total=report.total,
                       baseline_total=base.total, exit_code=res.exit_code)
        new_failures = sorted(set(report.failed) - set(base.failed_tests))
        ctx.emit("testfirst.result", total=report.total, new_failures=new_failures[:10],
                 level="info" if new_failures else "warn")
        if is_t2 and new_failures:
            ctx.decide(Reason.HUMAN_T2_FOUND_BUG,
                       "task khai là T2 nhưng test bù lại đỏ ngay trên code chưa sửa",
                       tests=new_failures[:10])
        if not is_t2 and not new_failures:
            ctx.decide(Reason.NOMR_TEST_PASSES_PRE,
                       "test viết trước đã pass trên code chưa sửa — không có gì để sửa",
                       total=report.total)
        if not is_t2:
            evidence["fail_before_pass_after"] = {"failed_before": True, "tests": new_failures}
        # Mốc để G-9 so: từ đây trở đi test là hợp đồng, không ai được sửa.
        testfirst_sha = sandbox.commit_all(work, f"test: {spec.objective[:60]}")
        evidence["test_freeze"] = {"testfirst_sha": testfirst_sha}
        ctx.emit("testfirst.frozen", sha=testfirst_sha[:8],
                 note="từ mốc này G-9 cấm mọi thay đổi trong file test")

    if is_t2:
        # Không có gì để sửa — chỉ cần chắc rằng test mới không làm hỏng gì.
        with ctx.stage("gate"):
            gate_report = gate_mod.run(ctx, prof, work, base)
        if gate_report["verdict"] == "FAIL":
            ctx.decide(Reason.NOMR_GATE_FAIL, "gate fail ngay sau khi thêm test bù",
                       failed_checks=gate_report["failed_checks"])
    else:
        with ctx.stage("implement"):
            gate_report = _implement_loop(ctx, prof, work, spec, plan, base, agent)
            evidence["fail_before_pass_after"]["passed_after"] = True

    if prof.is_available("coverage"):
        with ctx.stage("coverage"):
            # Đo trên đúng cây code sắp đem đi mở MR; G-7 đọc lại file này.
            baseline_mod.measure_coverage(ctx, prof, work)

    if prof.mutation_cfg("enabled") and spec.task_type in ("T2", "T3"):
        with ctx.stage("mutation"):
            targets = mutation.covered_lines(prof, work, spec.modules) if is_t2 else None
            evidence["mutation"] = mutation.run(ctx, prof, work, base.base_sha, targets)

    (ctx.run_dir / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    with ctx.stage("antigaming"):
        ag = antigaming.run(ctx, prof, work, base.base_sha, base, gate_report,
                            task_type=spec.task_type, acceptance_ids=spec.ac_ids,
                            modules=spec.modules)
    if ag["verdict"] == "FAIL":
        ctx.decide(Reason.HUMAN_ANTIGAMING,
                   f"vi phạm {', '.join(ag['failed_rules'])} — giữ nguyên hiện trường, không tự sửa",
                   failed_rules=ag["failed_rules"])

    with ctx.stage("create_mr"):
        mr = _create_mr(ctx, prof, work, ticket, spec, plan, base, gate_report, ag,
                        forge, branch)

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


def _create_mr(ctx, prof, work, ticket, spec, plan, base, gate_report, ag, forge, branch):
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

{plan}

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
