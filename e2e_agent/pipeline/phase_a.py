"""Phase A — Intake, Discovery, Planning. Kết thúc bằng plan chờ người duyệt.

Phase này chỉ đọc; không dòng code nào bị sửa.
"""
from __future__ import annotations

from pathlib import Path

from ..agent import spec as spec_mod
from ..agent.headless import AgentResult
from ..config.profile import Profile
from ..core.reasons import Reason
from ..core.run import RunContext
from ..tracker.base import Ticket

SKILLS = Path(__file__).resolve().parents[1] / "skills"


def run(ctx: RunContext, prof: Profile, repo: Path, ticket: Ticket, agent,
        rejections: list[str] | None = None) -> dict:
    """Trả về {spec, plan, discovery}. Tự decide() và thoát nếu không đi tiếp được.

    `rejections` là lý do người đã bác các plan trước. Không đưa vào prompt thì
    agent lập lại đúng cái plan vừa bị bác, và vòng từ chối thành vòng luẩn quẩn.
    """
    ctx.phase = "A"
    rejected_block = _rejected_block(rejections)
    if rejections:
        ctx.emit("planning.rejections", count=len(rejections))

    with ctx.stage("intake"):
        prompt = _skill("intake") + _ticket_block(ticket) + rejected_block
        result = _ask(ctx, agent, repo, "intake", prompt)
        spec = _parse_spec(ctx, agent, repo, result, ticket, prompt)
        ctx.emit("intake.spec", task_type=spec.task_type, ac=len(spec.acceptance_criteria),
                 not_ready=spec.not_ready())
        if spec.task_type == "T4":
            ctx.decide(Reason.NOMR_T4, "task không kiểm chứng được bằng test tất định",
                       objective=spec.objective)
        if missing := spec.not_ready():
            ctx.decide(Reason.NOMR_NOT_READY, f"Definition of Ready chưa đạt: {', '.join(missing)}",
                       missing=missing)
        spec.save(ctx.run_dir / "spec.yaml")

    with ctx.stage("discovery"):
        result = _ask(ctx, agent, repo, "discovery",
                      _skill("discovery") + _spec_block(spec))
        discovery = result.text
        (ctx.run_dir / "discovery.md").write_text(discovery, encoding="utf-8")

    with ctx.stage("planning"):
        prompt = (_skill("planning") + _spec_block(spec)
                  + f"\n## Kết quả discovery\n\n{discovery}\n"
                  + f"\n## Ràng buộc đường dẫn\n\nallowed_paths: {prof.allowed_paths}\n"
                    f"forbidden_paths: {prof.forbidden_paths}\n"
                  + rejected_block)
        result = _ask(ctx, agent, repo, "planning", prompt)
        plan = result.text
        (ctx.run_dir / "plan.md").write_text(plan, encoding="utf-8")
        ctx.emit("planning.ready", chars=len(plan))

    return {"spec": spec, "plan": plan, "discovery": discovery}


def _ask(ctx: RunContext, agent, repo: Path, name: str, prompt: str) -> AgentResult:
    result = agent.run(ctx, prompt, repo, name)
    if not result.ok:
        ctx.decide(Reason.ERROR, f"agent {name} lỗi: {result.error}", agent=name)
    return result


def _parse_spec(ctx: RunContext, agent, repo: Path, result: AgentResult, ticket: Ticket,
                prompt: str):
    """Sai schema thì cho sửa lại một lần kèm chính thông báo lỗi.

    Sai YAML là lỗi định dạng, không phải lỗi hiểu đề — bỏ cuộc ngay là lãng phí
    cả bước Intake đã chạy.
    """
    try:
        spec = spec_mod.parse(result.text)
    except spec_mod.SpecError as exc:
        ctx.emit("intake.retry", level="warn", error=str(exc)[:200])
        retry = _ask(ctx, agent, repo, "intake-retry",
                     prompt + f"\n\n## Lần trước bạn in ra YAML không hợp lệ\n\n"
                              f"Lỗi: {exc}\n\nIn lại đúng một khối ```yaml, "
                              f"đặt MỌI giá trị text trong nháy đơn.\n")
        try:
            spec = spec_mod.parse(retry.text)
        except spec_mod.SpecError as exc2:
            ctx.decide(Reason.ERROR, f"spec agent sinh ra sai schema sau 2 lần: {exc2}")
    spec.task_id = spec.task_id or ticket.id
    spec.source_ticket = spec.source_ticket or ticket.url or ticket.id
    return spec


def _skill(name: str) -> str:
    return (SKILLS / f"{name}.md").read_text(encoding="utf-8") + "\n\n---\n\n"


def _rejected_block(rejections: list[str] | None) -> str:
    if not rejections:
        return ""
    lines = "\n".join(f"{i}. {why}" for i, why in enumerate(rejections, 1))
    return ("\n## Plan trước đã bị người duyệt từ chối\n\n"
            f"{lines}\n\nLập plan khác hẳn, đừng lặp lại hướng đã bị bác. "
            "Nếu lý do từ chối cho thấy ticket còn thiếu thông tin, hãy nói thẳng "
            "điều đó trong plan thay vì đoán.\n")


def _ticket_block(ticket: Ticket) -> str:
    return f"## Ticket {ticket.id}\n\n**{ticket.title}**\n\n{ticket.body}\n"


def _spec_block(spec) -> str:
    lines = ["## Spec\n", f"- Mục tiêu: {spec.objective}", f"- Loại: {spec.task_type}",
             f"- Phạm vi: {spec.modules}"]
    lines += [f"- {ac['id']}: {ac['text']}" for ac in spec.acceptance_criteria]
    if spec.repro_steps:
        lines.append("- Tái hiện: " + " → ".join(spec.repro_steps))
    return "\n".join(lines) + "\n"
