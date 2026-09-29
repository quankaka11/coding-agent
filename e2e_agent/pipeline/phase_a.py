"""Phase A — Intake, Discovery, Planning. Kết thúc bằng plan chờ người duyệt.

Phase này chỉ đọc; không dòng code nào bị sửa.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..agent import spec as spec_mod
from ..agent.headless import AgentResult
from ..config.profile import Profile
from ..core.reasons import Kind, Reason
from ..core.run import RunContext
from ..tracker.base import Ticket

SKILLS = Path(__file__).resolve().parents[1] / "skills"

#: Từ khoá cắt mục của plan. Đây là HỢP ĐỒNG với `skills/planning.md`:
#: `_plan_section` tìm heading CHỨA chuỗi này, nên đổi tên mục bên skill mà quên ở
#: đây là mục đó lặng lẽ biến mất khỏi mô tả MR. Đổi thì đổi cả hai chỗ.
SEC_APPROACH = "giải pháp"
SEC_FILES = "file"
SEC_TESTS = "test"
SEC_ASSUMPTIONS = "giả định"
SEC_OUT_OF_SCOPE = "ngoài phạm vi"
SEC_RISKS = "rủi ro"
#: planning.md dặn plan ≤ 40 dòng. Quá xa mức đó thì agent đang phớt lờ ràng buộc,
#: và plan dài là thứ chảy thẳng vào mô tả MR — nói ra trong log thay vì im lặng.
PLAN_MAX_LINES = 60


def run(ctx: RunContext, prof: Profile, repo: Path, ticket: Ticket, agent,
        rejections: list[str] | None = None,
        talk: list[tuple[str, str]] | None = None,
        previous_plan: str | None = None) -> dict:
    """Trả về {spec, plan, discovery}. Tự decide() và thoát nếu không đi tiếp được.

    `rejections` là lý do người đã bác các plan trước. Không đưa vào prompt thì
    agent lập lại đúng cái plan vừa bị bác, và vòng từ chối thành vòng luẩn quẩn.
    `previous_plan`: plan chưa duyệt mà người vừa góp ý (góp ý nằm trong `talk`) — sửa
    plan đó theo góp ý, không lập một plan khác hẳn.
    """
    ctx.phase = "A"
    rejected_block = _rejected_block(rejections) + _revise_block(previous_plan)
    if previous_plan:
        ctx.emit("planning.revise", note="sửa plan trước theo góp ý của người")
    if rejections:
        ctx.emit("planning.rejections", count=len(rejections))
    talk_block = _talk_block(talk)
    if talk:
        ctx.emit("intake.conversation", comments=len(talk),
                 human=sum(1 for who, _ in talk if who == "human"))

    with ctx.stage("intake"):
        # Intake phải biết vùng agent được đụng: không thì scope/AC kéo cả docs, README
        # vào, planning đành ghi "không làm được", và MR mở ra với một AC tự nhận không đạt.
        prompt = (_skill("intake") + _mode_block(prof.conventions.get("intake_mode", "assume"))
                  + _ticket_block(ticket) + talk_block + _paths_block(prof) + rejected_block)
        result = _ask(ctx, agent, repo, "intake", prompt)
        spec = _parse_spec(ctx, agent, repo, result, ticket, prompt)
        # Lưu TRƯỚC khi phán: spec là thứ người xem khi hỏi "agent vướng ở đâu",
        # kể cả (nhất là) khi run dừng ở not_ready.
        spec.save(ctx.run_dir / "spec.yaml")
        ctx.emit("intake.spec", task_type=spec.task_type, ac=len(spec.acceptance_criteria),
                 not_ready=spec.not_ready(), questions=spec.questions[:5],
                 assumptions=len(spec.assumptions), out_of_scope=len(spec.out_of_scope))
        if spec.task_type == "T4":
            if not prof.relaxed:
                ctx.decide(Reason.NO_MR,
                           "task loại T4: không kiểm chứng được bằng test tất định (phụ thuộc "
                           "output LLM, đổi prompt, hành vi không quan sát được bằng assert)",
                           kind=Kind.T4, objective=spec.objective)
            # relaxed: vẫn làm, gate vẫn phải xanh, MR mở dạng Draft vì không có bằng
            # chứng test — người review MR là chốt chặn cho loại việc này.
            ctx.emit("intake.t4_relaxed", level="warn",
                     note="T4 ở chế độ relaxed: implement không có test viết trước, MR sẽ là Draft")
        if spec.blocking(prof.relaxed) and spec.questions:
            spec = _reconsider(ctx, agent, repo, spec, prompt)
            spec.save(ctx.run_dir / "spec.yaml")
        if bad := scope_violations(prof, spec.modules):
            # Kiểm bằng script, trước khi tốn Discovery/Planning và trước khi người duyệt:
            # plan đòi sửa file G-3 sẽ chặn thì duyệt xong cũng chỉ ra NO_MR ở Phase B.
            ctx.decide(Reason.NEEDS_HUMAN, _scope_why(prof, bad), kind=Kind.SCOPE_POLICY,
                       files=[f for f, _ in bad])
        if gaps := spec.soft_gaps(prof.relaxed):
            ctx.emit("intake.soft_gaps", level="warn", gaps=gaps,
                     note="relaxed: readiness chưa đạt nhưng không chặn — hiện cảnh báo trên plan")
        if missing := spec.blocking(prof.relaxed):
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
                      _skill("discovery") + _spec_block(spec) + commands_block(prof))
        discovery = result.text
        (ctx.run_dir / "discovery.md").write_text(discovery, encoding="utf-8")

    with ctx.stage("planning"):
        prompt = (_skill("planning") + _spec_block(spec)
                  + f"\n## Kết quả discovery\n\n{discovery}\n"
                  + talk_block + _paths_block(prof) + commands_block(prof) + rejected_block)
        result = _ask(ctx, agent, repo, "planning", prompt)
        plan, dropped = _tidy_plan(result.text)
        (ctx.run_dir / "plan.md").write_text(plan, encoding="utf-8")
        long_plan = len(plan.splitlines()) > PLAN_MAX_LINES
        ctx.emit("planning.ready", chars=len(plan), lines=len(plan.splitlines()),
                 dropped_sections=dropped,
                 level="warn" if long_plan else "info",
                 note=f"plan dài hơn {PLAN_MAX_LINES} dòng — prompt dặn ≤ 40"
                      if long_plan else None)

    return {"spec": spec, "plan": plan, "discovery": discovery}


def _ask(ctx: RunContext, agent, repo: Path, name: str, prompt: str) -> AgentResult:
    """Gọi agent; lỗi tiến trình (timeout, mạng, thoát khác 0) thì thử lại MỘT lần.

    Lỗi kiểu đó không phải chuyện của người — giao người ngay là bắt họ bấm chạy lại.
    Lần hai vẫn lỗi mới là ERROR.
    """
    result = agent.run(ctx, prompt, repo, name)
    if not result.ok:
        ctx.emit("agent.retry", level="warn", agent=name, error=result.error[:200])
        result = agent.run(ctx, prompt, repo, f"{name}-again")
    if not result.ok:
        ctx.decide(Reason.ERROR, f"agent {name} lỗi 2 lần: {result.error}", agent=name)
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


def scope_violations(prof: Profile, modules: list[str]) -> list[tuple[str, str]]:
    """[(file, lý do)] cho mỗi mục trong scope.modules mà G-3 sẽ chặn."""
    from ..config.profile import matches
    out = []
    for module in modules:
        rel = str(module).strip().removeprefix("./").rstrip("/")
        if not rel:
            continue
        if pattern := matches(rel, prof.forbidden_paths):
            out.append((rel, f"thuộc `forbidden_paths` (khớp `{pattern}`)"))
        elif not matches(rel, prof.allowed_paths):
            out.append((rel, "ngoài `allowed_paths`"))
    return out


def _scope_why(prof: Profile, bad: list[tuple[str, str]]) -> str:
    rows = "\n".join(f"- `{f}`: {why}" for f, why in bad)
    return (f"{rows}\n\nNgười quản trị: thêm pattern vào `allowed_paths` (hoặc bỏ khỏi "
            f"`forbidden_paths` nếu chắc chắn muốn cho agent sửa) — tab Cấu hình → Hồ sơ trên giao "
            f"diện, hoặc `profiles/{prof.repo_id}.yaml`. allowed_paths hiện tại: "
            f"{', '.join(f'`{p}`' for p in prof.allowed_paths)}.")


def _reconsider(ctx: RunContext, agent, repo: Path, spec, prompt: str):
    """Sắp dừng ticket để hỏi người → cho agent xét lại MỘT lần xem có tự chốt được không.

    Mỗi câu hỏi là một vòng chờ người (vài giờ, có khi vài ngày) để trả lời thứ agent tự
    quyết được: "sơ đồ dạng Mermaid hay ASCII", "đặt ở mục nào". Plan vẫn qua tay người
    duyệt, nên một giả định hợp lý ghi rõ trên plan rẻ hơn nhiều một câu hỏi.
    """
    before = list(spec.questions)
    qs = "\n".join(f"{i}. {q}" for i, q in enumerate(before, 1))
    retry = _ask(ctx, agent, repo, "intake-reconsider", prompt + (
        "\n\n## Xét lại trước khi hỏi người\n\n"
        f"Bạn vừa định dừng ticket để hỏi người:\n\n{qs}\n\n"
        f"Spec bạn vừa in:\n\n```yaml\n{spec.dumps()}```\n\n"
        "Mỗi câu hỏi làm ticket chờ người thêm một vòng. Xét TỪNG câu:\n"
        "- Có lựa chọn mặc định hợp lý (quy ước phổ biến, cách repo đang làm, cách hiểu hẹp "
        "nhất) → TỰ CHỐT: ghi vào `assumptions` (người duyệt plan thấy và bác được), bỏ câu đó "
        "khỏi `questions`. Định dạng, vị trí, tên gọi, mức chi tiết, cách trình bày đều thuộc loại này.\n"
        "- Câu về `allowed_paths`, `forbidden_paths`, hồ sơ repo, CI, hay cách implement → BỎ "
        "HẲN: người viết ticket không đổi được mấy thứ đó; hệ thống tự kiểm phạm vi. Ghi đúng "
        "file ticket cần sửa vào `scope.modules`.\n"
        "- Chỉ GIỮ câu mà chỉ người viết ticket mới biết (quy tắc nghiệp vụ, giá trị đúng, hành "
        "vi mong đợi) VÀ đoán sai thì kết quả vô dụng.\n\n"
        "Không còn câu nào thì đổi các mục readiness đã fail vì chúng về `pass`. In lại TOÀN BỘ "
        "một khối ```yaml.\n"))
    try:
        new = spec_mod.parse(retry.text)
    except spec_mod.SpecError as exc:
        ctx.emit("intake.reconsider_failed", level="warn", error=str(exc)[:200])
        return spec
    new.task_id, new.source_ticket = spec.task_id, spec.source_ticket
    ctx.emit("intake.reconsider", questions_before=len(before), questions_after=len(new.questions),
             still_blocking=new.blocking(True), assumptions=len(new.assumptions))
    return new


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


def _revise_block(previous_plan: str | None) -> str:
    if not previous_plan:
        return ""
    return ("\n## Plan trước — người đã góp ý (xem mục Trao đổi trên ticket)\n\n"
            f"{previous_plan}\n\n"
            "Sửa CHÍNH plan này theo góp ý MỚI NHẤT của người; phần người không nhắc tới thì giữ "
            "nguyên. Góp ý đòi thêm file hay đổi loại task thì cứ làm theo — hệ thống sẽ tự đưa "
            "plan lại cho người duyệt. Đừng mở rộng phạm vi khi góp ý không đòi.\n")


def _talk_block(talk: list[tuple[str, str]] | None) -> str:
    """Cuộc trao đổi trên ticket. Người trả lời ở comment là một phần của ticket."""
    if not talk:
        return ""
    rows = "\n\n".join(f"**{'Người' if who == 'human' else 'Agent (lần chạy trước)'}:**\n{body}"
                       for who, body in talk)
    return ("\n## Trao đổi trên ticket (cũ → mới)\n\n"
            "Comment của người ở đây là một phần của ticket, ngang với mô tả; chỗ nào mâu thuẫn "
            "thì comment MỚI HƠN thắng. Câu nào agent đã hỏi mà người đã trả lời thì dùng câu trả "
            "lời đó — KHÔNG hỏi lại, không đánh `fail` readiness vì chính điều đó nữa.\n\n"
            f"{rows}\n")


def _mode_block(mode: str) -> str:
    if mode == "ask":
        return ("## Chế độ: ask\n\nRepo này muốn được HỎI: chỗ mơ hồ thì đánh `fail` ở readiness "
                "kèm `readiness_notes` và `questions`, không tự giả định; `assumptions` để [].\n\n")
    return "## Chế độ: assume\n\nÁp đúng mục \"Cách xử lý chỗ mơ hồ\" ở trên.\n\n"


def _paths_block(prof: Profile) -> str:
    deps = ("\ndependency_files (được sửa nếu thật cần, sẽ bị gắn cờ review): "
            f"{prof.dependency_files}\n" if prof.relaxed else
            f"\ndependency_files (KHÔNG được sửa ở chế độ strict): {prof.dependency_files}\n")
    return (f"\n## Ràng buộc đường dẫn\n\nallowed_paths: {prof.allowed_paths}\n"
            f"forbidden_paths: {prof.forbidden_paths}\n{deps}")


def commands_block(prof: Profile) -> str:
    """Lệnh thật của repo — ngôn ngữ nào cũng vậy, agent chạy đúng lệnh mà gate sẽ chạy.

    Không có khối này thì agent tự đoán cách chạy test (thường là pytest), và ở repo
    Node/Go/Java nó sẽ báo "test xanh" bằng một lệnh gate không bao giờ chạy.
    """
    rows = []
    for name in ("setup", "build", "test", "lint"):
        cmd = prof.commands.get(name)
        if not cmd or (name != "setup" and not prof.is_available(name)):
            continue
        rows.append(f"- {name}: `{cmd.replace('{junit}', '/tmp/junit.xml')}`")
    if not rows:
        return ""
    return ("\n## Lệnh của repo (gate chạy đúng các lệnh này)\n\n" + "\n".join(rows)
            + "\n\nViết test bằng đúng framework mà lệnh test dùng, theo convention sẵn có.\n")


_HEADING = re.compile(r"^#{1,6}\s*(.+?)\s*$", re.MULTILINE)
_BULLET = re.compile(r"^[-*]\s+(.*)$")


def _plan_section(plan: str, keyword: str) -> str:
    """Nội dung mục có heading chứa `keyword` (không phân biệt hoa thường), hoặc ""."""
    heads = list(_HEADING.finditer(plan or ""))
    for i, m in enumerate(heads):
        if keyword.lower() in m.group(1).lower():
            end = heads[i + 1].start() if i + 1 < len(heads) else len(plan)
            return plan[m.end():end].strip()
    return ""


def _bullets(text: str) -> list[str]:
    """Các dòng `- ...` có nội dung thật; `- không`/`- (không có)` coi là rỗng."""
    out = []
    for raw in (text or "").splitlines():
        # Cắt đúng MỘT dấu gạch đầu dòng. `lstrip("-* ")` cắt theo tập ký tự nên
        # `- **Mới từ discovery:**` ra `Mới từ discovery:**` — mất dấu mở đậm.
        m = _BULLET.match(raw.strip())
        if not m:
            continue
        item = m.group(1).strip()
        if item and item.lower().strip("()") not in ("không", "không có", "none"):
            out.append(item)
    return out


def _tidy_plan(plan: str) -> tuple[str, int]:
    """Bỏ những mục chỉ chứa `- không`. Trả về (plan, số mục đã bỏ).

    planning.md đã dặn bỏ hẳn mục trống, nhưng plan là thứ người phải đọc để duyệt —
    không nên phụ thuộc vào việc agent nhớ lời dặn. Mục chỉ có văn xuôi (Giải pháp)
    giữ nguyên; chỉ mục toàn gạch đầu dòng mà không dòng nào có nội dung mới bị bỏ.
    """
    heads = list(_HEADING.finditer(plan or ""))
    if not heads:
        return (plan or "").strip(), 0
    keep, dropped = [], 0
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(plan)
        body = plan[m.end():end]
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        if lines and all(_BULLET.match(ln) for ln in lines) and not _bullets(body):
            dropped += 1
            continue
        keep.append(plan[m.start():end].rstrip())
    preamble = plan[:heads[0].start()].strip()
    return "\n\n".join(([preamble] if preamble else []) + keep) + "\n", dropped


def _ticket_block(ticket: Ticket) -> str:
    return f"## Ticket {ticket.id}\n\n**{ticket.title}**\n\n{ticket.body}\n"


def _spec_block(spec, for_tests: bool = False) -> str:
    """Spec cho prompt. `for_tests=True` chỉ đưa AC + tái hiện + phạm vi: giả định và
    ngoài-phạm-vi thường nói luôn cách sửa, mà agent viết test không được thấy cách sửa."""
    lines = ["## Spec\n", f"- Mục tiêu: {spec.objective}", f"- Loại: {spec.task_type}",
             f"- Phạm vi: {spec.modules}"]
    lines += [f"- {ac['id']}: {ac['text']}" for ac in spec.acceptance_criteria]
    if spec.repro_steps:
        lines.append("- Tái hiện: " + " → ".join(spec.repro_steps))
    if for_tests:
        return "\n".join(lines) + "\n"
    if spec.assumptions:
        lines.append("\n### Giả định đã chốt (người duyệt plan đã thấy)")
        lines += [f"- {a}" for a in spec.assumptions]
    if spec.out_of_scope:
        lines.append("\n### NGOÀI PHẠM VI — không làm, không đụng")
        lines += [f"- {o}" for o in spec.out_of_scope]
    return "\n".join(lines) + "\n"
