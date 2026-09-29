"""Vòng quét trạng thái. Mọi thứ diễn ra trên MỘT ticket — ticket người đã viết.

Tên dưới đây là trạng thái logic; tracker quyết định thể hiện nó ra sao (label GitLab,
status hay category Backlog). Câu viết cho người đi qua `tracker.words()` để hiện đúng
tên người thấy trên tracker.

Trạng thái chỉ nói TỚI LƯỢT AI: `agent:try` (agent), `agent:running` (agent đang chạy), các
trạng thái còn lại là lượt người. Người xong phần mình — duyệt plan, trả lời câu hỏi, sửa
token/hồ sơ, góp ý MR — thì chuyển `agent:try`, kèm comment nếu muốn. Làm gì tiếp do
`route()` suy ra từ lịch sử comment của ticket, người không phải chọn đúng trạng thái:

  chưa có plan / plan bị từ chối / plan lạc hậu  → Phase A (lập plan)
  có plan, chưa duyệt, không comment mới         → duyệt plan → Phase B
  có plan, chưa duyệt, có comment của người      → sửa plan theo comment; không mở rộng
                                                   phạm vi thì tự duyệt, có thì đưa duyệt lại
  plan đã duyệt, chưa có MR                      → Phase B, làm tiếp từ bước dừng (checkpoint)
  đã có MR, có comment mới                       → làm tiếp trên nhánh MR, cập nhật MR
  đã có MR, không comment mới                    → không có gì để làm

`agent:plan-approved`/`agent:plan-rejected`/`agent:impl` là trạng thái của phiên bản cũ —
vẫn đọc được để ticket đang dở không bị kẹt, nhưng hệ thống không đặt chúng nữa.

Trước đây duyệt plan đẻ ra một ticket `[impl]` con rồi Phase B chạy trên con. Plan và
spec đã nằm sẵn trong comment bàn giao (handoff) của chính ticket gốc, nên ticket con
chỉ thêm một thứ cho người dùng Backlog phải hiểu.

`limits.max_parallel` > 1: các run chạy song song trong thread, mỗi run một worktree.
Thao tác git trên repo gốc (fetch, thêm/xoá worktree) đi qua một khoá chung.
"""
from __future__ import annotations

import copy
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
from . import checkpoint, handoff, phase_a, phase_b, sandbox

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
#: Plan được duyệt: người chuyển `agent:try` ngay sau plan, bấm Duyệt trên web, hoặc
#: `e2ea approve`. Dấu này là thứ `route()` dựa vào để không lập lại plan lần sau.
APPROVE_MARK = "<!-- e2ea:approved -->"
#: Plan lạc hậu (code trong phạm vi đổi trên nhánh gốc sau khi duyệt) → lần sau lập plan mới.
STALE_MARK = "<!-- e2ea:plan-stale -->"
#: Comment thông báo "plan đã sẵn sàng". Trước đây nó được ghi KHÔNG có dấu agent, nên bị
#: đọc như lời của người: agent coi nó là câu trả lời, và khi người từ chối plan mà không
#: ghi lý do thì chính câu thông báo này thành "lý do từ chối".
NOTICE_MARK = "<!-- e2ea:notice -->"
#: `<!-- e2ea:running <epoch> <run_id> -->` — mốc để biết một run đã bắt đầu từ bao
#: giờ. Không có mốc này thì `agent:running` là trạng thái không có đường ra: tiến
#: trình bị kill là ticket nằm đó vĩnh viễn, và không ai phân biệt được "đang chạy"
#: với "đã chết từ tuần trước".
RUNNING_MARK = "<!-- e2ea:running"
#: `<!-- e2ea:running <epoch> <run_id> [A|B] -->`. Phase để `route()` biết Phase B đã từng
#: chạy trên plan này (tức plan đã được duyệt); mốc của phiên bản cũ không có phase.
_RUNNING_RE = re.compile(re.escape(RUNNING_MARK) + r"\s+(\d+)\s+(\S+?)(?:\s+([AB]))?\s*-->")


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
        #: ticket → run đã đặt nó sang `agent:running`. Lúc run kết thúc, ticket phải còn ở
        #: `agent:running`: người đã kéo nó đi chỗ khác giữa chừng thì không ghi đè lựa chọn đó.
        self._running: dict[str, str] = {}
        self._profile_mtime = _mtime(profile_path)

    def refresh_profile(self) -> bool:
        """Hồ sơ repo sửa trên giao diện (hoặc tay) → vòng quét sau dùng bản mới.

        Trước đây hồ sơ nạp một lần lúc bật vòng quét: người thêm `allowed_paths` rồi chuyển
        ticket về cho agent vẫn bị chặn y như cũ, vì run đọc bản trong bộ nhớ. Chỉ nạp lại
        `prof`; tracker/forge/agent (URL, project, model) vẫn cần khởi động lại. Token thì
        không: tracker/forge đọc lại token mỗi lần gọi.
        """
        from ..config import profile as profile_mod
        mtime = _mtime(self.profile_path)
        if mtime == self._profile_mtime:
            return False
        try:
            prof = profile_mod.load(self.profile_path)
        except (profile_mod.ProfileError, OSError) as exc:
            # Hồ sơ đang sửa dở mà sai: chạy tiếp bằng bản cũ còn hơn dừng cả vòng quét.
            self.errors.append(f"hồ sơ repo mới không hợp lệ, vẫn dùng bản cũ: {exc}")
            self._profile_mtime = mtime
            return False
        self.prof, self._profile_mtime = prof, mtime
        secrets_mod.protect(prof.tracker_cfg("api_key_env"), prof.tracker_cfg("token_env"),
                            prof.forge_cfg("token_env"), prof.notify_cfg("url_env"))
        self.max_parallel = max(1, int(prof.limit("max_parallel")))
        return True

    # -- quét ------------------------------------------------------------
    def scan_once(self, wait: bool = True) -> list[tuple[str, str]]:
        """Một vòng quét. Trả về [(ticket, kết cục)] của những việc đã XONG.

        max_parallel = 1: mỗi trạng thái nhiều nhất một ticket, chạy tuần tự (như cũ).
        max_parallel > 1: việc nặng (Phase A/B) đẩy vào thread pool tới khi đủ chỗ.
        `wait=False` (vòng watch) trả ngay, việc đang chạy báo về ở vòng sau;
        `wait=True` (scan một lần, test) đợi mọi việc xong.
        """
        done: list[tuple[str, str]] = []
        self.refresh_profile()
        # Dọn ticket kẹt trước: chúng không nằm trong bất kỳ hàng đợi nào bên dưới.
        for ticket in self._stuck():
            done.append((ticket.id, self.recover(ticket).value))
        # `agent:plan-rejected` của phiên bản cũ: ticket đang nằm đó vẫn phải có đường ra.
        for ticket in self._waiting(L.PLAN_REJECTED):
            done.append((ticket.id, self.reject(ticket).value))

        slots = self.max_parallel - len(self._inflight) if self.max_parallel > 1 else 1
        jobs: list[tuple[Ticket, object]] = []
        # Hai trạng thái đầu là của phiên bản cũ; `agent:try` là cửa duy nhất hiện nay.
        for label, fn in ((L.PLAN_APPROVED, self.run_phase_b), (L.IMPL, self.run_phase_b),
                          (L.TRY, self.run_turn)):
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
                           f"có thể do người đặt trạng thái này bằng tay, hoặc run của phiên bản cũ. "
                           f"Kiểm tra rồi chuyển `{L.TRY}`.",
                           kind=Kind.MISROUTED)
            age_min = round((time.time() - started[0]) / 60)
            ctx.decide(Reason.NEEDS_HUMAN, kind=Kind.STALE_RUN, why=
                       f"run `{started[1]}` bắt đầu {age_min} phút trước và chưa chốt kết cục "
                       f"(trần {self._stale_minutes():.0f} phút) — tiến trình nhiều khả năng đã bị "
                       f"kill. Xem `runs/{ticket.id}/{started[1]}/`, rồi chuyển `{L.TRY}`: agent làm "
                       f"tiếp từ bước cuối cùng đã xong, không lập lại plan.",
                       stale_run=started[1], age_min=age_min)
        return ctx.outcome

    def _mark_running(self, ticket_id: str, run_id: str, phase: str) -> None:
        self.tracker.set_state(ticket_id, L.RUNNING)
        self._running[ticket_id] = run_id
        self._say(ticket_id, f"{RUNNING_MARK} {int(time.time())} {run_id} {phase} -->\n"
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

    # -- lượt của agent ---------------------------------------------------
    def run_turn(self, ticket: Ticket) -> Reason:
        """Ticket ở `agent:try`: làm bước mà lịch sử ticket chỉ ra (xem `route()`)."""
        if ticket.parent:
            return self.run_phase_b(ticket)       # ticket chi tiết của phiên bản cũ
        comments = self.tracker.comments(ticket.id)
        step = route(comments)
        if step == PLAN:
            # Từ chối trên web không đi qua `reject()`, nên trần số lần kiểm ở đây — đếm từ lần
            # chạm trần gần nhất: người viết lại ticket rồi chuyển về thì được lập plan lại.
            since = _last_index(comments, f"{Reason.NO_MR.value}/{Kind.PLAN_REJECTED.value}") + 1
            if (count := sum(1 for c in comments[since:] if REJECT_MARK in c)) > MAX_REJECTS:
                with self._ctx(ticket, "A") as ctx:
                    ctx.on_finish(lambda c: self._finalize(ticket, c))
                    ctx.decide(Reason.NO_MR,
                               f"plan bị từ chối {count} lần, quá trần {MAX_REJECTS} — ticket cần "
                               f"người viết lại cho rõ rồi chuyển `{L.TRY}`",
                               kind=Kind.PLAN_REJECTED, reasons=[_short(r) for r in _rejections(comments)][:5])
                return ctx.outcome
            return self.run_phase_a(ticket)
        if step == REVISE:
            return self.run_phase_a(ticket, revise=True)
        if step == APPROVE:
            self._say(ticket.id, f"{APPROVE_MARK}\nPlan được duyệt (ticket chuyển sang `{L.TRY}` "
                                 f"ngay sau plan). Agent bắt đầu viết code.")
            return self.run_phase_b(ticket)
        if step == MR_DONE:
            self._say(ticket.id, "MR đã mở cho plan này và chưa có góp ý mới — agent không có gì "
                                 "để làm. Muốn agent sửa MR: viết góp ý trong comment rồi chuyển "
                                 f"`{L.TRY}`.")
            self.tracker.set_state(ticket.id, L.MR_CREATED)
            return Reason.OK
        return self.run_phase_b(ticket, update_mr=step == UPDATE_MR)

    # -- Phase A ---------------------------------------------------------
    def run_phase_a(self, ticket: Ticket, revise: bool = False) -> Reason:
        """Lập plan. `revise=True`: người góp ý trên plan chưa duyệt rồi chuyển ticket về cho
        agent — sửa plan theo góp ý; không mở rộng phạm vi thì tự duyệt và làm tiếp luôn."""
        with self._ctx(ticket, "A") as ctx:
            # Phase A xong là `agent:plan-ready`, không phải nhãn mặc định của OK.
            ctx.on_finish(lambda c: self._finalize(ticket, c,
                                                   skip_label=c.outcome is Reason.OK))
            comments = self.tracker.comments(ticket.id)
            previous = handoff.unpack(comments) if revise else None
            self._mark_running(ticket.id, ctx.run_id, "A")
            rejections = _rejections(comments)
            # Người trả lời câu hỏi của agent bằng comment rồi đặt lại `agent:try`: không
            # đưa comment vào prompt thì agent đọc đúng mô tả cũ và hỏi lại y hệt.
            talk = conversation(comments)
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
                out = phase_a.run(ctx, self.prof, work, ticket, self.agent, rejections, talk,
                                  previous_plan=previous.plan if previous else None)
                if gitutil.is_dirty(work):
                    ctx.emit("phase_a.dirty", level="warn",
                             note="Phase A đã sửa file dù chỉ được phép đọc — thay đổi bị "
                                  "bỏ cùng worktree, repo gốc không hề bị chạm")
            finally:
                with self._git_lock:
                    sandbox.remove(self.repo, work)
            spec = out["spec"]
            wider = _widened(previous, spec) if previous else None
            self._say(ticket.id, handoff.pack(
                _plan_comment(out["plan"], spec, base_sha, self.prof, revised=previous is not None,
                              wider=wider),
                base_sha=base_sha, spec_yaml=spec.dumps(), plan=out["plan"]))
            if previous is not None and not wider:
                # Người đã chuyển ticket về cho agent = đồng ý làm tiếp. Plan sửa theo đúng
                # góp ý đó mà không mở rộng phạm vi thì không bắt người duyệt thêm vòng nữa;
                # MR vẫn là chốt review cuối.
                self._say(ticket.id, f"{APPROVE_MARK}\nPlan đã sửa theo góp ý và không mở rộng "
                                     f"phạm vi so với plan trước — tự duyệt. Agent bắt đầu viết "
                                     f"code ở vòng quét tới.")
                self._release(ctx, ticket.id, L.TRY)
                ctx.decide(Reason.OK, "plan sửa theo góp ý, trong phạm vi cũ — tự duyệt, chờ Phase B",
                           label=L.TRY, base_sha=base_sha[:8], revised=True)
            if spec.task_type in self.prof.auto_approve and not wider:
                # Tự duyệt theo hồ sơ: người vẫn đọc được plan ngay trên ticket, và vẫn
                # còn chốt chặn ở bước review MR.
                self._say(ticket.id, f"{AUTO_APPROVE_MARK}\nPlan loại `{spec.task_type}` được tự "
                                     f"duyệt theo hồ sơ repo (`conventions.auto_approve`). Agent "
                                     f"bắt đầu viết code ở vòng quét tới.")
                self._release(ctx, ticket.id, L.TRY)
                ctx.decide(Reason.OK, "plan đã tự duyệt theo hồ sơ, chờ Phase B",
                           label=L.TRY, base_sha=base_sha[:8], auto_approved=True)
            if self._release(ctx, ticket.id, L.PLAN_READY):
                self.tracker.notify(ticket.id, self._words(f"{AGENT_MARK}\n{NOTICE_MARK}\n{_notice(ticket)}"))
                self._announce(ctx, PLAN_READY, "Plan chờ duyệt", ticket,
                               f"**{ticket.title}**\n\n{_HOW_TO_DECIDE}\nChưa có dòng code nào được thay đổi.")
            ctx.decide(Reason.OK, "plan đã sẵn sàng, chờ người duyệt",
                       label=L.PLAN_READY, base_sha=base_sha[:8])
        return ctx.outcome

    # -- người duyệt xong ------------------------------------------------
    def approve(self, ticket: Ticket) -> str:
        """Duyệt plan qua CLI: ghi dấu duyệt rồi trả ticket về cho agent — vòng quét sau
        chạy Phase B. Cùng một đường với người chuyển trạng thái trên tracker hay bấm Duyệt
        trên web: không có đường tắt nào chạy Phase B mà bỏ qua kiểm tra của run_phase_b()."""
        comments = self.tracker.comments(ticket.id)
        if handoff.unpack(comments) is None:
            return (f"{ticket.id} chưa có plan nào để duyệt — chuyển `{L.TRY}` để agent lập plan trước")
        if route(comments) in (APPROVE, REVISE):
            self._say(ticket.id, f"{APPROVE_MARK}\nPlan được duyệt qua `e2ea approve`.")
        self.tracker.set_state(ticket.id, L.TRY)
        return f"{ticket.id} → `{L.TRY}`; Phase B chạy ở vòng quét tới"

    def reject(self, ticket: Ticket, why: str = "") -> Reason:
        """Người từ chối plan — qua CLI/web (`why` truyền thẳng) hoặc trạng thái cũ
        `agent:plan-rejected`.

        Khép kín: đếm số lần, đưa ticket về `agent:try` để agent lập plan mới có tính tới
        lý do (plan mới luôn lên chờ duyệt lại), và chỉ dừng hẳn khi quá trần.
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
                           f"ticket cần người viết lại cho rõ rồi chuyển `{L.TRY}`",
                           kind=Kind.PLAN_REJECTED,
                           reasons=[_short(r) for r in _rejections(comments) + [why]][:5])
            return ctx.outcome
        self.tracker.set_state(ticket.id, L.TRY)
        return Reason.OK

    # -- Phase B ---------------------------------------------------------
    def run_phase_b(self, ticket: Ticket, update_mr: bool = False) -> Reason:
        comments = [] if ticket.parent else self.tracker.comments(ticket.id)
        packed = None if ticket.parent else handoff.unpack(comments)
        if packed is not None and _mr_after_plan(comments) and not update_mr:
            # Duyệt lại plan đã ra MR (trạng thái cũ `agent:plan-approved`): không mở MR thứ hai.
            self.tracker.set_state(ticket.id, L.MR_CREATED)
            return Reason.OK
        with self._ctx(ticket, "B") as ctx:
            ctx.on_finish(lambda c: self._finalize(ticket, c))
            if ticket.parent:
                # Ticket chi tiết `[impl]` của phiên bản cũ: spec/plan nằm trong body.
                spec, plan = _read_detail(ctx, ticket)
                approved_base = ticket.base_sha
                spec_yaml = spec.dumps()
            elif packed is None:
                ctx.decide(Reason.NEEDS_HUMAN,
                           f"{ticket.id} được đưa vào bước viết code nhưng agent chưa lập plan nào "
                           f"cho ticket này — không có gì để duyệt. Chuyển `{L.TRY}` để agent đọc "
                           f"ticket và lập plan trước.", kind=Kind.MISROUTED)
            else:
                try:
                    spec = spec_mod.parse(packed.spec_yaml)
                except spec_mod.SpecError as exc:
                    ctx.decide(Reason.ERROR, f"spec trong comment bàn giao sai schema: {exc}")
                plan, approved_base, spec_yaml = packed.plan, packed.base_sha, packed.spec_yaml
            self._mark_running(ticket.id, ctx.run_id, "B")
            # Preflight: token forge hỏng thì biết NGAY, trước khi tốn tiền cho agent — không
            # phải tới bước mở MR mới biết.
            if problems := _forge_problems(self.forge):
                ctx.decide(Reason.NEEDS_HUMAN, "preflight forge: " + "; ".join(problems),
                           kind=Kind.FORGE_AUTH, problems=problems)
            tip = self._base_sha(ctx)
            base = approved_base or tip
            # Sửa trên đúng commit người đã duyệt, nhưng so với đầu nhánh gốc HIỆN
            # TẠI để biết phạm vi plan có còn đúng không (quyết định D1).
            if stale := _stale_files(self.repo, base, tip, spec.modules):
                # Plan lạc hậu không cần người: agent lập plan lại trên code mới. Ticket
                # quay về `agent:try` (kèm STALE_MARK) — vòng sau Phase A chạy lại, plan mới
                # lên chờ duyệt như bình thường (điểm duyệt duy nhất R-7).
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

            # Làm tiếp từ checkpoint nếu được; không thì chạy lại từ đầu với plan đã duyệt.
            phash = checkpoint.plan_hash(spec_yaml, plan, base)
            prev = checkpoint.load(self.runs_root, ticket.id)
            ok, why_not = checkpoint.usable(prev, plan=phash, repo=self.repo)
            if update_mr and not (ok and prev.mr):
                ctx.decide(Reason.NEEDS_HUMAN,
                           f"có góp ý mới trên MR nhưng không làm tiếp được trên nhánh của MR "
                           f"({why_not or 'checkpoint không có thông tin MR'}). Sửa thẳng trên MR, "
                           f"hoặc viết lại ticket rồi chuyển `{L.TRY}` để agent lập plan mới.",
                           kind=Kind.MISROUTED)
            if ok:
                # Một tên nhánh cho cả worktree lẫn MR — làm tiếp thì giữ đúng nhánh cũ.
                branch = prev.branch
                with self._git_lock:
                    work = sandbox.create_at(self.repo, self.work_root, branch, prev.head_sha)
                cp = copy.deepcopy(prev)
                cp.run_id = ctx.run_id
            else:
                if prev is not None:
                    ctx.emit("resume.skipped", reason=why_not,
                             note="chạy lại Phase B từ đầu với plan đã duyệt")
                prev = None
                # Hậu tố ngẫu nhiên: lần chạy lại từ đầu không đè lên nhánh của MR đang mở.
                branch = f"agent/{ticket.id}-{secrets.token_hex(2)}"
                with self._git_lock:
                    work = sandbox.create(self.repo, self.work_root, branch, base)
                cp = checkpoint.Checkpoint(ticket=ticket.id, run_id=ctx.run_id, branch=branch,
                                           plan_hash=phash)
            cp.profile_hash = checkpoint.profile_hash(self.profile_path)
            rec = checkpoint.Recorder(self.runs_root, self.repo, cp)
            sandbox.install_guardrails(work, self.profile_path, self.package_root)
            ctx.emit("sandbox.ready", work=str(work), branch=branch, base=base[:8],
                     resumed=prev is not None)
            try:
                phase_b.run(ctx, self.prof, self.repo, work, ticket, spec, plan,
                            self.agent, self.forge, branch,
                            notes=[text for _, text in conversation(comments, since_last_plan=True)],
                            resume=prev, rec=rec)
            finally:
                if ctx.outcome is not Reason.OK:
                    rec.stop(work, ctx.outcome_kind)
                    ctx.resume_hint = _resume_hint(rec.cp)
                if ctx.outcome is Reason.OK:
                    # Đã ra MR: code nằm trên nhánh (remote hoặc nhánh local của forge
                    # offline) và ref checkpoint, worktree không còn gì để xem.
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
        self.tracker.comment(ticket_id, self._words(f"{AGENT_MARK}\n{body}"))

    def _words(self, text: str) -> str:
        words = getattr(self.tracker, "words", None)
        return words(text) if words else text

    def _release(self, ctx: RunContext, ticket_id: str, label: str) -> bool:
        """Trả ticket về trạng thái kế tiếp sau một run — trừ khi người đã đổi nó giữa chừng.

        Run nào đặt `agent:running` thì lúc kết thúc ticket phải còn ở đó. Người kéo ticket
        sang trạng thái khác trong lúc agent chạy (đóng nó, giao lại, tự làm…) là một quyết
        định; ghi đè nó bằng kết cục của run là giẫm lên người.
        """
        if self._running.pop(ticket_id, None) is not None:
            now = self.tracker.get(ticket_id).labels
            if L.RUNNING not in now:
                ctx.emit("tracker.state_kept", level="warn", wanted=label, now=now,
                         note="người đã đổi trạng thái trong lúc agent chạy — không ghi đè")
                self._say(ticket_id, f"Trạng thái ticket đã được đổi trong lúc agent chạy, nên agent "
                                     f"không đặt `{label}` đè lên. Kết quả run vẫn ghi ở dưới.")
                return False
        self.tracker.set_state(ticket_id, label)
        return True

    def _announce(self, ctx: RunContext, event: str, title: str, ticket: Ticket,
                  body: str, url: str = "") -> None:
        """Thông báo ra ngoài. Hỏng thì ghi log và đi tiếp — không làm đổ pipeline."""
        if event not in self.notify_events:
            return
        sent = self.notifier.send(Notice(event=event, title=self._words(title),
                                         body=self._words(body), url=url or ticket.url))
        ctx.emit("notify.sent" if sent else "notify.skipped",
                 level="info" if sent else "debug", channel_event=event,
                 configured=getattr(self.notifier, "configured", False))

    def _finalize(self, ticket: Ticket, ctx: RunContext, skip_label: bool = False) -> None:
        if not skip_label:
            # decide(label=…) ghi đè nhãn mặc định (vd plan lạc hậu → quay về agent:try).
            label = ctx.outcome_label or LABEL[ctx.outcome or Reason.ERROR]
            self._release(ctx, ticket.id, label)
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
            stale = ctx.outcome_kind == Kind.PLAN_STALE.value
            # Lần sau người chuyển `agent:try` thì agent làm gì — nói trước, để người khỏi
            # đoán phải kéo ticket sang đâu.
            hint = ctx.resume_hint or ("" if stale or ctx.outcome_kind in _SELF_EXPLAINED else
                                       f"trả lời/sửa nguyên nhân (comment ngay trên ticket nếu cần) "
                                       f"rồi chuyển `{L.TRY}`.")
            nxt = f"\n\n**Tiếp theo:** {hint}" if hint else ""
            self._say(ticket.id, f"{STALE_MARK if stale else ''}\n**{_tag(ctx)}** — {meaning}{why}"
                                 f"{nxt}\n\nLog: `{ctx.run_dir}`")
            if stale:
                return          # tự lập plan lại, không phải việc của người
            event = NEEDS_HUMAN if LABEL[ctx.outcome] == L.NEEDS_HUMAN else NO_MR
            self._announce(ctx, event, f"{_tag(ctx)} — {ticket.id}", ticket,
                           f"**{ticket.title}**{why}\n\n{meaning}")
        elif ctx.phase == "B":
            mr = self._last_mr(ctx)
            url, draft = mr.get("url", ""), bool(mr.get("draft"))
            kind = "MR Draft" if draft else "MR"
            verb = "đã cập nhật theo góp ý" if mr.get("updated") else "đã mở"
            if url:
                self._say(ticket.parent or ticket.id,
                          f"{MR_MARK}\n**{kind} {verb}:** {url}" + (
                              "\n\nDraft vì không có bằng chứng test đầy đủ — lý do ghi ở đầu mô tả MR."
                              if draft else ""))
            self._announce(ctx, MR_CREATED, f"{kind} {verb} — {ticket.id}", ticket,
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
            if event.get("event") in ("mr.created", "mr.updated"):
                return {**(event.get("data") or {}), "updated": event["event"] == "mr.updated"}
        return {}


def _norm(module: str) -> str:
    return str(module).strip().removeprefix("./").rstrip("/")


def _widened(previous: handoff.Handoff, spec) -> list[str]:
    """Plan sửa theo góp ý có MỞ RỘNG phạm vi so với plan trước không. [] = không.

    Người chưa nhìn thấy plan mới, nên chỉ được tự duyệt khi nó không làm gì hơn plan người
    vừa góp ý: không thêm file ngoài `scope.modules` cũ, không đổi loại task (đổi loại task
    là đổi luật chấm — T4 không có test viết trước, T2 không có fail-trước).
    """
    try:
        old = spec_mod.parse(previous.spec_yaml)
    except spec_mod.SpecError:
        return ["không đọc được spec của plan trước để so"]
    out = []
    olds = [_norm(m) for m in old.modules]
    for module in spec.modules:
        m = _norm(module)
        if m and not any(m == o or m.startswith(o + "/") for o in olds if o):
            out.append(f"thêm `{m}` vào phạm vi")
    if spec.task_type != old.task_type:
        out.append(f"đổi loại task `{old.task_type}` → `{spec.task_type}`")
    return out


def _forge_problems(forge) -> list[str]:
    """Preflight của forge. Forge không có `check()` (bản cũ, test) thì coi như ổn."""
    check = getattr(forge, "check", None)
    if check is None:
        return []
    try:
        return list(check())
    except Exception as exc:        # preflight hỏng không được làm hỏng cả run
        return [f"không kiểm được forge: {type(exc).__name__}: {exc}"]


def _resume_hint(cp: checkpoint.Checkpoint) -> str:
    """Câu nói cho người biết chuyển `agent:try` thì agent làm gì tiếp."""
    if not cp.stage:
        return f"chuyển `{L.TRY}`: agent chạy lại phần viết code với plan đã duyệt (không lập lại plan)."
    if not cp.resumable:
        return (f"chuyển `{L.TRY}`: bằng chứng của lần này không dùng lại được, agent chạy lại phần "
                f"viết code từ đầu với plan đã duyệt (không lập lại plan).")
    done = checkpoint.STAGE_WORDS.get(cp.stage, cp.stage)
    return (f"sửa nguyên nhân rồi chuyển `{L.TRY}` (kèm comment nếu muốn agent sửa gì thêm): agent "
            f"làm tiếp từ bước **{cp.next_step()}** trên code đã có (đã xong tới: {done}), không "
            f"lập lại plan, không viết lại code.")


def _mtime(path: Path) -> float | None:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def _value(outcome) -> str:
    return outcome.value if isinstance(outcome, Reason) else str(outcome)


def _mr_after_plan(comments: list[str]) -> bool:
    """Đã có comment link MR sau comment plan MỚI NHẤT chưa."""
    last_plan = _last_index(comments, handoff.OPEN)
    return any(MR_MARK in c for c in comments[last_plan + 1:])


def _last_index(comments: list[str], mark: str) -> int:
    return max((i for i, c in enumerate(comments) if mark in c), default=-1)


def _is_human(text: str) -> bool:
    """Comment thật của người: có nội dung, không mang dấu agent. Backlog ghi comment RỖNG
    mỗi lần đổi status — kể cả lần agent đổi — nên comment rỗng không phải lời của ai."""
    text = (text or "").strip()
    return bool(text) and AGENT_MARK not in text and handoff.OPEN not in text


def _phase_b_ran(text: str) -> bool:
    """Mốc chạy Phase B. Mốc của phiên bản cũ không ghi phase: sau plan thì chỉ có thể là B
    (Phase A chạy lại sau plan chỉ khi plan bị từ chối/lạc hậu, và các trường hợp đó đã có dấu riêng)."""
    m = _RUNNING_RE.search(text)
    return bool(m) and m.group(3) in ("B", None)


#: Kind mà câu giải thích đã tự nói phải làm gì tiếp — không thêm dòng "Tiếp theo".
_SELF_EXPLAINED = {Kind.NOT_READY.value, Kind.SCOPE_POLICY.value, Kind.FORGE_AUTH.value,
                   Kind.MR_FAILED.value}

#: Kết quả của `route()`.
PLAN, REVISE, APPROVE, BUILD, UPDATE_MR, MR_DONE = (
    "plan", "revise", "approve", "build", "update_mr", "mr_done")


def route(comments: list[str]) -> str:
    """Ticket vừa về `agent:try`: agent làm bước nào. Chỉ đọc dấu máy — không LLM, không đoán.

    Người chỉ cần chuyển ticket về cho agent; ý của cú chuyển đó suy ra từ việc agent vừa
    nhờ: plan chờ duyệt → duyệt; câu hỏi → đã trả lời; lỗi → đã sửa, làm tiếp.
    """
    last_plan = _last_index(comments, handoff.OPEN)
    if last_plan < 0:
        return PLAN
    after = comments[last_plan + 1:]
    if any(REJECT_MARK in c or STALE_MARK in c for c in after):
        return PLAN
    approved = any(APPROVE_MARK in c or AUTO_APPROVE_MARK in c or MR_MARK in c or _phase_b_ran(c)
                   for c in after)
    if not approved:
        return REVISE if any(_is_human(c) for c in after) else APPROVE
    last_mr = _last_index(comments, MR_MARK)
    if last_mr > last_plan:
        return UPDATE_MR if any(_is_human(c) for c in comments[last_mr + 1:]) else MR_DONE
    return BUILD


#: Comment máy của agent không mang thông tin cho lần chạy sau: mốc chạy, plan (đã có
#: riêng), link ticket chi tiết/MR, thông báo tự duyệt.
_MACHINE_ONLY = (RUNNING_MARK, handoff.OPEN, DETAIL_MARK, MR_MARK, AUTO_APPROVE_MARK, NOTICE_MARK)
_LOG_LINE = re.compile(r"\n+Log: `[^`]*`\s*$")


def conversation(comments: list[str], since_last_plan: bool = False,
                 limit: int = 12, per_comment: int = 1500) -> list[tuple[str, str]]:
    """[(người nói, nội dung)] cũ → mới, để agent đọc được cuộc trao đổi trên ticket.

    "agent" là câu hỏi/lý do dừng mà agent để lại (NO_MR, NEEDS_HUMAN, từ chối); "human" là
    mọi comment không mang dấu agent. `since_last_plan=True` (Phase B): chỉ comment của
    người viết SAU plan mới nhất — những gì người bổ sung sau khi đã duyệt.
    """
    start = 0
    if since_last_plan:
        start = max((i for i, c in enumerate(comments) if handoff.OPEN in c), default=-1) + 1
    out: list[tuple[str, str]] = []
    for text in comments[start:]:
        text = (text or "").strip()
        if not text:
            continue                 # Backlog ghi comment rỗng khi chỉ đổi status/category
        if AGENT_MARK in text:
            if since_last_plan or any(m in text for m in _MACHINE_ONLY):
                continue
            body = _LOG_LINE.sub("", text.replace(AGENT_MARK, "").replace(REJECT_MARK, "")).strip()
            who = "agent"
        elif handoff.OPEN in text:
            continue
        else:
            body, who = text, "human"
        if len(body) > per_comment:
            body = body[:per_comment] + "…"
        out.append((who, body))
    return out[-limit:]


def _tag(ctx: RunContext) -> str:
    """`NO_MR/plan_stale` — kết cục kèm chi tiết, cho comment và thông báo."""
    return f"{ctx.outcome.value}/{ctx.outcome_kind}" if ctx.outcome_kind else ctx.outcome.value


def _last_human_comment(comments: list[str]) -> str:
    """Comment gần nhất của người. Bỏ qua comment rỗng: Backlog tạo một comment rỗng mỗi
    lần đổi status/category — kể cả lần agent tự đổi — nên "comment cuối" thường là nó."""
    for text in reversed(comments):
        if (text or "").strip() and AGENT_MARK not in text and handoff.OPEN not in text:
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
    from ..core.parsers import fenced_block
    spec_text = fenced_block(body)
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


#: Cách người quyết plan — một chỗ cho comment plan, thông báo và kênh chat.
_HOW_TO_DECIDE = (
    f"**Duyệt:** chuyển `{L.TRY}` — agent viết code và mở MR ngay trên ticket này.\n"
    f"**Muốn sửa:** viết góp ý trong comment và chuyển `{L.TRY}` trong CÙNG lần Submit — agent sửa "
    f"plan theo góp ý rồi làm tiếp; plan mới mà mở rộng phạm vi (thêm file, đổi loại task) thì "
    f"agent đưa lại để bạn duyệt.")


def _plan_comment(plan: str, spec, base_sha: str, prof: Profile | None = None,
                  revised: bool = False, wider: list[str] | None = None) -> str:
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
    if revised and not wider:
        title, tail = ("## Plan đã sửa theo góp ý",
                       "Plan này không mở rộng phạm vi so với plan trước nên được tự duyệt — agent "
                       "viết code ở vòng quét tới. MR vẫn là chốt review cuối.")
    else:
        title = "## Plan chờ duyệt (đã sửa theo góp ý)" if revised else "## Plan chờ duyệt"
        tail = _HOW_TO_DECIDE
        if wider:
            blocks = ("**⚠ Plan sửa theo góp ý nhưng MỞ RỘNG phạm vi so với plan trước** — cần bạn "
                      "duyệt lại:\n" + "\n".join(f"- {w}" for w in wider) + "\n\n") + blocks
    return (f"{title}\n\n**Chưa có dòng code nào được thay đổi.**\n\n"
            f"- Loại task: `{spec.task_type}`\n- Phạm vi thay đổi: {modules}\n"
            f"- Base commit: `{base_sha[:12]}`\n\n"
            f"{blocks}---\n\n{plan}\n\n---\n\n{tail}")


def _notice(ticket: Ticket) -> str:
    return (f"@here Plan cho **{ticket.title}** đã sẵn sàng để duyệt. Xem comment ngay trên: "
            f"chuyển `{L.TRY}` để duyệt, hoặc viết góp ý rồi chuyển `{L.TRY}` để agent sửa plan.")
