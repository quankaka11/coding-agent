"""Phase B — Baseline → (sửa lint sẵn) → Test trước → Implement → Gate → Anti-gaming
→ (agent tự sửa vi phạm) → MR.

Chạy trên ticket chi tiết. KHÔNG đọc lại ticket gốc: cái người duyệt và cái agent
implement phải là một.

Quy tắc áp từ skill nội bộ e2e-agent, tóm lại:
  bước 5  lint đỏ sẵn → agent sửa bằng commit riêng, không NO_MR; test đỏ trong
          phạm vi → NO_MR.
  bước 6  agent viết test chỉ nhận AC + mục "Test sẽ viết" của plan, KHÔNG nhận
          cách giải — test nhìn thấy cách sửa sẽ chép đúng cái bug của cách sửa.
  bước 8  test fail phân loại tất định: PRE_EXISTING bỏ qua, AGENT_INTRODUCED agent
          sửa, FLAKY giao người.
  bước 9  anti-gaming fail mà sửa được → agent sửa, gate + anti-gaming lại (có
          trần vòng); needs_review → ghi vào MR; chỉ bằng chứng hỏng mới giao người.

Chế độ relaxed (mặc định, `conventions.mode`): mục tiêu là ra MR. Không có bằng chứng
fail-trước (T4, test không viết được, test pass sẵn) thì vẫn implement, gate vẫn phải
xanh, luật mềm hết vòng thì hạ xuống needs_review — và MR mở dạng **Draft** kèm lý do
trong `evidence["draft_reasons"]`. Luật an toàn (G-1/G-2/G-3/G-5/G-9) không bao giờ hạ.
"""
from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from ..agent import spec as spec_mod
from ..config.profile import Profile, matches
from ..core import gitutil, parsers
from ..core.reasons import Kind, Reason
from ..core.run import RunContext
from ..enforce import antigaming, baseline as baseline_mod, gate as gate_mod, mutation
from ..forge.base import DRAFT_PREFIX, MR_LABEL
from ..tracker.base import Ticket
from . import handoff, phase_a, sandbox


def run(ctx: RunContext, prof: Profile, repo: Path, work: Path, ticket: Ticket,
        spec: spec_mod.Spec, plan: str, agent, forge, branch: str) -> dict:
    ctx.phase = "B"
    evidence: dict = {"self_fix": {"lint": False, "testgen_rounds": 0,
                                   "implement_rounds": 0, "antigaming_rounds": 0},
                      #: Mỗi dòng là một lý do MR phải là Draft. Rỗng = MR thường.
                      "draft_reasons": []}
    is_t2 = spec.task_type == "T2"
    untested = spec.task_type == "T4"
    if untested and not prof.relaxed:
        ctx.decide(Reason.NO_MR, "task loại T4 — chế độ strict không implement khi không có test tất định",
                   kind=Kind.T4)

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
            ctx.decide(Reason.NEEDS_HUMAN,
                       "task T2 chỉ kết luận được nhờ coverage và mutation, hồ sơ repo "
                       f"còn thiếu: {', '.join(thieu)}",
                       kind=Kind.PROFILE_GAP, task_type=spec.task_type, missing=thieu)

    if prof.commands.get("setup"):
        with ctx.stage("setup"):
            res = gate_mod.setup(ctx, prof, work, why="worktree mới")
            if not res.ok:
                ctx.decide(Reason.NEEDS_HUMAN,
                           "commands.setup (cài dependency) thất bại trên worktree mới — kiểm lại "
                           f"lệnh trong hồ sơ repo:\n\n```\n{gate_mod._tail(res.stdout + res.stderr, 30)}\n```",
                           kind=Kind.PROFILE_GAP, command=prof.commands["setup"])

    with ctx.stage("baseline"):
        base = baseline_mod.capture(ctx, prof, work, scope=spec.modules,
                                    defer_lint=bool(prof.limit("baseline_lint_fix")))

    if base.lint == "fail":
        with ctx.stage("lint_fix"):
            _fix_baseline_lint(ctx, prof, work, spec, base, agent, evidence)

    if untested:
        _no_proof(ctx, evidence, "task T4 — không kiểm chứng được bằng test viết trước")
    else:
        with ctx.stage("test_first"):
            _test_first(ctx, prof, work, spec, plan, base, agent, evidence, is_t2)

    if is_t2:
        # Không có gì để sửa — chỉ cần chắc rằng test mới không làm hỏng gì.
        with ctx.stage("gate"):
            gate_report = gate_mod.run(ctx, prof, work, base)
        _stop_if_flaky(ctx, gate_report)
        if gate_report["verdict"] == "FAIL":
            ctx.decide(Reason.NO_MR, "gate fail ngay sau khi thêm test bù",
                       kind=Kind.GATE_FAIL, failed_checks=gate_report["failed_checks"])
    else:
        with ctx.stage("implement"):
            gate_report = _implement_loop(ctx, prof, work, spec, plan, base, agent, evidence)
            if fbpa := evidence.get("fail_before_pass_after"):
                fbpa["passed_after"] = True

    # Gate xanh trên một nhánh không đổi gì vẫn là gate xanh — nhưng mở MR rỗng (hoặc chỉ
    # có test cho một ticket sửa code) thì vô nghĩa. T2 thì thay đổi chính là test.
    changed = gitutil.changed_files(work, base.base_sha)
    if not (changed if is_t2 else [f for f in changed if not matches(f, prof.test_globs)]):
        ctx.decide(Reason.NO_MR, "agent không đổi " + ("file nào" if is_t2 else "file nguồn nào")
                   + " — không có gì để mở MR", kind=Kind.NO_CHANGE)

    ag = _verify(ctx, prof, work, spec, base, gate_report, evidence)

    # -- anti-gaming fail → agent sửa, không giao người vội (skill bước 9) --------
    rounds = int(prof.limit("antigaming_fix_rounds"))
    for round_no in range(1, rounds + 1):
        if ag["verdict"] == "PASS":
            break
        if ag["blocking_rules"]:
            break
        evidence["self_fix"]["antigaming_rounds"] = round_no
        ctx.emit("antigaming.retry", level="warn", round=round_no, rounds=rounds,
                 failed_rules=ag["failed_rules"], remedies=ag["remedies"])
        gate_report = _remediate(ctx, prof, work, spec, plan, base, agent, evidence, ag,
                                 round_no, is_t2)
        ag = _verify(ctx, prof, work, spec, base, gate_report, evidence)

    if ag["verdict"] == "FAIL" and not ag["blocking_rules"] and prof.relaxed:
        if softened := antigaming.soften(ag):
            evidence["draft_reasons"].append(
                f"{', '.join(softened)} chưa đạt sau {rounds} vòng agent tự sửa — hạ xuống cần review")
            _save_evidence(ctx, evidence)
            (ctx.run_dir / "antigaming-report.json").write_text(
                json.dumps(ag, ensure_ascii=False, indent=2), encoding="utf-8")
            ctx.emit("antigaming.softened", level="warn", rules=softened, verdict=ag["verdict"])

    if ag["verdict"] == "FAIL":
        if ag["blocking_rules"]:
            ctx.decide(Reason.NEEDS_HUMAN,
                       f"vi phạm {', '.join(ag['blocking_rules'])} — bằng chứng không còn tin "
                       f"được, giữ nguyên hiện trường, không tự sửa",
                       kind=Kind.ANTIGAMING_EVIDENCE,
                       failed_rules=ag["failed_rules"], blocking_rules=ag["blocking_rules"])
        ctx.decide(Reason.NO_MR,
                   f"anti-gaming vẫn fail ({', '.join(ag['failed_rules'])}) sau {rounds} vòng "
                   f"agent tự sửa",
                   kind=Kind.ANTIGAMING, failed_rules=ag["failed_rules"], rounds=rounds)

    with ctx.stage("create_mr"):
        mr = _create_mr(ctx, prof, work, ticket, spec, plan, base, gate_report, ag,
                        evidence, forge, branch)

    draft = bool(evidence["draft_reasons"])
    ctx.decide(Reason.OK, f"MR{' (Draft)' if draft else ''} đã mở: {mr.url}", mr=mr.url,
               draft=draft, needs_review=ag["needs_review"])
    return {"mr": mr}


def _no_proof(ctx: RunContext, evidence: dict, why: str) -> None:
    """relaxed: ghi nhận run đi tiếp mà không có bằng chứng fail-trước → MR Draft, G-4 cần review."""
    evidence["no_proof"] = why
    evidence["draft_reasons"].append(f"không có bằng chứng test fail-trước/pass-sau: {why}")
    ctx.emit("evidence.no_proof", level="warn", why=why,
             note="relaxed: vẫn implement, gate vẫn phải xanh, MR sẽ là Draft")


# -- bước 5: lint đỏ sẵn ---------------------------------------------------

def _fix_baseline_lint(ctx: RunContext, prof: Profile, work: Path, spec, base, agent,
                       evidence: dict) -> None:
    """Lint đỏ trên code chưa sửa: agent sửa bằng commit riêng TRƯỚC bước viết test.

    Khác test đỏ, lint sửa được tất định và không làm mờ bằng chứng fail-trước.
    Commit lint tách riêng để người review nhìn thấy nó không lẫn với fix nghiệp vụ,
    và base của anti-gaming dời lên sau commit này để G-3/G-10 không kết oan.
    """
    _, output = baseline_mod.lint_output(ctx, prof, work)
    prompt = (phase_a._skill("lint_fix") + phase_a._spec_block(spec)
              + f"\n## Kết quả lint hiện tại\n\n```\n{output}\n```\n"
              + phase_a._paths_block(prof) + phase_a.commands_block(prof))
    _ask(ctx, agent, work, "lint_fix", prompt)
    sha = sandbox.commit_all(work, "chore: sửa lint có sẵn trước khi sửa code")
    changed = gitutil.changed_files(work, base.base_sha)
    forbidden = [f for f in changed if matches(f, prof.forbidden_paths)]
    outside = [f for f in changed
               if prof.allowed_paths and not matches(f, prof.allowed_paths)]
    if forbidden or outside:
        ctx.decide(Reason.NO_MR,
                   "lint có sẵn nằm ở file agent không được đụng — sửa lint phải đi MR riêng",
                   kind=Kind.BASELINE_RED, check="lint", forbidden=forbidden[:10],
                   outside_allowed=outside[:10])
    status, output = baseline_mod.lint_output(ctx, prof, work)
    if status != "pass":
        ctx.decide(Reason.NO_MR,
                   "lint vẫn đỏ sau khi agent sửa — không đi tiếp trên nền lint đỏ",
                   kind=Kind.BASELINE_RED, check="lint", output=output[-1500:])
    base.lint = "pass"
    base.base_sha = sha
    base.save(ctx.run_dir / "baseline.json")
    evidence["self_fix"]["lint"] = True
    evidence["lint_fix"] = {"sha": sha, "files": changed}
    ctx.emit("lint_fix.done", sha=sha[:8], files=changed[:10],
             note="base của anti-gaming dời lên sau commit lint")


# -- bước 6: test trước ----------------------------------------------------

def _test_first(ctx: RunContext, prof: Profile, work: Path, spec, plan: str, base, agent,
                evidence: dict, is_t2: bool) -> str | None:
    """Viết test → commit → chạy → tiền kiểm chất lượng → (sửa) → đóng băng.

    Trả về sha đóng băng (mốc G-9), hoặc None khi relaxed bỏ test không chạy được.
    """
    # Agent viết test KHÔNG được thấy cách giải: chỉ AC (trong spec) và mục "Test sẽ
    # viết" mà người đã duyệt. Thấy "File sẽ thay đổi"/"Giải pháp" là test chép lại
    # đúng giả định của implement.
    tests_planned = _plan_section(plan, phase_a.SEC_TESTS)
    ctx.emit("plan.handed", to="test_gen", section="Test sẽ viết" if tests_planned else None,
             lines=len(tests_planned.splitlines()) if tests_planned else 0,
             level="info" if tests_planned else "warn",
             note=None if tests_planned else "plan không có mục 'Test sẽ viết' — agent chỉ có AC")
    base_prompt = (phase_a._skill("test_gen") + phase_a._spec_block(spec, for_tests=True)
                   + phase_a._paths_block(prof) + phase_a.commands_block(prof))
    if tests_planned:
        base_prompt += f"\n## Test sẽ viết (người đã duyệt)\n\n{tests_planned}\n"

    rounds = int(prof.limit("testgen_fix_rounds"))
    feedback = ""
    for attempt in range(rounds + 1):
        name = "test_gen" if attempt == 0 else f"test_gen-retry{attempt}"
        prompt = base_prompt + (f"\n## Test vừa viết chưa đạt, sửa lại\n\n{feedback}\n"
                                if feedback else "")
        _ask(ctx, agent, work, name, prompt)
        sha = sandbox.commit_all(work, _commit_msg("test", spec)
                                 if attempt == 0 else f"test: viết lại (lần {attempt})")
        problem, new_failures, data = _run_testfirst(ctx, prof, work, base, is_t2)
        issues = [] if problem else antigaming.precheck_tests(
            ctx, prof, work, base.base_sha, spec.task_type, spec.ac_ids)
        if not problem and not issues:
            break
        if attempt == rounds:
            # Hết lượt. Tiền kiểm còn cờ thì đi tiếp (anti-gaming cuối sẽ ghi
            # needs_review); test không dùng được thì NO_MR — kết quả hợp lệ, không
            # phải chuyện của người.
            if problem and prof.relaxed and problem in (Kind.TESTGEN_BROKEN, Kind.TEST_PASSES_PRE):
                return _relax_tests(ctx, prof, work, base, evidence, problem, data["why"], sha)
            if problem:
                ctx.decide(Reason.NO_MR, data.pop("why"), kind=problem, attempts=attempt + 1, **data)
            break
        evidence["self_fix"]["testgen_rounds"] = attempt + 1
        feedback = (_TESTGEN_FEEDBACK[problem].format(**data) if problem
                    else antigaming.feedback_from_results(issues))
        ctx.emit("testgen.retry", level="warn", attempt=attempt + 1,
                 problem=problem.value if problem else None, issues=[r.rule for r in issues])

    if not is_t2:
        evidence["fail_before_pass_after"] = {"failed_before": True, "tests": new_failures}
    # Mốc để G-9 so: từ đây trở đi test là hợp đồng, không ai được sửa.
    evidence["test_freeze"] = {"testfirst_sha": sha}
    ctx.emit("testfirst.frozen", sha=sha[:8],
             note="từ mốc này G-9 cấm mọi thay đổi trong file test")
    return sha


def _relax_tests(ctx: RunContext, prof: Profile, work: Path, base, evidence: dict,
                 problem: Kind, why: str, sha: str) -> str | None:
    """relaxed, hết lượt viết test: đi tiếp không có bằng chứng thay vì NO_MR.

    - Test không chạy được → gỡ bỏ (không để test hỏng lọt vào MR), không đóng băng.
    - Test pass sẵn trên code cũ → GIỮ (nó là test hồi quy hợp lệ) và đóng băng như thường.
    """
    _no_proof(ctx, evidence, why)
    if problem is Kind.TESTGEN_BROKEN:
        broken = [f for f in gitutil.changed_files(work, base.base_sha) if matches(f, prof.test_globs)]
        sandbox.restore_files(work, base.base_sha, broken)
        sandbox.commit_all(work, "revert: bỏ test không chạy được")
        ctx.emit("testfirst.dropped", level="warn", files=broken[:10])
        return None
    evidence["fail_before_pass_after"] = {"failed_before": False, "tests": []}
    evidence["test_freeze"] = {"testfirst_sha": sha}
    ctx.emit("testfirst.frozen", sha=sha[:8], note="test pass sẵn được giữ làm test hồi quy")
    return sha


#: Feedback cho agent viết test khi lượt trước không dùng được. Chỉ nói hiện tượng và
#: yêu cầu; không nói cách sửa code (agent này không được biết cách sửa).
_TESTGEN_FEEDBACK = {
    Kind.TESTGEN_BROKEN: (
        "Test vừa viết KHÔNG CHẠY ĐƯỢC (lỗi thu thập: sai import, sai cú pháp, fixture "
        "nổ) — không phán xét gì được về code. Test lỗi: {errors}\n\n```\n{output}\n```\n"
        "Sửa cho test chạy được; nó vẫn phải fail vì lý do nghiệp vụ trong AC, không phải vì lỗi kỹ thuật."),
    Kind.TEST_PASSES_PRE: (
        "Mọi test vừa viết đều PASS trên code CHƯA sửa — nghĩa là chúng không tái hiện bug "
        "mô tả trong AC. Đọc lại AC và bước tái hiện, viết test gọi đúng đường đi gây lỗi "
        "với giá trị đầu vào/kỳ vọng cụ thể. Nếu bạn kết luận code hiện tại đã đúng theo AC, "
        "nói thẳng điều đó thay vì cố làm test đỏ."),
    Kind.T2_RED: (
        "Task là T2: code hiện tại được coi là ĐÚNG, test bù phải PASS. Nhưng test sau đây "
        "lại đỏ: {tests}. Đọc lại code thật để xác định hành vi hiện tại và kiểm tra kỳ vọng "
        "của bạn. Nếu kỳ vọng của bạn sai, sửa test. Nếu bạn chắc chắn code có bug, GIỮ NGUYÊN "
        "test và nói rõ trong kết quả — hệ thống sẽ dừng và đề nghị đổi ticket sang T1."),
}


def _run_testfirst(ctx: RunContext, prof: Profile, work: Path, base, is_t2: bool
                   ) -> tuple[Kind | None, list[str], dict]:
    """Chạy suite sau khi có test mới.

    Trả về (vấn đề hoặc None, test MỚI đang fail, dữ liệu cho feedback/decide). Không
    decide() ở đây: agent còn lượt viết lại thì được viết lại (skill bước 6 + mục tiêu
    "agent tự xử lý"); hết lượt mới NO_MR.
    """
    junit = ctx.run_dir / "testfirst-junit.xml"
    parsers.reset(junit)
    res = ctx.cmd(prof.commands["test"].format(junit=str(junit)), cwd=work,
                  timeout=prof.limit("cmd_timeout_sec"))
    report = parsers.junit(junit)
    # File test vừa viết không chạy được (sai import, sai cú pháp) thì cả bước thu
    # thập đổ, và những gì đọc được sau đó không nói gì về code cả.
    new_errors = sorted(set(report.errored) - set(base.failed_tests))
    if new_errors or report.total == 0:
        why = ("test vừa viết không chạy được nên chưa phán xét gì được về code"
               + (f": {', '.join(new_errors[:3])}" if new_errors else " — không thu được test nào"))
        ctx.emit("testfirst.result", level="warn", total=report.total, errors=new_errors[:10])
        return Kind.TESTGEN_BROKEN, [], {
            "why": why, "errors": new_errors[:10] or ["(không thu được test nào)"],
            "output": _tail_text(res.stdout + res.stderr, 40), "total": report.total,
            "baseline_total": base.total, "exit_code": res.exit_code}
    new_failures = sorted(set(report.failed) - set(base.failed_tests))
    ctx.emit("testfirst.result", total=report.total, new_failures=new_failures[:10],
             level="info" if new_failures else "warn")
    if is_t2 and new_failures:
        return Kind.T2_RED, new_failures, {
            "why": "task khai là T2 (test bù cho code đã đúng) nhưng test bù vẫn đỏ trên code "
                   "chưa sửa sau khi agent kiểm lại kỳ vọng — nhiều khả năng code có bug thật; "
                   "đổi ticket sang T1 rồi đặt lại `agent:try`",
            "tests": new_failures[:10]}
    if not is_t2 and not new_failures:
        return Kind.TEST_PASSES_PRE, [], {
            "why": "test viết trước đã pass trên code chưa sửa — không có bug xác định để sửa",
            "total": report.total}
    return None, new_failures, {}


def _tail_text(text: str, lines: int) -> str:
    return "\n".join((text or "").strip().splitlines()[-lines:])


# -- bước 7/8: implement + gate ---------------------------------------------

def _implement_loop(ctx: RunContext, prof: Profile, work: Path, spec, plan: str,
                    base, agent, evidence: dict, feedback: str = "",
                    rounds: int | None = None, tag: str = "") -> dict:
    """Sửa → gate → sửa lại. Trần vòng lấy từ hồ sơ repo, không hardcode.

    `feedback` ban đầu (vd từ anti-gaming) đi vào prompt lượt đầu; `tag` phân biệt
    lượt của vòng khắc phục với lượt chính trong log/prompt file.
    """
    rounds = int(prof.limit("gate_fix_rounds")) if rounds is None else rounds
    same_error_limit = int(prof.limit("same_error_limit"))
    seen_errors: list[str] = []

    untested = ""
    if not evidence.get("test_freeze"):
        untested = ("\n## Không có test viết trước\n\nTask này KHÔNG có test đóng băng "
                    f"({evidence.get('no_proof') or 'không có'}). Nếu thay đổi kiểm được bằng test "
                    "tất định thì viết kèm test theo convention repo; không được thì thôi. Gate vẫn "
                    "chạy toàn bộ test và lint sẵn có, và MR sẽ mở dạng Draft.\n")
    for attempt in range(rounds + 1):
        prompt = (phase_a._skill("implement") + phase_a._spec_block(spec)
                  + f"\n## Plan đã được duyệt\n\n{plan}\n"
                  + phase_a._paths_block(prof) + phase_a.commands_block(prof) + untested
                  + (f"\n## Kết quả kiểm tra vừa rồi chưa đạt, sửa tiếp\n\n{feedback}\n"
                     if feedback else ""))
        _ask(ctx, agent, work, f"implement{tag}-{attempt + 1}", prompt)
        evidence["self_fix"]["implement_rounds"] += 1
        sandbox.commit_all(work, _commit_msg("fix", spec, plan)
                           if attempt == 0 and not tag else f"fix: vòng {attempt + 1}{tag}")

        gate_report = gate_mod.run(ctx, prof, work, base)
        _stop_if_flaky(ctx, gate_report)
        if gate_report["verdict"] == "PASS":
            ctx.emit("implement.done", rounds=attempt + 1)
            return gate_report

        signature = ",".join(sorted(gate_report["failed_checks"])) + "|" + ",".join(
            sorted(gate_report["checks"].get("test", {}).get("new_failures", []))[:5])
        seen_errors.append(signature)
        if seen_errors.count(signature) >= same_error_limit:
            ctx.decide(Reason.NO_MR,
                       f"cùng một lỗi lặp {same_error_limit} lần, dừng để khỏi đốt token",
                       kind=Kind.LOOP_LIMIT, signature=signature[:200])
        feedback = _feedback(gate_report)
        ctx.emit("implement.retry", level="warn", attempt=attempt + 1,
                 failed_checks=gate_report["failed_checks"])

    ctx.decide(Reason.NO_MR, f"gate vẫn fail sau {rounds} vòng tự sửa",
               kind=Kind.GATE_FAIL, failed_checks=gate_report["failed_checks"])


def _stop_if_flaky(ctx: RunContext, gate_report: dict) -> None:
    """FLAKY là chuyện của người: không quarantine, không skip, không xoá (skill bước 8)."""
    if flaky := gate_report.get("flaky"):
        ctx.decide(Reason.NEEDS_HUMAN,
                   "test mới fail nhưng không nhất quán giữa các lần chạy lại — không phân "
                   "biệt được lỗi agent với test flaky. Không tự quarantine/skip.",
                   kind=Kind.FLAKY, tests=flaky[:10])


def _feedback(gate_report: dict) -> str:
    lines = []
    for name, entry in gate_report["checks"].items():
        if entry["status"] == "fail":
            lines.append(f"- `{name}` fail (exit {entry.get('exit_code')})")
            for test in entry.get("new_failures", [])[:10]:
                lines.append(f"  - test fail (AGENT_INTRODUCED, 3/3 lần): {test}")
            if entry.get("pre_existing"):
                lines.append(f"  - (bỏ qua, lỗi sẵn có: {len(entry['pre_existing'])} test)")
            if entry.get("output"):
                lines.append("  ```")
                lines.extend("  " + row for row in entry["output"].splitlines()[-25:])
                lines.append("  ```")
    return "\n".join(lines)


# -- bước 9: coverage + mutation + anti-gaming, và vòng khắc phục ----------------

def _verify(ctx: RunContext, prof: Profile, work: Path, spec, base, gate_report: dict,
            evidence: dict) -> dict:
    """Đo coverage, chạy mutation (T2/T3), ghi evidence, chấm anti-gaming."""
    if prof.is_available("coverage"):
        with ctx.stage("coverage"):
            # Đo trên đúng cây code sắp đem đi mở MR; G-7 đọc lại file này.
            baseline_mod.measure_coverage(ctx, prof, work)

    if prof.mutation_cfg("enabled") and spec.task_type in ("T2", "T3"):
        with ctx.stage("mutation"):
            targets = (mutation.covered_lines(prof, baseline_mod.coverage_path(ctx), spec.modules)
                       if spec.task_type == "T2" else None)
            evidence["mutation"] = mutation.run(ctx, prof, work, base.base_sha, targets)

    _save_evidence(ctx, evidence)
    with ctx.stage("antigaming"):
        return antigaming.run(ctx, prof, work, base.base_sha, base, gate_report,
                              task_type=spec.task_type, acceptance_ids=spec.ac_ids,
                              modules=spec.modules)


def _remediate(ctx: RunContext, prof: Profile, work: Path, spec, plan: str, base, agent,
               evidence: dict, ag: dict, round_no: int, is_t2: bool) -> dict:
    """Một vòng khắc phục anti-gaming. Trả về gate report PASS (hoặc decide() và thoát).

    Thứ tự cố định: khôi phục test đóng băng (máy làm) → bổ sung test (agent viết
    test, dời mốc đóng băng) → sửa code (agent implement) → gate phải xanh.
    """
    remedies = set(ag["remedies"])
    tag = f"-ag{round_no}"

    if antigaming.RESTORE_TESTS in remedies:
        frozen = evidence["test_freeze"]["testfirst_sha"]
        touched = [f for f in gitutil.changed_files(work, frozen) if matches(f, prof.test_globs)]
        sandbox.restore_files(work, frozen, touched)
        sandbox.commit_all(work, "revert: khôi phục test đã đóng băng (G-9)")
        ctx.emit("antigaming.restored_tests", files=touched[:10], frozen=frozen[:8])

    if antigaming.TEST_GEN in remedies or (is_t2 and remedies):
        # T2 chỉ có agent viết test, nên mọi việc sửa (kể cả hoàn nguyên file) là của nó.
        fb = antigaming.feedback(ag) if is_t2 else antigaming.feedback(ag, only={antigaming.TEST_GEN})
        prompt = (phase_a._skill("test_gen") + phase_a._spec_block(spec, for_tests=True)
                  + phase_a.commands_block(prof)
                  + "\n## Bổ sung/sửa test theo kết quả kiểm tra\n\n"
                    "Test hiện có được giữ nguyên trừ khi phần dưới yêu cầu sửa. "
                    "Viết THÊM test để đáp ứng từng mục:\n\n" + fb + "\n")
        _ask(ctx, agent, work, f"test_gen{tag}", prompt)
        sha = sandbox.commit_all(work, f"test: bổ sung theo anti-gaming (vòng {round_no})")
        evidence["test_freeze"] = {"testfirst_sha": sha}
        ctx.emit("testfirst.frozen", sha=sha[:8], note="mốc đóng băng dời sau khi bổ sung test")

    if is_t2:
        gate_report = gate_mod.run(ctx, prof, work, base)
        _stop_if_flaky(ctx, gate_report)
        if gate_report["verdict"] == "FAIL":
            ctx.decide(Reason.NO_MR,
                       "T2: test bổ sung theo anti-gaming lại đỏ trên code chưa sửa — code có "
                       "bug thật hay test hiểu sai; đổi ticket sang T1 nếu là bug",
                       kind=Kind.T2_RED, failed_checks=gate_report["failed_checks"])
        return gate_report

    fb = antigaming.feedback(ag, only={antigaming.IMPLEMENT, antigaming.RESTORE_TESTS})
    if antigaming.IMPLEMENT in remedies or antigaming.RESTORE_TESTS in remedies:
        # Có việc cho implement: sửa theo feedback, gate phải xanh (1 lượt + 1 lượt sửa).
        return _implement_loop(ctx, prof, work, spec, plan, base, agent, evidence,
                               feedback=fb or "Xem kết quả gate bên dưới.", rounds=1, tag=tag)

    # Chỉ bổ sung test: gate phải xanh trên code hiện tại; test mới đỏ thì code còn
    # thiếu — cho implement một lượt sửa.
    gate_report = gate_mod.run(ctx, prof, work, base)
    _stop_if_flaky(ctx, gate_report)
    if gate_report["verdict"] == "PASS":
        return gate_report
    return _implement_loop(ctx, prof, work, spec, plan, base, agent, evidence,
                           feedback=_feedback(gate_report), rounds=1, tag=tag)


def _save_evidence(ctx: RunContext, evidence: dict) -> None:
    (ctx.run_dir / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


# -- bước 10: MR --------------------------------------------------------------

def _create_mr(ctx, prof, work, ticket, spec, plan, base, gate_report, ag, evidence,
               forge, branch):
    head = gitutil.head_sha(work)
    draft = bool(evidence.get("draft_reasons"))
    body = handoff.pack_task(
        _mr_body(ticket, spec, plan, base, gate_report, ag, evidence, head, ctx, work),
        task_type=spec.task_type, modules=spec.modules, acceptance_ids=spec.ac_ids)
    (ctx.run_dir / "mr-body.md").write_text(body, encoding="utf-8")
    forge.push_branch(work, branch)
    mr = forge.create_mr(branch, prof.base_branch, f"[{spec.task_id or ticket.id}] {spec.title}",
                         body, [MR_LABEL], draft=draft)
    ctx.emit("mr.created", url=mr.url, branch=branch, head_sha=head[:8], draft=draft,
             needs_review=ag["needs_review"])
    return mr


def _mr_body(ticket, spec, plan, base, gate_report, ag, evidence, head, ctx, work) -> str:
    """Mô tả MR theo skill bước 10: bằng chứng tự nói, không có câu "tôi tự tin rằng".

    Độ dài như một developer viết: cách giải + file đụng lấy từ plan, AC kèm test, hai
    bảng bằng chứng, rủi ro, truy vết. KHÔNG dán nguyên plan — plan đã nằm trên ticket,
    và dán vào là mọi mục Giả định/Ngoài phạm vi/Test/Rủi ro xuất hiện hai lần.
    """
    checks = "\n".join(
        f"| {name} | {entry['status']} | {_check_note(entry)} |"
        for name, entry in gate_report["checks"].items())
    rules = "\n".join(
        f"| {rule} | {_rule_icon(data['status'])} {data['status']} | {data['why']} |"
        for rule, data in ag["rules"].items())
    acs = "\n".join(f"- **{ac['id']}**: {ac['text']}" for ac in spec.acceptance_criteria)
    warning = _plan_preamble(plan)
    approach = _plan_section(plan, phase_a.SEC_APPROACH) or spec.objective
    # Giả định/ngoài phạm vi: spec + phần plan bổ sung từ discovery (nếu có nội dung
    # thật). `_dedup` chỉ bắt được bản chép y hệt — lưới chính là planning.md chỉ đòi
    # mục "mới", đây là lưới hai khi agent vẫn chép cả mục cũ sang.
    assumed = _dedup([*spec.assumptions, *_bullets(_plan_section(plan, phase_a.SEC_ASSUMPTIONS))])
    skipped = _dedup([*spec.out_of_scope, *_bullets(_plan_section(plan, phase_a.SEC_OUT_OF_SCOPE))])
    pre_existing = gate_report["checks"].get("test", {}).get("pre_existing") or []

    review = ""
    if ag["needs_review"]:
        rows = "\n".join(
            f"- **{rule}** — {ag['rules'][rule]['why']}\n  "
            f"`{json.dumps({k: v for k, v in ag['rules'][rule]['evidence'].items() if v}, ensure_ascii=False)[:300]}`"
            for rule in ag["needs_review"])
        review = f"""
**⚠️ Cần người review tự quyết** (luật heuristic, không chặn MR):

{rows}
"""
    tests_line = _tests_line(gate_report, base, ag, evidence)
    fixes = evidence.get("self_fix", {})
    fix_lines = []
    if fixes.get("lint"):
        fix_lines.append(f"- Sửa lint có sẵn bằng commit riêng `{evidence['lint_fix']['sha'][:12]}` "
                         f"({len(evidence['lint_fix']['files'])} file)")
    if fixes.get("testgen_rounds"):
        fix_lines.append(f"- Test viết lại theo tiền kiểm: {fixes['testgen_rounds']} lần")
    if (n := fixes.get("implement_rounds", 0)) > 1:
        fix_lines.append(f"- Lượt implement: {n}")
    if fixes.get("antigaming_rounds"):
        fix_lines.append(f"- Vòng khắc phục anti-gaming: {fixes['antigaming_rounds']}")
    if mut := evidence.get("mutation"):
        fix_lines.append(f"- Mutation: {mut.get('status')} — giết {mut.get('kill_pct', '—')}% "
                         f"({mut.get('killed', 0)}/{mut.get('total', 0)})")
    def section(title: str, body: str) -> str:
        return f"## {title}\n\n{body}\n\n" if body.strip() else ""

    # Mục trống (PRE_EXISTING, fix_lines, review) để lại dòng trống thừa — dồn lại,
    # nếu không mô tả MR trông như bị cắt dở.
    draft_block = ""
    if reasons := evidence.get("draft_reasons"):
        draft_block = ("## Vì sao MR này là Draft\n\n"
                       + "\n".join(f"- {r}" for r in reasons)
                       + f"\n\nNgười review kiểm hành vi bằng tay rồi bỏ tiền tố `{DRAFT_PREFIX.strip()}`.\n\n")
    return re.sub(r"\n{3,}", "\n\n", (
        (f"{warning}\n\n" if warning else "")
        + draft_block
        + f"## Giải pháp\n\n{approach}\n\n"
        + f"## Acceptance criteria\n\n{acs}\n\n{tests_line}\n\n"
        + section("Giả định đã duyệt", "\n".join(f"- {a}" for a in assumed))
        + section("Ngoài phạm vi (cố tình không thực hiện)", "\n".join(f"- {o}" for o in skipped))
        + f"""## Bằng chứng

| Check | Kết quả | Ghi chú |
|---|---|---|
{checks}

Baseline: {base.total} test, {len(base.failed_tests)} lỗi sẵn có, ổn định {base.runs}/{base.runs} lần.
{f"PRE_EXISTING (đã đỏ từ baseline, bỏ qua): {', '.join(pre_existing[:10])}" if pre_existing else ""}

| Anti-gaming | Kết quả | Ghi chú |
|---|---|---|
{rules}

{chr(10).join(fix_lines)}
{review}
## Truy vết

Ticket {ticket.id} (gốc {ticket.parent or "—"}) · commit `{head[:12]}` · run `{ctx.run_id}` (`{ctx.run_dir}/events.jsonl`)

---
*MR do agent tạo. Gate và anti-gaming là script tất định, CI chạy lại toàn bộ trên MR này.*
"""))


def _tests_line(gate_report: dict, base, ag: dict, evidence: dict) -> str:
    """Một câu về test, dựng từ số liệu script — không dán lại mục "Test sẽ viết".

    Danh sách nodeid kèm assert nằm sẵn trong diff; nhắc lại trong mô tả là bắt
    người review đọc hai lần cùng một thứ.
    """
    total = gate_report["checks"].get("test", {}).get("total")
    added = (total - base.total) if isinstance(total, int) else 0
    proved = [t.split("::")[-1] for t in
              ((ag["rules"].get("G-4") or {}).get("evidence") or {}).get("tests") or []]
    head = (f"**Test:** {added} test mới ({base.total} → {total})" if added > 0
            else f"**Test:** {total} test")
    if proved:
        tail = ("; đỏ trên code cũ rồi xanh sau khi sửa: "
                + ", ".join(f"`{t}`" for t in proved[:8]))
    elif mut := evidence.get("mutation"):
        tail = (f"; code đã đúng sẵn nên test xanh ngay, mutation giết "
                f"{mut.get('kill_pct', '—')}% đột biến")
    else:
        tail = ""
    return head + tail + "."


def _dedup(items: list[str]) -> list[str]:
    """Giữ thứ tự, bỏ trùng y hệt. Plan chép lại giả định của spec là chuyện đã xảy ra."""
    return list(dict.fromkeys(i.strip() for i in items if i and i.strip()))


def _plan_preamble(plan: str) -> str:
    """Dòng `⚠` planning.md dặn đặt TRƯỚC mọi heading, hoặc "".

    MR dựng theo heading nên dòng này rơi mất — đúng chỗ reviewer cần nó nhất:
    nó là câu "AC-x không đạt được trong allowed_paths".
    """
    head = _HEADING.search(plan or "")
    text = (plan[:head.start()] if head else (plan or "")).strip()
    return text if text.startswith("⚠") else ""


def _one_sentence(text: str, limit: int = 200) -> str:
    """Câu đầu của một đoạn, cắt ở ranh giới từ nếu quá dài."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    head = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(head) <= limit or " " not in head[:limit]:
        return head[:limit]
    return head[:limit][:head[:limit].rfind(" ")].rstrip(" ,;:.-") + "…"


def _commit_msg(prefix: str, spec, plan: str = "") -> str:
    """Subject ngắn, một câu vì sao, mã ticket — dài như dev viết tay.

    `plan` bỏ trống ở commit test: vòng viết lại sau đọc được `git log`, mà
    "Giải pháp" là đúng thứ bước test-first không được thấy (skill bước 6).
    """
    parts = [f"{prefix}: {spec.title}"]
    if why := (_one_sentence(_plan_section(plan, phase_a.SEC_APPROACH)) if plan else ""):
        parts.append(textwrap.fill(why, width=72))
    if spec.task_id:
        parts.append(spec.task_id)
    return "\n\n".join(parts)


def _check_note(entry: dict) -> str:
    if entry.get("reason"):
        return entry["reason"]
    parts = []
    if "total" in entry:
        parts.append(f"{entry['total']} test")
    if entry.get("pre_existing"):
        parts.append(f"{len(entry['pre_existing'])} PRE_EXISTING")
    if "exit_code" in entry:
        parts.append(f"exit {entry['exit_code']}")
    return ", ".join(parts)


def _rule_icon(status: str) -> str:
    return {"pass": "✅", "fail": "❌", "needs_review": "⚠️", "out_of_scope": "⊘"}.get(status, "")


_HEADING = phase_a._HEADING
_BULLET = phase_a._BULLET
_plan_section = phase_a._plan_section
_bullets = phase_a._bullets
_ask = phase_a._ask
