"""Vòng quét label. Một ticket mỗi lần (giới hạn MVP).

`agent:try`            → Phase A
`agent:plan-approved`  → tạo ticket chi tiết, gán `agent:impl`
`agent:impl`           → Phase B
"""
from __future__ import annotations

import re
import secrets
import time
from pathlib import Path

from ..agent import spec as spec_mod
from ..config.profile import Profile
from ..core import gitutil
from ..core.reasons import LABEL, Kind, Reason, explain
from ..core.run import RunContext, run_context
from ..notify import NullNotifier
from ..notify.base import MR_CREATED, NEEDS_HUMAN, NO_MR, PLAN_READY, Notice
from ..tracker import base as L
from ..tracker.base import Ticket, Tracker
from . import handoff, phase_a, phase_b, sandbox

MAX_REJECTS = 2

#: Mọi comment do agent ghi đều mang dấu này, nên cái KHÔNG mang dấu chắc chắn là
#: của người. Không có dấu thì phải đoán theo nội dung, mà đoán thì sẽ có ngày sai.
AGENT_MARK = "<!-- e2ea:agent -->"
REJECT_MARK = "<!-- e2ea:rejected -->"
DETAIL_MARK = "<!-- e2ea:detail -->"
#: `<!-- e2ea:running <epoch> <run_id> -->` — mốc để biết một run đã bắt đầu từ bao
#: giờ. Không có mốc này thì `agent:running` là trạng thái không có đường ra: tiến
#: trình bị kill là ticket nằm đó vĩnh viễn, và không ai phân biệt được "đang chạy"
#: với "đã chết từ tuần trước".
RUNNING_MARK = "<!-- e2ea:running"
_RUNNING_RE = re.compile(re.escape(RUNNING_MARK) + r"\s+(\d+)\s+(\S+)\s*-->")


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
        #: Ticket đã thử quá số lần cho phép — vòng quét không đụng tới nữa.
        self.skip: set[str] = set()

    # -- quét ------------------------------------------------------------
    def scan_once(self) -> list[tuple[str, str]]:
        """Xử lý nhiều nhất một ticket cho mỗi trạng thái. Trả về [(ticket, kết cục)]."""
        done: list[tuple[str, str]] = []
        # Dọn ticket kẹt trước: chúng không nằm trong bất kỳ hàng đợi nào bên dưới.
        for ticket in self._stuck():
            done.append((ticket.id, self.recover(ticket).value))
        for ticket in self._waiting(L.PLAN_APPROVED):
            done.append((ticket.id, self.promote(ticket)))
        # Người đổi category sang `agent:plan-rejected` là một nước đi hợp lệ mà
        # comment plan đã dặn sẵn. Không quét trạng thái này thì ticket nằm im
        # vĩnh viễn và người duyệt mất đường nói "không".
        for ticket in self._waiting(L.PLAN_REJECTED):
            done.append((ticket.id, self.reject(ticket).value))
        for ticket in self._waiting(L.TRY):
            done.append((ticket.id, self.run_phase_a(ticket).value))
        for ticket in self._waiting(L.IMPL):
            done.append((ticket.id, self.run_phase_b(ticket).value))
        return done

    def _waiting(self, label: str) -> list[Ticket]:
        """Ticket cũ nhất đang chờ ở trạng thái này, bỏ qua ticket đã đầu hàng."""
        return [t for t in self.tracker.list_by_label(label) if t.id not in self.skip][:1]

    # -- ticket kẹt ở agent:running ---------------------------------------
    def _stale_minutes(self) -> float:
        explicit = self.prof.limit("running_stale_min")
        return float(explicit) if explicit else 2.0 * float(self.prof.limit("run_timeout_min"))

    def _stuck(self) -> list[Ticket]:
        """Ticket ở `agent:running` mà không có run nào còn sống đứng sau nó.

        Ticket gốc đang chờ ticket con (đã có DETAIL_MARK, hoặc có con ở
        `agent:impl`) không tính: trạng thái của nó do con báo về khi kết thúc.
        """
        waiting_parents = {t.parent for t in self.tracker.list_by_label(L.IMPL) if t.parent}
        stale_after = self._stale_minutes() * 60
        out: list[Ticket] = []
        for ticket in self.tracker.list_by_label(L.RUNNING):
            if ticket.id in self.skip:
                continue
            comments = self.tracker.comments(ticket.id)
            if not ticket.parent and (ticket.id in waiting_parents
                                      or any(DETAIL_MARK in c for c in comments)):
                continue
            started = _last_running_mark(comments)
            if started is not None and time.time() - started[0] < stale_after:
                continue          # run này có thể vẫn đang chạy ở tiến trình khác
            out.append(ticket)
        return out

    def recover(self, ticket: Ticket) -> Reason:
        """Giao một ticket kẹt cho người, kèm lý do đủ để họ biết nhìn vào đâu."""
        started = _last_running_mark(self.tracker.comments(ticket.id))
        with self._ctx(ticket, "B" if ticket.parent else "A") as ctx:
            ctx.on_finish(lambda c: self._finalize(ticket, c))
            if started is None:
                ctx.decide(Reason.NEEDS_HUMAN,
                           f"{ticket.id} ở `{L.RUNNING}` nhưng không có mốc bắt đầu run nào — "
                           f"có thể do người đặt category tay, hoặc run của phiên bản cũ. "
                           f"Kiểm tra rồi đặt lại `{L.TRY}` (ticket gốc) hoặc `{L.IMPL}` (ticket chi tiết).",
                           kind=Kind.MISROUTED)
            age_min = round((time.time() - started[0]) / 60)
            ctx.decide(Reason.NEEDS_HUMAN, kind=Kind.STALE_RUN, why=
                       f"run `{started[1]}` bắt đầu {age_min} phút trước và chưa chốt kết cục "
                       f"(trần {self._stale_minutes():.0f} phút) — tiến trình nhiều khả năng đã bị "
                       f"kill. Xem `runs/{ticket.id}/{started[1]}/`, rồi đặt lại category để chạy lại.",
                       stale_run=started[1], age_min=age_min)
        return ctx.outcome

    def _mark_running(self, ticket_id: str, run_id: str) -> None:
        self.tracker.set_state(ticket_id, L.RUNNING)
        self._say(ticket_id, f"{RUNNING_MARK} {int(time.time())} {run_id} -->\n"
                             f"Agent bắt đầu xử lý (run `{run_id}`).")

    def _base_sha(self, ctx: RunContext) -> str:
        """Đầu nhánh gốc TRÊN REMOTE. Không có remote thì HEAD local, và nói rõ."""
        branch = self.prof.base_branch
        # Forge biết cách đăng nhập (repo private) thì fetch thẳng bằng URL của nó;
        # không thì trông vào remote `origin` của clone.
        remote = getattr(self.forge, "fetch_url", lambda: None)() or "origin"
        try:
            tip = gitutil.remote_tip(self.repo, branch, remote=remote)
        except (RuntimeError, OSError) as exc:
            tip = None
            ctx.emit("base.fetch_failed", level="warn", branch=branch, error=str(exc)[:200],
                     note="không fetch được — dùng HEAD local, có thể lạc hậu so với remote")
        if tip:
            ctx.emit("base.synced", branch=branch, sha=tip[:8])
            return tip
        head = gitutil.head_sha(self.repo)
        ctx.emit("base.local", level="info", sha=head[:8],
                 note="repo không có remote origin — dùng HEAD local")
        return head

    def give_up(self, ticket_id: str, why: str) -> None:
        """Ngừng thử lại một ticket và giao hẳn cho người.

        Không có bước này thì một ticket hỏng được chạy lại mỗi chu kỳ, mãi mãi:
        tốn tiền, và tệ hơn là hỏng trong im lặng vì mỗi vòng trông như một vòng
        bình thường.
        """
        self.skip.add(ticket_id)
        self.tracker.set_state(ticket_id, L.NEEDS_HUMAN)
        self._say(ticket_id, f"Agent ngừng thử lại ticket này: {why}")

    # -- Phase A ---------------------------------------------------------
    def run_phase_a(self, ticket: Ticket) -> Reason:
        with self._ctx(ticket, "A") as ctx:
            # Phase A xong là `agent:plan-ready`, không phải nhãn mặc định của OK.
            ctx.on_finish(lambda c: self._finalize(ticket, c,
                                                   skip_label=c.outcome is Reason.OK))
            self._mark_running(ticket.id, ctx.run_id)
            rejections = _rejections(self.tracker.comments(ticket.id))
            # Phase A mang tiếng "chỉ đọc" nhưng vẫn chạy với Write/Edit/Bash, và
            # trước đây chạy thẳng trong repo gốc — lời dặn trong skill là lưới duy
            # nhất. Cho nó một worktree riêng: agent giữ nguyên mọi công cụ (Discovery
            # cần chạy thử lệnh), còn repo của người thì không ai chạm tới được.
            base_sha = self._base_sha(ctx)
            work = sandbox.create_detached(
                self.repo, sandbox.under(self.repo, self.work_root) / f"read-{ticket.id}",
                base_sha)
            sandbox.install_guardrails(work, self.profile_path, self.package_root)
            ctx.emit("sandbox.ready", work=str(work), base=base_sha[:8], mode="chỉ đọc")
            try:
                out = phase_a.run(ctx, self.prof, work, ticket, self.agent, rejections)
                if gitutil.is_dirty(work):
                    ctx.emit("phase_a.dirty", level="warn",
                             note="Phase A đã sửa file dù chỉ được phép đọc — thay đổi bị "
                                  "bỏ cùng worktree, repo gốc không hề bị chạm")
            finally:
                sandbox.remove(self.repo, work)
            spec = out["spec"]
            self._say(ticket.id, handoff.pack(
                _plan_comment(out["plan"], spec, base_sha), base_sha=base_sha,
                spec_yaml=spec.dumps(), plan=out["plan"]))
            self.tracker.set_state(ticket.id, L.PLAN_READY)
            self.tracker.notify(ticket.id, _notice(ticket))
            self._announce(ctx, PLAN_READY, "Plan chờ duyệt", ticket,
                           f"**{ticket.title}**\n\nĐổi category sang `{L.PLAN_APPROVED}` để duyệt, "
                           f"hoặc `{L.PLAN_REJECTED}` kèm comment lý do.\nChưa dòng code nào bị sửa.")
            ctx.decide(Reason.OK, "plan đã sẵn sàng, chờ người duyệt",
                       label=L.PLAN_READY, base_sha=base_sha[:8])
        return ctx.outcome

    # -- người duyệt xong ------------------------------------------------
    def promote(self, ticket: Ticket) -> str:
        """Người duyệt plan → tạo ticket chi tiết, chính nó mới vào Phase B."""
        packed = handoff.unpack(self.tracker.comments(ticket.id))
        if packed is None:
            # Người đặt `agent:plan-approved` lên ticket chưa từng qua Phase A. Không
            # có plan để duyệt, nên nếu cứ tạo ticket chi tiết thì nó rỗng và Phase B
            # sẽ chết ở bước sau — xa chỗ gây lỗi, khó hiểu cho người vận hành.
            with self._ctx(ticket, "A") as ctx:
                ctx.on_finish(lambda c: self._finalize(ticket, c))
                ctx.decide(Reason.NEEDS_HUMAN,
                           f"{ticket.id} đang ở `{L.PLAN_APPROVED}` nhưng agent chưa lập plan nào "
                           f"cho ticket này — không có gì để duyệt. Đổi category sang `{L.TRY}` "
                           f"để agent đọc ticket và lập plan trước.", kind=Kind.MISROUTED)
            return ctx.outcome.value
        comments = self.tracker.comments(ticket.id)
        # Chỉ tính ticket chi tiết sinh ra SAU plan mới nhất: plan lạc hậu → agent lập
        # plan lại → plan mới được duyệt phải đẻ được ticket chi tiết mới; còn cùng một
        # plan mà đặt lại `agent:plan-approved` thì vẫn không đẻ thêm.
        last_plan = max((i for i, c in enumerate(comments) if handoff.OPEN in c), default=-1)
        if already := [c for c in comments[last_plan + 1:] if DETAIL_MARK in c]:
            # Đặt lại `agent:plan-approved` lần nữa không được đẻ thêm một ticket
            # chi tiết nữa: hai ticket cùng một plan thì Phase B chạy hai lần và
            # mở hai MR cho cùng một việc.
            self.tracker.set_state(ticket.id, L.RUNNING)
            return f"đã có ticket chi tiết từ trước — {_short(already[-1], 80)}"
        # Rời `agent:plan-approved` TRƯỚC khi tạo ticket con. Đổ giữa hai bước thì
        # ticket gốc ở `agent:running` có con ở `agent:impl` — _stuck() nhận ra cặp
        # này và không đụng. Thứ tự ngược lại để hở một cửa sổ đẻ hai ticket con.
        self.tracker.set_state(ticket.id, L.RUNNING)
        detail = self.tracker.create_ticket(
            title=f"[impl] {ticket.title}",
            body=f"<!-- base_sha: {packed.base_sha} -->\n\n"
                 f"## Spec\n\n```yaml\n{packed.spec_yaml}\n```\n\n"
                 f"## Plan đã duyệt\n\n{packed.plan}\n",
            labels=[L.IMPL], parent=ticket.id)
        self._say(ticket.id, f"{DETAIL_MARK}\nPlan đã duyệt. Ticket chi tiết: **{detail.id}** "
                             f"{detail.url}\nPhase B sẽ chạy trên ticket đó.")
        return f"đã tạo ticket chi tiết {detail.id}"

    def reject(self, ticket: Ticket, why: str = "") -> Reason:
        """Người từ chối plan — qua CLI (`why` truyền thẳng) hoặc qua category.

        Đổi category rồi viết lý do vào comment là đường người thật sự dùng, nên
        nó phải khép kín: đếm số lần, đưa ticket về `agent:try` để agent lập plan
        mới có tính tới lý do, và chỉ dừng hẳn khi quá trần.
        """
        comments = self.tracker.comments(ticket.id)
        why = why or _last_human_comment(comments) or "người không ghi lý do"
        count = sum(1 for c in comments if REJECT_MARK in c) + 1
        self._say(ticket.id, f"{REJECT_MARK}\n**Plan bị từ chối (lần {count}/{MAX_REJECTS}):** {why}")

        if count > MAX_REJECTS:
            with self._ctx(ticket, "A") as ctx:
                ctx.on_finish(lambda c: self._finalize(ticket, c))
                ctx.decide(Reason.NO_MR,
                           f"plan bị từ chối {count} lần, quá trần {MAX_REJECTS} — "
                           f"ticket cần người viết lại cho rõ rồi đặt `{L.TRY}` thủ công",
                           kind=Kind.PLAN_REJECTED,
                           reasons=[_short(r) for r in _rejections(comments) + [why]][:5])
            return ctx.outcome
        self.tracker.set_state(ticket.id, L.TRY)
        return Reason.OK

    # -- Phase B ---------------------------------------------------------
    def run_phase_b(self, ticket: Ticket) -> Reason:
        with self._ctx(ticket, "B") as ctx:
            ctx.on_finish(lambda c: self._finalize(ticket, c))
            # `agent:impl` là trạng thái máy: chỉ ticket chi tiết do promote() sinh ra
            # mới có liên kết về ticket gốc. Thiếu liên kết đó nghĩa là người gán nhầm
            # category — chặn ngay trước khi dựng worktree hoặc gọi agent.
            if not ticket.parent:
                ctx.decide(Reason.NEEDS_HUMAN,
                           f"{ticket.id} đang ở `{L.IMPL}` nhưng không phải ticket chi tiết do "
                           f"agent sinh ra (không có liên kết về ticket gốc). Nếu đây là ticket "
                           f"bạn tự tạo, đổi category sang `{L.TRY}`.", kind=Kind.MISROUTED)
            self._mark_running(ticket.id, ctx.run_id)
            spec, plan = _read_detail(ctx, ticket)
            tip = self._base_sha(ctx)
            base = ticket.base_sha or tip
            # Sửa trên đúng commit người đã duyệt, nhưng so với đầu nhánh gốc HIỆN
            # TẠI để biết phạm vi plan có còn đúng không (quyết định D1).
            if stale := _stale_files(self.repo, base, tip, spec.modules):
                # Plan lạc hậu không cần người: agent lập plan lại trên code mới. Ticket
                # chi tiết này đóng NO_MR, ticket gốc quay về `agent:try` — vòng sau Phase A
                # chạy lại, plan mới lên chờ duyệt như bình thường (điểm duyệt duy nhất R-7).
                ctx.parent_label = L.TRY
                self._say(ticket.parent,
                          f"Code trong `{stale[:5]}` đã đổi trên nhánh gốc sau khi plan được duyệt "
                          f"(`{base[:8]}` → `{tip[:8]}`). Agent sẽ lập plan lại trên code mới; "
                          f"ticket này tự quay về `{L.TRY}`.")
                ctx.decide(Reason.NO_MR,
                           "code trong phạm vi plan đã đổi trên nhánh gốc kể từ lúc duyệt — "
                           f"ticket gốc {ticket.parent} đã đặt lại `{L.TRY}` để agent lập plan mới",
                           kind=Kind.PLAN_STALE, files=stale[:10], base=base[:8], tip=tip[:8])
            # Một tên nhánh duy nhất cho cả worktree lẫn MR. Trước đây Phase B tự
            # đặt lại tên không hậu tố khi push, nên hậu tố ngẫu nhiên mất tác dụng
            # đúng ở chỗ nó cần có: lần chạy sau đè lên nhánh của MR đang mở.
            branch = f"agent/{ticket.id}-{secrets.token_hex(2)}"
            work = sandbox.create(self.repo, self.work_root, branch, base)
            sandbox.install_guardrails(work, self.profile_path, self.package_root)
            ctx.emit("sandbox.ready", work=str(work), branch=branch, base=base[:8])
            try:
                phase_b.run(ctx, self.prof, self.repo, work, ticket, spec, plan,
                            self.agent, self.forge, branch)
            finally:
                ctx.emit("sandbox.kept", work=str(work),
                         note="giữ lại để người xem hiện trường")
        return ctx.outcome

    # -- dùng chung ------------------------------------------------------
    def _ctx(self, ticket: Ticket, phase: str):
        run_id = "r-" + secrets.token_hex(3)
        return run_context(run_dir=self.runs_root / ticket.id / run_id, run_id=run_id,
                           task_id=ticket.id, phase=phase,
                           timeout_min=self.prof.limit("run_timeout_min"),
                           cost_cap_usd=self.prof.limit("run_cost_cap_usd"),
                           log_level=self.log_level, console=self.console)

    def _say(self, ticket_id: str, body: str) -> None:
        """Mọi comment do agent ghi đều mang dấu, để phân biệt với lời của người."""
        self.tracker.comment(ticket_id, f"{AGENT_MARK}\n{body}")

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
                self.tracker.set_state(ticket.parent, ctx.parent_label or label)
                self._say(ticket.parent,
                          f"Ticket chi tiết **{ticket.id}** kết thúc: `{_tag(ctx)}`.")
        if ctx.outcome is not Reason.OK:
            # `why` là câu duy nhất nói cho người vận hành biết phải làm gì. Nếu chỉ
            # gửi explain() thì thông báo chung chung tới mức phải mở code ra mới hiểu.
            why = f"\n\n{ctx.outcome_why}" if ctx.outcome_why else ""
            meaning = explain(ctx.outcome, ctx.outcome_kind)
            self._say(ticket.id, f"**{_tag(ctx)}** — {meaning}{why}\n\nLog: `{ctx.run_dir}`")
            event = NEEDS_HUMAN if LABEL[ctx.outcome] == L.NEEDS_HUMAN else NO_MR
            self._announce(ctx, event, f"{_tag(ctx)} — {ticket.id}", ticket,
                           f"**{ticket.title}**{why}\n\n{meaning}")
        elif ctx.phase == "B":
            self._announce(ctx, MR_CREATED, f"MR đã mở — {ticket.id}", ticket,
                           f"**{ticket.title}**\n\nGate PASS và anti-gaming PASS, chờ review.",
                           url=self._last_mr(ctx) or ticket.url)

    def _last_mr(self, ctx: RunContext) -> str:
        import json
        for line in (ctx.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() \
                if (ctx.run_dir / "events.jsonl").is_file() else []:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "mr.created":
                return (event.get("data") or {}).get("url", "")
        return ""


def _tag(ctx: RunContext) -> str:
    """`NO_MR/plan_stale` — kết cục kèm chi tiết, cho comment và thông báo."""
    return f"{ctx.outcome.value}/{ctx.outcome_kind}" if ctx.outcome_kind else ctx.outcome.value


def _last_human_comment(comments: list[str]) -> str:
    for text in reversed(comments):
        if AGENT_MARK not in text and handoff.OPEN not in text:
            return text.strip()
    return ""


def _rejections(comments: list[str]) -> list[str]:
    """Các lý do từ chối đã ghi, cũ → mới. Phase A phải thấy để khỏi lập lại y hệt."""
    return [c.split("**", 2)[-1].strip() if "**" in c else c.strip()
            for c in comments if REJECT_MARK in c]


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


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


def _last_running_mark(comments: list[str]) -> tuple[float, str] | None:
    """(epoch bắt đầu, run_id) của mốc running MỚI NHẤT, hoặc None."""
    for text in reversed(comments):
        if m := _RUNNING_RE.search(text):
            return float(m.group(1)), m.group(2)
    return None


def _stale_files(repo: Path, base: str, tip: str, modules: list[str]) -> list[str]:
    """Quyết định D1: plan lạc hậu thì trả người, không tự đoán."""
    if base == tip:
        return []
    try:
        changed = gitutil.changed_files(repo, base, tip)
    except RuntimeError:
        return []
    return [f for f in changed if any(f.startswith(m.rstrip("/")) for m in modules)]


def _plan_comment(plan: str, spec, base_sha: str) -> str:
    assumed = "\n".join(f"- {a}" for a in spec.assumptions) or "- (ticket đủ rõ, không có)"
    skipped = "\n".join(f"- {o}" for o in spec.out_of_scope) or "- (không có)"
    return (f"## Plan chờ duyệt\n\n**Chưa dòng code nào được sửa.**\n\n"
            f"- Loại task: `{spec.task_type}`\n- Phạm vi: `{spec.modules}`\n"
            f"- Base commit: `{base_sha[:12]}`\n\n"
            f"**Agent đã tự chốt các giả định sau thay vì hỏi lại — sai thì từ chối plan kèm "
            f"câu trả lời:**\n{assumed}\n\n"
            f"**Thấy nhưng KHÔNG làm (ngoài ticket):**\n{skipped}\n\n---\n\n{plan}\n\n---\n\n"
            f"**Duyệt:** đổi label sang `{L.PLAN_APPROVED}`.\n"
            f"**Từ chối:** đổi label sang `{L.PLAN_REJECTED}` kèm comment lý do "
            f"(quá {MAX_REJECTS} lần từ chối thì ticket chuyển `{L.NO_MR}`).")


def _notice(ticket: Ticket) -> str:
    return (f"@here Plan cho **{ticket.title}** đã sẵn sàng để duyệt. "
            f"Xem comment ngay trên, đổi label để duyệt hoặc từ chối.")
