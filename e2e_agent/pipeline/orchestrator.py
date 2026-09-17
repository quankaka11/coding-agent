"""Vòng quét label. Một ticket mỗi lần (giới hạn MVP).

`agent:try`            → Phase A
`agent:plan-approved`  → tạo ticket chi tiết, gán `agent:impl`
`agent:impl`           → Phase B
"""
from __future__ import annotations

import secrets
from pathlib import Path

from ..agent import spec as spec_mod
from ..config.profile import Profile
from ..core import gitutil
from ..core.reasons import LABEL, Reason
from ..core.run import RunContext, run_context
from ..notify import NullNotifier
from ..notify.base import MR_CREATED, NEEDS_HUMAN, NO_MR, PLAN_READY, Notice
from ..tracker import base as L
from ..tracker.base import Ticket, Tracker
from . import phase_a, phase_b, sandbox

MAX_REJECTS = 2


class Orchestrator:
    def __init__(self, prof: Profile, profile_path: Path, repo: Path, tracker: Tracker,
                 agent, runs_root: Path, work_root: Path, package_root: Path,
                 forge=None, notifier=None, notify_events: list[str] | None = None,
                 log_level: str = "info", console: bool = True) -> None:
        self.prof, self.profile_path, self.repo = prof, profile_path, repo
        self.tracker, self.agent, self.forge = tracker, agent, forge
        self.notifier = notifier or NullNotifier()
        self.notify_events = notify_events or [PLAN_READY, NEEDS_HUMAN, MR_CREATED]
        self.runs_root, self.work_root, self.package_root = runs_root, work_root, package_root
        self.log_level, self.console = log_level, console

    # -- quét ------------------------------------------------------------
    def scan_once(self) -> list[tuple[str, str]]:
        """Xử lý nhiều nhất một ticket cho mỗi trạng thái. Trả về [(ticket, kết cục)]."""
        done: list[tuple[str, str]] = []
        for ticket in self.tracker.list_by_label(L.PLAN_APPROVED)[:1]:
            done.append((ticket.id, self.promote(ticket)))
        for ticket in self.tracker.list_by_label(L.TRY)[:1]:
            done.append((ticket.id, self.run_phase_a(ticket).value))
        for ticket in self.tracker.list_by_label(L.IMPL)[:1]:
            done.append((ticket.id, self.run_phase_b(ticket).value))
        return done

    # -- Phase A ---------------------------------------------------------
    def run_phase_a(self, ticket: Ticket) -> Reason:
        with self._ctx(ticket, "A") as ctx:
            self.tracker.set_state(ticket.id, L.RUNNING)
            out = phase_a.run(ctx, self.prof, self.repo, ticket, self.agent)
            base_sha = gitutil.head_sha(self.repo)
            self.tracker.comment(ticket.id, _plan_comment(out["plan"], out["spec"], base_sha))
            self.tracker.set_state(ticket.id, L.PLAN_READY)
            self.tracker.notify(ticket.id, _notice(ticket))
            self._announce(ctx, PLAN_READY, "Plan chờ duyệt", ticket,
                           f"**{ticket.title}**\n\nĐổi category sang `{L.PLAN_APPROVED}` để duyệt, "
                           f"hoặc `{L.PLAN_REJECTED}` kèm comment lý do.\nChưa dòng code nào bị sửa.")
            ctx.decide(Reason.OK, "plan đã sẵn sàng, chờ người duyệt",
                       label=L.PLAN_READY, base_sha=base_sha[:8])
        self._finalize(ticket, ctx, skip_label=ctx.outcome is Reason.OK)
        return ctx.outcome

    # -- người duyệt xong ------------------------------------------------
    def promote(self, ticket: Ticket) -> str:
        """Người duyệt plan → tạo ticket chi tiết, chính nó mới vào Phase B."""
        spec_text, plan = _split_plan_comment(self._last_plan(ticket))
        if not spec_text.strip():
            # Người đặt `agent:plan-approved` lên ticket chưa từng qua Phase A. Không
            # có plan để duyệt, nên nếu cứ tạo ticket chi tiết thì nó rỗng và Phase B
            # sẽ chết ở bước sau — xa chỗ gây lỗi, khó hiểu cho người vận hành.
            with self._ctx(ticket, "A") as ctx:
                ctx.decide(Reason.HUMAN_TICKET_MISROUTED,
                           f"{ticket.id} đang ở `{L.PLAN_APPROVED}` nhưng agent chưa lập plan nào "
                           f"cho ticket này — không có gì để duyệt. Đổi category sang `{L.TRY}` "
                           f"để agent đọc ticket và lập plan trước.")
            self._finalize(ticket, ctx)
            return ctx.outcome.value
        detail = self.tracker.create_ticket(
            title=f"[impl] {ticket.title}",
            body=f"<!-- parent: {ticket.id} -->\n<!-- base_sha: {ticket.base_sha or ''} -->\n\n"
                 f"## Spec\n\n```yaml\n{spec_text}\n```\n\n## Plan đã duyệt\n\n{plan}\n",
            labels=[L.IMPL], parent=ticket.id)
        self.tracker.comment(ticket.id, f"Plan đã duyệt. Ticket chi tiết: **{detail.id}** "
                                        f"{detail.url}\nPhase B sẽ chạy trên ticket đó.")
        self.tracker.set_state(ticket.id, L.RUNNING)
        return f"đã tạo ticket chi tiết {detail.id}"

    def reject(self, ticket: Ticket, why: str) -> Reason:
        """Người từ chối plan. Quá 2 lần thì NO_MR."""
        self.tracker.comment(ticket.id, f"<!-- plan-rejected -->\n**Plan bị từ chối:** {why}")
        count = ticket.reject_count + 1
        if count > MAX_REJECTS:
            self.tracker.set_state(ticket.id, L.NO_MR)
            return Reason.NOMR_PLAN_REJECTED
        self.tracker.set_state(ticket.id, L.PLAN_REJECTED)
        return Reason.OK

    # -- Phase B ---------------------------------------------------------
    def run_phase_b(self, ticket: Ticket) -> Reason:
        with self._ctx(ticket, "B") as ctx:
            # `agent:impl` là trạng thái máy: chỉ ticket chi tiết do promote() sinh ra
            # mới có liên kết về ticket gốc. Thiếu liên kết đó nghĩa là người gán nhầm
            # category — chặn ngay trước khi dựng worktree hoặc gọi agent.
            if not ticket.parent:
                ctx.decide(Reason.HUMAN_TICKET_MISROUTED,
                           f"{ticket.id} đang ở `{L.IMPL}` nhưng không phải ticket chi tiết do "
                           f"agent sinh ra (không có liên kết về ticket gốc). Nếu đây là ticket "
                           f"bạn tự tạo, đổi category sang `{L.TRY}`.")
            self.tracker.set_state(ticket.id, L.RUNNING)
            spec, plan = _read_detail(ctx, ticket)
            base = ticket.base_sha or gitutil.head_sha(self.repo)
            if stale := _stale_files(self.repo, base, spec.modules):
                ctx.decide(Reason.HUMAN_PLAN_STALE,
                           "code trong phạm vi plan đã đổi kể từ lúc duyệt", files=stale[:10])
            branch = f"agent/{ticket.id}-{secrets.token_hex(2)}"
            work = sandbox.create(self.repo, self.work_root, branch, base)
            sandbox.install_guardrails(work, self.profile_path, self.package_root)
            ctx.emit("sandbox.ready", work=str(work), branch=branch, base=base[:8])
            try:
                phase_b.run(ctx, self.prof, self.repo, work, ticket, spec, plan,
                            self.agent, self.forge)
            finally:
                ctx.emit("sandbox.kept", work=str(work),
                         note="giữ lại để người xem hiện trường")
        self._finalize(ticket, ctx)
        return ctx.outcome

    # -- dùng chung ------------------------------------------------------
    def _ctx(self, ticket: Ticket, phase: str):
        run_id = "r-" + secrets.token_hex(3)
        return run_context(run_dir=self.runs_root / ticket.id / run_id, run_id=run_id,
                           task_id=ticket.id, phase=phase,
                           timeout_min=self.prof.limit("run_timeout_min"),
                           cost_cap_usd=self.prof.limit("run_cost_cap_usd"),
                           log_level=self.log_level, console=self.console)

    def _announce(self, ctx: RunContext, event: str, title: str, ticket: Ticket,
                  body: str, url: str = "") -> None:
        """Thông báo ra ngoài. Hỏng thì ghi log và đi tiếp — không làm đổ pipeline."""
        if event not in self.notify_events:
            return
        sent = self.notifier.send(Notice(event=event, title=title, body=body,
                                         url=url or ticket.url))
        ctx.emit("notify.sent" if sent else "notify.skipped",
                 level="info" if sent else "debug", channel_event=event,
                 configured=getattr(self.notifier, "configured", False))

    def _finalize(self, ticket: Ticket, ctx: RunContext, skip_label: bool = False) -> None:
        if not skip_label:
            label = LABEL[ctx.outcome or Reason.ERROR]
            self.tracker.set_state(ticket.id, label)
            # Ticket gốc phải thấy được kết cục, nếu không nó kẹt ở agent:running mãi.
            if ticket.parent:
                self.tracker.set_state(ticket.parent, label)
                self.tracker.comment(ticket.parent,
                                     f"Ticket chi tiết **{ticket.id}** kết thúc: `{ctx.outcome.value}`.")
        from ..core.reasons import explain
        if ctx.outcome is not Reason.OK:
            # `why` là câu duy nhất nói cho người vận hành biết phải làm gì. Nếu chỉ
            # gửi explain() thì thông báo chung chung tới mức phải mở code ra mới hiểu.
            why = f"\n\n{ctx.outcome_why}" if ctx.outcome_why else ""
            self.tracker.comment(ticket.id, f"**{ctx.outcome.value}** — {explain(ctx.outcome)}"
                                            f"{why}\n\nLog: `{ctx.run_dir}`")
            event = NEEDS_HUMAN if LABEL[ctx.outcome] == L.NEEDS_HUMAN else NO_MR
            self._announce(ctx, event, f"{ctx.outcome.value} — {ticket.id}", ticket,
                           f"**{ticket.title}**{why}\n\n{explain(ctx.outcome)}")
        elif ctx.phase == "B":
            self._announce(ctx, MR_CREATED, f"MR đã mở — {ticket.id}", ticket,
                           f"**{ticket.title}**\n\nGate PASS và anti-gaming PASS, chờ review.",
                           url=self._last_mr(ctx) or ticket.url)

    def _last_mr(self, ctx: RunContext) -> str:
        import json
        path = ctx.run_dir / "outcome.json"
        for line in (ctx.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() \
                if (ctx.run_dir / "events.jsonl").is_file() else []:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "mr.created":
                return (event.get("data") or {}).get("url", "")
        return ""

    def _last_plan(self, ticket: Ticket) -> str:
        path = self.runs_root / ticket.id
        runs = sorted(path.glob("*/plan.md"), key=lambda p: p.stat().st_mtime)
        spec = sorted(path.glob("*/spec.yaml"), key=lambda p: p.stat().st_mtime)
        plan_text = runs[-1].read_text(encoding="utf-8") if runs else ""
        spec_text = spec[-1].read_text(encoding="utf-8") if spec else ""
        return f"{spec_text}\n<!--plan-->\n{plan_text}"


def _split_plan_comment(blob: str) -> tuple[str, str]:
    spec_text, _, plan = blob.partition("\n<!--plan-->\n")
    return spec_text.strip(), plan.strip()


def _read_detail(ctx: RunContext, ticket: Ticket) -> tuple[spec_mod.Spec, str]:
    body = ticket.body
    if "```yaml" not in body:
        # Ticket người tạo đã bị chặn từ run_phase_b, nên tới đây thật sự là bất ngờ:
        # promote() luôn ghi khối yaml vào body.
        ctx.decide(Reason.ERROR, "ticket chi tiết do agent sinh ra nhưng thiếu khối spec YAML "
                                 "— nhiều khả năng body bị sửa tay sau khi tạo")
    spec_text = body.split("```yaml", 1)[1].split("```", 1)[0]
    plan = body.split("## Plan đã duyệt", 1)[-1].strip()
    try:
        return spec_mod.parse(spec_text), plan
    except spec_mod.SpecError as exc:
        ctx.decide(Reason.ERROR, f"spec trong ticket chi tiết sai schema: {exc}")


def _stale_files(repo: Path, base: str, modules: list[str]) -> list[str]:
    """Quyết định D1: plan lạc hậu thì trả người, không tự đoán."""
    try:
        changed = gitutil.changed_files(repo, base)
    except RuntimeError:
        return []
    return [f for f in changed if any(f.startswith(m.rstrip("/")) for m in modules)]


def _plan_comment(plan: str, spec, base_sha: str) -> str:
    return (f"## Plan chờ duyệt\n\n**Chưa dòng code nào được sửa.**\n\n"
            f"- Loại task: `{spec.task_type}`\n- Phạm vi: `{spec.modules}`\n"
            f"- Base commit: `{base_sha[:12]}`\n\n---\n\n{plan}\n\n---\n\n"
            f"**Duyệt:** đổi label sang `{L.PLAN_APPROVED}`.\n"
            f"**Từ chối:** đổi label sang `{L.PLAN_REJECTED}` kèm comment lý do "
            f"(quá {MAX_REJECTS} lần từ chối thì ticket chuyển `{L.NO_MR}`).")


def _notice(ticket: Ticket) -> str:
    return (f"@here Plan cho **{ticket.title}** đã sẵn sàng để duyệt. "
            f"Xem comment ngay trên, đổi label để duyệt hoặc từ chối.")
