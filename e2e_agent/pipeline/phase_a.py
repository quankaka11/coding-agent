"""Phase A — Intake, Discovery, Planning. Kết thúc bằng plan chờ người duyệt.

Phase này chỉ đọc; không dòng code nào bị sửa.
"""
from __future__ import annotations

from pathlib import Path

from ..agent import spec as spec_mod
from ..agent.headless import AgentResult
from ..config.profile import Profile
from ..core.reasons import Kind, Reason
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
        prompt = (_skill("intake") + _mode_block(prof.conventions.get("intake_mode", "assume"))
                  + _ticket_block(ticket) + rejected_block)
        result = _ask(ctx, agent, repo, "intake", prompt)
        spec = _parse_spec(ctx, agent, repo, result, ticket, prompt)
        # Lưu TRƯỚC khi phán: spec là thứ người xem khi hỏi "agent vướng ở đâu",
        # kể cả (nhất là) khi run dừng ở not_ready.
        spec.save(ctx.run_dir / "spec.yaml")
        ctx.emit("intake.spec", task_type=spec.task_type, ac=len(spec.acceptance_criteria),
                 not_ready=spec.not_ready(), questions=spec.questions[:5],
                 assumptions=len(spec.assumptions), out_of_scope=len(spec.out_of_scope))
        if spec.task_type == "T4":
            ctx.decide(Reason.NO_MR,
                       "task loại T4: không kiểm chứng được bằng test tất định (phụ thuộc "
                       "output LLM, đổi prompt, hành vi không quan sát được bằng assert)",
                       kind=Kind.T4, objective=spec.objective)
        if missing := spec.not_ready():
            if spec.unexplained():
                spec = _ask_for_notes(ctx, agent, repo, spec, prompt)
                spec.save(ctx.run_dir / "spec.yaml")
            ctx.decide(Reason.NO_MR,
                       f"Definition of Ready chưa đạt ({', '.join(missing)}):\n\n"
                       f"{spec.not_ready_report()}",
                       kind=Kind.NOT_READY, missing=missing,
                       notes=spec.readiness_notes, questions=spec.questions)

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


def _ask_for_notes(ctx: RunContext, agent, repo: Path, spec, prompt: str):
    """Agent đánh `fail` mà không nói vì sao → xin bổ sung MỘT lần, không coi là lỗi.

    Người viết ticket cần lý do để sửa ticket; nhưng agent quên ghi lý do không phải
    lỗi hệ thống, và cũng không phải chuyện phải giao người. Vẫn thiếu thì NO_MR kèm
    ghi chú "agent không ghi lý do" và spec.yaml để xem.
    """
    missing = spec.unexplained()
    ctx.emit("intake.ask_notes", level="warn", unexplained=missing)
    retry = _ask(ctx, agent, repo, "intake-notes",
                 prompt + "\n\n## Bổ sung lý do\n\nBạn đánh `fail` cho "
                          f"{missing} nhưng chưa ghi vì sao. In lại TOÀN BỘ khối ```yaml y như "
                          "trước, thêm `readiness_notes` (một dòng cho mỗi mục fail, chỉ đúng chỗ "
                          "mơ hồ trong ticket) và `questions` (câu người viết ticket phải trả "
                          "lời). Không đổi các mục đã pass.\n")
    try:
        new = spec_mod.parse(retry.text)
    except spec_mod.SpecError as exc:
        ctx.emit("intake.ask_notes_failed", level="warn", error=str(exc)[:200])
        return spec
    new.task_id, new.source_ticket = spec.task_id, spec.source_ticket
    return new


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


def _mode_block(mode: str) -> str:
    if mode == "ask":
        return ("## Chế độ: ask\n\nRepo này muốn được HỎI khi ticket mơ hồ: chỗ nào không rõ thì "
                "đánh `fail` ở readiness kèm `readiness_notes` và `questions`, không tự giả định. "
                "`assumptions` để [].\n\n")
    return ("## Chế độ: assume\n\nÁp \"Nguyên tắc tự chủ\" ở trên: không hỏi lại; chọn cách hiểu "
            "hẹp nhất đúng chữ trên ticket, ghi vào `assumptions`; việc liên quan nhưng ticket "
            "không yêu cầu ghi vào `out_of_scope`. Chỉ `fail` khi không cách hiểu nào ra được "
            "test tất định.\n\n")


def _ticket_block(ticket: Ticket) -> str:
    return f"## Ticket {ticket.id}\n\n**{ticket.title}**\n\n{ticket.body}\n"


def _spec_block(spec) -> str:
    lines = ["## Spec\n", f"- Mục tiêu: {spec.objective}", f"- Loại: {spec.task_type}",
             f"- Phạm vi: {spec.modules}"]
    lines += [f"- {ac['id']}: {ac['text']}" for ac in spec.acceptance_criteria]
    if spec.repro_steps:
        lines.append("- Tái hiện: " + " → ".join(spec.repro_steps))
    if spec.assumptions:
        lines.append("\n### Giả định đã chốt (người duyệt plan đã thấy)")
        lines += [f"- {a}" for a in spec.assumptions]
    if spec.out_of_scope:
        lines.append("\n### NGOÀI PHẠM VI — không làm, không đụng")
        lines += [f"- {o}" for o in spec.out_of_scope]
    return "\n".join(lines) + "\n"
