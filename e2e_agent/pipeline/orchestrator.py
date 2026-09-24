"""Vòng quét label. Mọi thứ diễn ra trên MỘT ticket — ticket người đã viết.

`agent:try`            → Phase A → `agent:plan-ready` (hoặc tự duyệt, xem auto_approve)
`agent:plan-approved`  → Phase B ngay trên ticket đó → MR
`agent:impl`           → Phase B trên ticket chi tiết của phiên bản cũ (chỉ để tương thích)

Trước đây duyệt plan đẻ ra một ticket `[impl]` con rồi Phase B chạy trên con. Plan và
spec đã nằm sẵn trong comment bàn giao (handoff) của chính ticket gốc, nên ticket con
chỉ thêm một thứ cho người dùng Backlog phải hiểu.

`limits.max_parallel` > 1: các run chạy song song trong thread, mỗi run một worktree.
Thao tác git trên repo gốc (fetch, thêm/xoá worktree) đi qua một khoá chung.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from ..agent import spec as spec_mod
from ..config.profile import Profile
from ..core import gitutil, secrets as secrets_mod
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
#: Comment link MR — người dùng Backlog thấy MR ngay trên ticket, và là mốc chống mở
#: MR thứ hai cho cùng một plan khi ai đó đặt lại `agent:plan-approved`.
MR_MARK = "<!-- e2ea:mr -->"
AUTO_APPROVE_MARK = "<!-- e2ea:auto-approved -->"
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
        # Tên biến token do hồ sơ khai cũng phải bị gỡ khỏi môi trường của agent và lệnh gate.
        secrets_mod.protect(prof.tracker_cfg("api_key_env"), prof.tracker_cfg("token_env"),
                            prof.forge_cfg("token_env"), prof.notify_cfg("url_env"))
        self.max_parallel = max(1, int(prof.limit("max_parallel")))
        #: fetch ghi FETCH_HEAD, `git worktree add/remove` sửa metadata — trên CÙNG một
        #: repo gốc thì hai thread không được làm cùng lúc.
        self._git_lock = threading.RLock()
        self._pool: ThreadPoolExecutor | None = None
        self._inflight: dict[str, Future] = {}
        #: Lỗi ngoài RunContext của các run chạy trong thread — watcher đọc rồi ghi log.
        self.errors: list[str] = []

    # -- quét ------------------------------------------------------------
    def scan_once(self, wait: bool = True) -> list[tuple[str, str]]:
        """Một vòng quét. Trả về [(ticket, kết cục)] của những việc đã XONG.

        max_parallel = 1: mỗi trạng thái nhiều nhất một ticket, chạy tuần tự (như cũ).
        max_parallel > 1: việc nặng (Phase A/B) đẩy vào thread pool tới khi đủ chỗ.
        `wait=False` (vòng watch) trả ngay, việc đang chạy báo về ở vòng sau;
        `wait=True` (scan một lần, test) đợi mọi việc xong.
        """
        done: list[tuple[str, str]] = []
        # Dọn ticket kẹt trước: chúng không nằm trong bất kỳ hàng đợi nào bên dưới.
        for ticket in self._stuck():
            done.append((ticket.id, self.recover(ticket).value))
        # Người đổi category sang `agent:plan-rejected` là một nước đi hợp lệ mà
        # comment plan đã dặn sẵn. Không quét trạng thái này thì ticket nằm im
        # vĩnh viễn và người duyệt mất đường nói "không".
        for ticket in self._waiting(L.PLAN_REJECTED):
            done.append((ticket.id, self.reject(ticket).value))

        slots = self.max_parallel - len(self._inflight) if self.max_parallel > 1 else 1
        jobs: list[tuple[Ticket, object]] = []
        # Phase B trước Phase A: ticket đã được duyệt là việc người đang chờ kết quả.
        for label, fn in ((L.PLAN_APPROVED, self.run_phase_b), (L.IMPL, self.run_phase_b),
                          (L.TRY, self.run_phase_a)):
            if self.max_parallel == 1:
                jobs += [(t, fn) for t in self._waiting(label)]
            elif len(jobs) < slots:
                jobs += [(t, fn) for t in self._waiting(label, slots - len(jobs))]

        if self.max_parallel == 1:
            for ticket, fn in jobs:
                done.append((ticket.id, _value(fn(ticket))))
            return done

        self._pool = self._pool or ThreadPoolExecutor(self.max_parallel, thread_name_prefix="e2ea-run")
        for ticket, fn in jobs:
            self._inflight[ticket.id] = self._pool.submit(fn, ticket)
        return done + self._collect(block=wait)

    def drain(self) -> list[tuple[str, str]]:
        """Đợi mọi run đang chạy xong rồi đóng pool. Gọi khi vòng watch dừng."""
        out = self._collect(block=True)
        if self._pool:
            self._pool.shutdown(wait=True)
            self._pool = None
        return out

    def _collect(self, block: bool) -> list[tuple[str, str]]:
        out = []
        for ticket_id, fut in list(self._inflight.items()):
            if not block and not fut.done():
                continue
            try:
                out.append((ticket_id, _value(fut.result())))
            except Exception as exc:        # run_* tự bắt lỗi; tới đây là lỗi ngoài ctx
                out.append((ticket_id, Reason.ERROR.value))
                self.errors.append(f"{ticket_id}: {type(exc).__name__}: {exc}")
            self._inflight.pop(ticket_id, None)
        return out

    def _waiting(self, label: str, n: int = 1) -> list[Ticket]:
        """n ticket cũ nhất đang chờ ở trạng thái này, bỏ qua ticket đã đầu hàng/đang chạy."""
        return [t for t in self.tracker.list_by_label(label)
                if t.id not in self.skip and t.id not in self._inflight][:n]

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
            if ticket.id in self.skip or ticket.id in self._inflight:
                continue          # đang chạy trong pool của chính tiến trình này
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
            with self._git_lock:
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
            with self._git_lock:
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
                with self._git_lock:
                    sandbox.remove(self.repo, work)
            spec = out["spec"]
            self._say(ticket.id, handoff.pack(
                _plan_comment(out["plan"], spec, base_sha, self.prof), base_sha=base_sha,
                spec_yaml=spec.dumps(), plan=out["plan"]))
            if spec.task_type in self.prof.auto_approve:
                # Tự duyệt theo hồ sơ: người vẫn đọc được plan ngay trên ticket, và vẫn
                # còn chốt chặn ở bước review MR.
                self._say(ticket.id, f"{AUTO_APPROVE_MARK}\nPlan loại `{spec.task_type}` được tự "
                                     f"duyệt theo hồ sơ repo (`conventions.auto_approve`). Agent "
                                     f"bắt đầu viết code ở vòng quét tới.")
                self.tracker.set_state(ticket.id, L.PLAN_APPROVED)
                ctx.decide(Reason.OK, "plan đã tự duyệt theo hồ sơ, chờ Phase B",
                           label=L.PLAN_APPROVED, base_sha=base_sha[:8], auto_approved=True)
            self.tracker.set_state(ticket.id, L.PLAN_READY)
            self.tracker.notify(ticket.id, _notice(ticket))
            self._announce(ctx, PLAN_READY, "Plan chờ duyệt", ticket,
                           f"**{ticket.title}**\n\nĐổi category sang `{L.PLAN_APPROVED}` để duyệt, "
                           f"hoặc `{L.PLAN_REJECTED}` kèm comment lý do.\nChưa có dòng code nào được thay đổi.")
            ctx.decide(Reason.OK, "plan đã sẵn sàng, chờ người duyệt",
                       label=L.PLAN_READY, base_sha=base_sha[:8])
        return ctx.outcome

    # -- người duyệt xong ------------------------------------------------
    def approve(self, ticket: Ticket) -> str:
        """Duyệt plan qua CLI: chỉ đổi category, vòng quét sau chạy Phase B.

        Cùng một đường với người đổi category trên Backlog hay bấm Duyệt trên web —
        không có đường tắt nào chạy Phase B mà bỏ qua kiểm tra của run_phase_b().
        """
        if handoff.unpack(self.tracker.comments(ticket.id)) is None:
            return (f"{ticket.id} chưa có plan nào để duyệt — đặt `{L.TRY}` để agent lập plan trước")
        self.tracker.set_state(ticket.id, L.PLAN_APPROVED)
        return f"{ticket.id} → `{L.PLAN_APPROVED}`; Phase B chạy ở vòng quét tới"

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
        comments = [] if ticket.parent else self.tracker.comments(ticket.id)
        packed = None if ticket.parent else handoff.unpack(comments)
        if packed is not None and _mr_after_plan(comments):
            # Đặt lại `agent:plan-approved` trên plan đã ra MR: không mở MR thứ hai.
            self.tracker.set_state(ticket.id, L.MR_CREATED)
            return Reason.OK
        with self._ctx(ticket, "B") as ctx:
            ctx.on_finish(lambda c: self._finalize(ticket, c))
            if ticket.parent:
                # Ticket chi tiết `[impl]` của phiên bản cũ: spec/plan nằm trong body.
                spec, plan = _read_detail(ctx, ticket)
                approved_base = ticket.base_sha
            elif packed is None:
                ctx.decide(Reason.NEEDS_HUMAN,
                           f"{ticket.id} đang ở `{L.PLAN_APPROVED}`/`{L.IMPL}` nhưng agent chưa lập "
                           f"plan nào cho ticket này — không có gì để duyệt. Đổi category sang "
                           f"`{L.TRY}` để agent đọc ticket và lập plan trước.", kind=Kind.MISROUTED)
            else:
                try:
                    spec = spec_mod.parse(packed.spec_yaml)
                except spec_mod.SpecError as exc:
                    ctx.decide(Reason.ERROR, f"spec trong comment bàn giao sai schema: {exc}")
                plan, approved_base = packed.plan, packed.base_sha
            self._mark_running(ticket.id, ctx.run_id)
            tip = self._base_sha(ctx)
            base = approved_base or tip
            # Sửa trên đúng commit người đã duyệt, nhưng so với đầu nhánh gốc HIỆN
            # TẠI để biết phạm vi plan có còn đúng không (quyết định D1).
            if stale := _stale_files(self.repo, base, tip, spec.modules):
                # Plan lạc hậu không cần người: agent lập plan lại trên code mới. Ticket
                # quay về `agent:try` — vòng sau Phase A chạy lại, plan mới lên chờ duyệt
                # như bình thường (điểm duyệt duy nhất R-7).
                root = ticket.parent or ticket.id
                why = (f"code trong `{stale[:5]}` đã đổi trên nhánh gốc sau khi plan được duyệt "
                       f"(`{base[:8]}` → `{tip[:8]}`) — {root} đã đặt lại `{L.TRY}` để agent "
                       f"lập plan mới trên code mới")
                if ticket.parent:
                    ctx.parent_label = L.TRY
                    ctx.decide(Reason.NO_MR, why, kind=Kind.PLAN_STALE,
                               files=stale[:10], base=base[:8], tip=tip[:8])
                ctx.decide(Reason.NO_MR, why, kind=Kind.PLAN_STALE, label=L.TRY,
                           files=stale[:10], base=base[:8], tip=tip[:8])
            # Một tên nhánh duy nhất cho cả worktree lẫn MR. Trước đây Phase B tự
            # đặt lại tên không hậu tố khi push, nên hậu tố ngẫu nhiên mất tác dụng
            # đúng ở chỗ nó cần có: lần chạy sau đè lên nhánh của MR đang mở.
            branch = f"agent/{ticket.id}-{secrets.token_hex(2)}"
            with self._git_lock:
                work = sandbox.create(self.repo, self.work_root, branch, base)
            sandbox.install_guardrails(work, self.profile_path, self.package_root)
            ctx.emit("sandbox.ready", work=str(work), branch=branch, base=base[:8])
            try:
                phase_b.run(ctx, self.prof, self.repo, work, ticket, spec, plan,
                            self.agent, self.forge, branch)
            finally:
                if ctx.outcome is Reason.OK:
                    # Đã ra MR: code nằm trên nhánh (remote hoặc nhánh local của forge
                    # offline), worktree không còn gì để xem. Giữ lại chỉ làm đầy đĩa.
                    with self._git_lock:
                        sandbox.remove(self.repo, work)
                    ctx.emit("sandbox.removed", work=str(work))
                else:
                    ctx.emit("sandbox.kept", work=str(work),
                             note="giữ lại để người xem hiện trường — `e2ea clean` dọn sau")
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
            # decide(label=…) ghi đè nhãn mặc định (vd plan lạc hậu → quay về agent:try).
            label = ctx.outcome_label or LABEL[ctx.outcome or Reason.ERROR]
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
            if ctx.outcome_kind == Kind.PLAN_STALE.value:
                return          # tự lập plan lại, không phải việc của người
            event = NEEDS_HUMAN if LABEL[ctx.outcome] == L.NEEDS_HUMAN else NO_MR
            self._announce(ctx, event, f"{_tag(ctx)} — {ticket.id}", ticket,
                           f"**{ticket.title}**{why}\n\n{meaning}")
        elif ctx.phase == "B":
            mr = self._last_mr(ctx)
            url, draft = mr.get("url", ""), bool(mr.get("draft"))
            kind = "MR Draft" if draft else "MR"
            if url:
                self._say(ticket.parent or ticket.id,
                          f"{MR_MARK}\n**{kind} đã mở:** {url}" + (
                              "\n\nDraft vì không có bằng chứng test đầy đủ — lý do ghi ở đầu mô tả MR."
                              if draft else ""))
            self._announce(ctx, MR_CREATED, f"{kind} đã mở — {ticket.id}", ticket,
                           f"**{ticket.title}**\n\n" + (
                               "Gate PASS; chưa đủ bằng chứng test nên mở dạng Draft, cần review kỹ."
                               if draft else "Gate PASS và anti-gaming PASS, chờ review."),
                           url=url or ticket.url)

    def _last_mr(self, ctx: RunContext) -> dict:
        import json
        for line in (ctx.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() \
                if (ctx.run_dir / "events.jsonl").is_file() else []:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "mr.created":
                return event.get("data") or {}
        return {}


def _value(outcome) -> str:
    return outcome.value if isinstance(outcome, Reason) else str(outcome)


def _mr_after_plan(comments: list[str]) -> bool:
    """Đã có comment link MR sau comment plan MỚI NHẤT chưa."""
    last_plan = max((i for i, c in enumerate(comments) if handoff.OPEN in c), default=-1)
    return any(MR_MARK in c for c in comments[last_plan + 1:])


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


def _plan_comment(plan: str, spec, base_sha: str, prof: Profile | None = None) -> str:
    # Khối rỗng thì bỏ hẳn, không in "(không có)": đây là thứ người phải đọc để
    # quyết duyệt hay không, mỗi dòng thừa là một dòng họ đọc để biết là không có gì.
    blocks = "".join(
        f"**{title}**\n" + "\n".join(f"- {x}" for x in items) + "\n\n"
        for title, items in (
            ("Các giả định Agent đã tự chốt", spec.assumptions),
            ("Đã thấy nhưng KHÔNG thực hiện (ngoài ticket)", spec.out_of_scope),
            ("⚠ Ticket chưa đạt đủ tiêu chí sẵn sàng (chế độ relaxed vẫn làm, MR có thể là Draft)",
             [f"{k}: {(spec.readiness_notes or {}).get(k, 'không ghi lý do')}"
              for k in spec.soft_gaps(True)] if prof is None or prof.relaxed else []))
        if items)
    if spec.task_type == "T4":
        blocks = ("**⚠ Loại T4:** không kiểm chứng được bằng test viết trước — agent vẫn làm, "
                  "gate vẫn phải xanh, MR sẽ mở dạng **Draft**.\n\n") + blocks
    modules = ", ".join(f"`{m}`" for m in spec.modules) or "—"
    return (f"## Plan chờ duyệt\n\n**Chưa có dòng code nào được thay đổi.**\n\n"
            f"- Loại task: `{spec.task_type}`\n- Phạm vi thay đổi: {modules}\n"
            f"- Base commit: `{base_sha[:12]}`\n\n"
            f"{blocks}---\n\n{plan}\n\n---\n\n"
            f"**Phê duyệt:** đổi label sang `{L.PLAN_APPROVED}` — agent viết code và mở MR "
            f"ngay trên ticket này.\n"
            f"**Từ chối kế hoạch:** đổi label sang `{L.PLAN_REJECTED}` kèm comment lý do "
            f"(quá {MAX_REJECTS} lần từ chối thì ticket chuyển `{L.NO_MR}`).")


def _notice(ticket: Ticket) -> str:
    return (f"@here Plan cho **{ticket.title}** đã sẵn sàng để duyệt. "
            f"Xem comment ngay trên, đổi label để duyệt hoặc từ chối.")
