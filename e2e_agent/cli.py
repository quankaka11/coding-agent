"""CLI e2ea — lớp cưỡng chế. Mỗi lệnh là một bước có thể chạy độc lập."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import secrets
import sys
from pathlib import Path

from . import metrics as metrics_mod, report as report_mod
from .config import profile as profile_mod
from .core import gitutil
from .enforce import antigaming, baseline as baseline_mod, gate as gate_mod, mutation
from . import agent as agent_mod, forge as forge_mod, notify as notify_mod, tracker as tracker_mod
from .pipeline import handoff
from .pipeline.orchestrator import Orchestrator
from .pipeline.watcher import Watcher
from .tracker import base as L
from .config.profile import ProfileError
from .core.reasons import LABEL, Kind, Reason
from .core.run import Decided, RunContext, run_context

RUNS_DIR = Path("runs")
WORK_DIR = Path("work")
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--profile", required=True, help="đường dẫn repo-profile.yaml")
    p.add_argument("--repo", default=".", help="thư mục repo cần soi")
    p.add_argument("--task-id", default="-")
    p.add_argument("--run-dir", default=None, help="mặc định runs/<task_id>/<run_id>")
    p.add_argument("--log-level", default="info", choices=("debug", "info", "warn", "error"))
    p.add_argument("--quiet", action="store_true", help="không in ra console, chỉ ghi events.jsonl")


def cmd_doctor(args) -> int:
    prof = profile_mod.load(args.profile)
    repo = Path(args.repo).resolve()
    rows = profile_mod.doctor(prof, repo, PACKAGE_ROOT)
    rows += profile_mod.tracker_rows(prof, Path(args.profile).resolve().parent)
    missing = gate_mod.assert_all_declared(prof)
    width = max(len(r[0]) for r in rows)
    failed = 0
    for name, ok, note in rows:
        print(f"{'✅' if ok else '❌'} {name:<{width}}  {note}")
        failed += not ok
    for check in missing:
        print(f"❌ check {check:<{width - 6}}  chưa khai available/out_of_scope")
        failed += 1
    print(f"\n{'ĐẠT' if not failed else f'{failed} mục chưa đạt'} — hồ sơ {prof.repo_id}")
    return 1 if failed else 0


def cmd_baseline(args) -> int:
    prof = profile_mod.load(args.profile)
    repo = Path(args.repo).resolve()
    with _run(args, "B") as ctx:
        with ctx.stage("baseline"):
            base = baseline_mod.capture(ctx, prof, repo, scope=args.scope or [])
        ctx.decide(Reason.OK, f"baseline sạch: {base.total} test, {len(base.failed_tests)} lỗi sẵn có")
    return _exit(ctx)


def cmd_gate(args) -> int:
    prof = profile_mod.load(args.profile)
    repo = Path(args.repo).resolve()
    base = _load_baseline(args)
    with _run(args, "B") as ctx:
        with ctx.stage("gate"):
            rep = gate_mod.run(ctx, prof, repo, base)
        if rep["verdict"] == "FAIL":
            ctx.decide(Reason.NO_MR, f"gate fail: {', '.join(rep['failed_checks'])}", kind=Kind.GATE_FAIL)
        ctx.decide(Reason.OK, "gate PASS")
    return _exit(ctx)


def cmd_antigaming(args) -> int:
    prof = profile_mod.load(args.profile)
    repo = Path(args.repo).resolve()
    base = _load_baseline(args)
    base_sha = args.base_sha or (base.base_sha if base else None)
    if not base_sha:
        print("cần --base-sha hoặc --baseline", file=sys.stderr)
        return 2
    with _run(args, "B") as ctx:
        if args.mutation:
            with ctx.stage("mutation"):
                result = mutation.run(ctx, prof, repo, base_sha)
            _merge_evidence(ctx, {"mutation": result})
        with ctx.stage("antigaming"):
            rep = antigaming.run(ctx, prof, repo, base_sha, base, task_type=args.task_type,
                                 acceptance_ids=args.ac or [], modules=args.modules or [])
        if rep["verdict"] == "FAIL":
            reason, why, kind = _antigaming_reason(rep)
            ctx.decide(reason, why, kind=kind, blocking_rules=rep["blocking_rules"],
                       needs_review=rep["needs_review"])
        ctx.decide(Reason.OK, "anti-gaming PASS", needs_review=rep["needs_review"])
    return _exit(ctx)


def cmd_check(args) -> int:
    """Gate + anti-gaming trong một run — dùng cho CI đối chứng."""
    prof = profile_mod.load(args.profile)
    repo = Path(args.repo).resolve()
    base = _load_baseline(args)
    base_sha = args.base_sha or (base.base_sha if base else gitutil.head_sha(repo) + "~1")
    task_type, acs, modules = args.task_type, args.ac or [], args.modules or []
    with _run(args, "B") as ctx:
        if args.task_meta_env:
            # CI không có ticket lẫn `runs/`, nên phạm vi plan phải đi cùng MR.
            # Thiếu payload thì giữ nguyên cờ dòng lệnh: G-8/G-10 SKIP, không báo oan.
            meta = handoff.unpack_task(os.environ.get(args.task_meta_env, ""))
            ctx.emit("task_meta.read", source=args.task_meta_env, found=bool(meta),
                     level="info" if meta else "warn", **(meta or {}))
            if meta:
                task_type = task_type or meta.get("task_type")
                acs = acs or list(meta.get("ac") or [])
                modules = modules or list(meta.get("modules") or [])
        task_type = task_type or "T1"
        if args.verify_from_base:
            from .pipeline import verify_from_base
            base, ev = verify_from_base(ctx, prof, repo, base_sha, args.work_root)
            _merge_evidence(ctx, ev)
        with ctx.stage("gate"):
            rep = gate_mod.run(ctx, prof, repo, base)
        if prof.is_available("coverage"):
            # G-7 đọc coverage.json của chính cây code đang soi. Không đo ở đây thì
            # luật quan trọng nhất về "test có chạy qua dòng mới không" vĩnh viễn
            # bỏ qua trên CI, dù hồ sơ đã khai coverage available.
            with ctx.stage("coverage"):
                baseline_mod.measure_coverage(ctx, prof, repo)
        # T2 và T3 không có bằng chứng fail-trước nên mutation là lưới duy nhất;
        # T1 thì chạy mutation chỉ tốn thời gian CI. Tự quyết theo loại task để
        # người viết file CI không phải đoán — họ không biết trước MR nào loại gì.
        if args.mutation or (task_type in ("T2", "T3") and prof.mutation_cfg("enabled")):
            with ctx.stage("mutation"):
                _merge_evidence(ctx, {"mutation": mutation.run(ctx, prof, repo, base_sha)})
        with ctx.stage("antigaming"):
            ag = antigaming.run(ctx, prof, repo, base_sha, base, rep,
                                task_type=task_type, acceptance_ids=acs, modules=modules)
        if ag["verdict"] == "FAIL":
            reason, why, kind = _antigaming_reason(ag)
            ctx.decide(reason, why, kind=kind, blocking_rules=ag["blocking_rules"],
                       needs_review=ag["needs_review"])
        if rep.get("flaky"):
            ctx.decide(Reason.NEEDS_HUMAN, f"test không nhất quán: {', '.join(rep['flaky'][:5])}",
                       kind=Kind.FLAKY)
        if rep["verdict"] == "FAIL":
            ctx.decide(Reason.NO_MR, f"gate fail: {', '.join(rep['failed_checks'])}", kind=Kind.GATE_FAIL)
        ctx.decide(Reason.OK, "gate PASS và anti-gaming PASS", needs_review=ag["needs_review"])
    return _exit(ctx)


# -- một lệnh cho repo mới: .env → clone → hồ sơ → doctor → category → watch ----

_ENV_HELP = """Cần trong .env (tại thư mục đang chạy) hoặc biến môi trường:
  repo_url=https://git.hblab.vn/nhom/ten-repo.git   # bắt buộc
  gitlab_token=...                                   # bắt buộc với repo GitLab, vai trò Developer+
  backlog_space=xxx.backlog.com                      # thiếu cả hai dòng backlog → ticket trên đĩa (offline)
  backlog_project=PROJKEY
  backlog_api_key=...
  google_chat_webhook=https://chat.googleapis.com/... # tuỳ chọn
  agent_model=opus                                   # tuỳ chọn: alias hoặc tên đầy đủ, ghi đè agent.model
  agent_effort=high                                  # tuỳ chọn: low|medium|high|xhigh|max, ghi đè agent.effort"""


def _layout(args) -> dict:
    """Mọi thứ nằm dưới --dir: repos/<tên>, profiles/<tên>.yaml, runs/<tên>, work/<tên>."""
    from .config import autoprofile
    from .core.secrets import read_secret

    root = Path(args.dir).resolve()
    from .core import secrets as secrets_mod
    if (root / ".env").is_file() and (root / ".env") not in secrets_mod.EXTRA_ENV_FILES:
        secrets_mod.EXTRA_ENV_FILES.append(root / ".env")
    url = read_secret("repo_url")
    if not url:
        raise ProfileError("thiếu repo_url.\n" + _ENV_HELP)
    if "://" in url or url.startswith("git@"):
        forge_url, project, name = autoprofile.parse_repo_url(url)
        forge = {"kind": "gitlab", "url": forge_url, "project": project, "token_env": "gitlab_token"}
        if not read_secret("gitlab_token"):
            raise ProfileError("repo_url là GitLab nhưng thiếu gitlab_token.\n" + _ENV_HELP)
    else:                                    # đường dẫn trên đĩa → chạy offline
        name, forge = Path(url).name.removesuffix(".git"), None
    tracker = None
    if read_secret("backlog_space") and read_secret("backlog_project"):
        if not read_secret("backlog_api_key"):
            raise ProfileError("có backlog_space/backlog_project nhưng thiếu backlog_api_key.\n" + _ENV_HELP)
        tracker = {"kind": "backlog", "space": read_secret("backlog_space"),
                   "project": read_secret("backlog_project"), "api_key_env": "backlog_api_key"}
    notify = ({"kind": "webhook", "url_env": "google_chat_webhook"}
              if read_secret("google_chat_webhook") else None)
    # `runs/<tên repo>/` chứ không phải `runs/` chung: đổi repo trong cùng thư mục
    # làm việc mà trộn run lại thì mọi tỉ lệ trong `e2ea metrics` đều sai.
    return {"root": root, "url": url, "name": name, "repo": root / "repos" / name,
            "profile": root / "profiles" / f"{name}.yaml", "forge": forge, "tracker": tracker,
            "notify": notify, "runs": root / "runs" / name, "work": root / "work" / name,
            "venv": root / "venvs" / name}


def _prepare_env(args, lay: dict) -> tuple[str, list[str]]:
    """venv + dependency của repo. Trả về (python dùng trong commands.*, TODO chưa xong).

    TODO ở đây phải CHẶN `up`: môi trường test hỏng thì mọi ticket đều kết thúc
    NO_MR/baseline_red, và người vận hành đọc ra là "repo đỏ" chứ không phải "venv sai".
    """
    from .config import autoprofile
    if args.no_venv:
        return "python3", []
    if (lay["repo"] / ".git").exists() and not autoprofile.is_python_repo(lay["repo"]):
        # Ngôn ngữ khác không dùng venv chung: dependency cài trong từng worktree bằng
        # `commands.setup` do agent đề xuất lúc sinh hồ sơ.
        return "python3", []
    print(f"== môi trường test: {lay['venv']}")
    python, notes, todos = autoprofile.ensure_venv(lay["repo"], lay["venv"])
    for n in notes:
        print(f"  {n}")
    for t in todos:
        print(f"  TODO: {t}")
    return python, todos


def _clone_if_missing(lay: dict) -> None:
    import subprocess
    repo = lay["repo"]
    if (repo / ".git").exists():
        return
    repo.parent.mkdir(parents=True, exist_ok=True)
    url = lay["url"]
    if lay["forge"]:
        from .core.secrets import read_secret
        host = lay["forge"]["url"].split("://", 1)[-1]
        auth_url = f"https://oauth2:{read_secret('gitlab_token')}@{host}/{lay['forge']['project']}.git"
    else:
        auth_url = url
    print(f"clone {url} → {repo}")            # KHÔNG in auth_url, kể cả đã che
    proc = subprocess.run(["git", "clone", "--quiet", auth_url, str(repo)], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise ProfileError(f"clone thất bại: {gitutil.redact_url(proc.stderr.strip())}")
    if lay["forge"]:
        # Không để token nằm lại trong .git/config: agent chạy Bash trong worktree đọc được.
        subprocess.run(["git", "remote", "set-url", "origin", url], cwd=repo, check=False)


def cmd_init(args) -> int:
    """Sinh hồ sơ cho repo mới từ .env và nội dung repo. Không ghi đè hồ sơ đã có."""
    from .config import autoprofile
    lay = _layout(args)
    _clone_if_missing(lay)
    if lay["profile"].exists() and not args.force:
        print(f"đã có hồ sơ {lay['profile']} — giữ nguyên (dùng --force để sinh lại)")
        return 1 if _prepare_env(args, lay)[1] else 0
    python, env_todos = _prepare_env(args, lay)
    draft = autoprofile.generate(lay["repo"], lay["name"], tracker=lay["tracker"],
                                 forge=lay["forge"], notify=lay["notify"], python=python,
                                 suggest=_suggester(lay))
    draft.todos = env_todos + draft.todos
    lay["profile"].parent.mkdir(parents=True, exist_ok=True)
    lay["profile"].write_text(draft.dumps(), encoding="utf-8")
    print(f"đã sinh {lay['profile']}")
    for n in draft.notes:
        print(f"  đoán: {n}")
    for t in draft.todos:
        print(f"  TODO: {t}")
    return 1 if draft.todos else 0


def _suggester(lay: dict):
    """Hàm cho autoprofile gọi khi repo không phải Python: agent soi repo, đề xuất lệnh."""
    from .config import agentprofile

    def suggest(repo: Path):
        agent = agent_mod.make_default()
        if not agent.available():
            return None, f"không thấy lệnh {agent.binary!r} (claude) để soi repo"
        print("== repo không phải Python — agent đang soi repo để đề xuất lệnh setup/test/lint (vài phút)")
        return agentprofile.suggest(repo, agent, agentprofile.run_dir_for(lay["runs"]))
    return suggest


def cmd_clean(args) -> int:
    """Dọn worktree của các run cũ (và nhánh local agent/* nếu --branches)."""
    from .pipeline import sandbox
    lay = _layout(args)
    removed = sandbox.prune(lay["repo"], lay["work"], args.days, branches=args.branches,
                            dry_run=args.dry_run)
    verb = "sẽ xoá" if args.dry_run else "đã xoá"
    print(f"{verb} {len(removed)} mục" + ("" if removed else " — không có gì cũ hơn "
                                            f"{args.days:g} ngày"))
    for item in removed:
        print(f"  {item}")
    return 0


def cmd_up(args) -> int:
    """init (nếu chưa) → doctor → tạo category → watch. Một lệnh cho repo mới."""
    lay = _layout(args)
    if not lay["profile"].exists():
        if cmd_init(args) != 0:
            print("\nhồ sơ còn TODO — sửa rồi chạy lại `e2ea up`", file=sys.stderr)
            return 2
    else:
        _clone_if_missing(lay)
        if _prepare_env(args, lay)[1]:   # dependency có thể đã đổi từ lần trước
            print("\nmôi trường test còn TODO — sửa rồi chạy lại `e2ea up`", file=sys.stderr)
            return 2
    args.profile, args.repo = str(lay["profile"]), str(lay["repo"])
    args.runs_root, args.work_root = str(lay["runs"]), str(lay["work"])

    print(f"== doctor {lay['profile'].name}")
    if cmd_doctor(args) != 0:
        print(f"\nsửa {lay['profile']} rồi chạy lại", file=sys.stderr)
        return 2
    print("== category trên tracker")
    cmd_labels_init(args)
    if lay["tracker"] is None or lay["forge"] is None:
        print("== CHẾ ĐỘ OFFLINE: ticket/MR ghi vào", lay["profile"].parent / "backlog")
    if args.once:
        return cmd_scan(args)
    print(f"== watch mỗi {args.interval}s — Ctrl-C để dừng. Tạo ticket với category `{L.TRY}`.")
    return cmd_watch(args)


def cmd_serve(args) -> int:
    """Giao diện web trên thư mục làm việc. Chỉ nghe localhost nếu không bảo khác."""
    try:
        from .web.app import serve
    except ImportError:
        print("thiếu fastapi/uvicorn — cài bằng: pip install -e \".[web]\"", file=sys.stderr)
        return 2
    root = Path(args.dir).resolve()
    # `read_secret` còn một đường đọc `.env` theo thư mục đang đứng. Không đứng
    # đúng chỗ thì `serve --dir X` lấy khoá của thư mục gọi lệnh — sai lặng lẽ,
    # và sai về bí mật.
    os.chdir(root)
    return serve(root, host=args.host, port=args.port,
                 watch_interval=args.interval if args.watch else None)


def _orchestrator(args, prof, repo):
    base_dir = Path(args.profile).resolve().parent
    tracker = tracker_mod.make(prof, base_dir)
    agent = agent_mod.make(prof)
    return Orchestrator(prof=prof, profile_path=Path(args.profile).resolve(), repo=repo,
                        tracker=tracker, agent=agent, forge=forge_mod.make(prof, base_dir),
                        notifier=notify_mod.make(prof), notify_events=notify_mod.wanted(prof),
                        runs_root=Path(args.runs_root), work_root=Path(args.work_root),
                        package_root=PACKAGE_ROOT, log_level=args.log_level,
                        console=not args.quiet), tracker


def cmd_scan(args) -> int:
    """Một vòng quét: nhiều nhất một ticket cho mỗi trạng thái (giới hạn MVP)."""
    prof = profile_mod.load(args.profile)
    repo = Path(args.repo).resolve()
    orch, _ = _orchestrator(args, prof, repo)
    done = orch.scan_once()
    if not done:
        print("không có ticket nào ở trạng thái chờ xử lý")
    for ticket_id, outcome in done:
        print(f"{ticket_id}: {outcome}")
    return 0


def cmd_watch(args) -> int:
    """Chạy liên tục: quét backlog → chạy bước tương ứng → lặp. Ctrl-C để dừng."""
    prof = profile_mod.load(args.profile)
    repo = Path(args.repo).resolve()
    orch, _ = _orchestrator(args, prof, repo)
    watcher = Watcher(orch=orch, interval=args.interval, runs_root=Path(args.runs_root),
                      log_level=args.log_level, console=not args.quiet)
    handled = watcher.run(max_cycles=args.max_cycles)
    if handled < 0:
        return 2
    print(f"đã xử lý {handled} lượt ticket")
    return 0


def cmd_phase_a(args) -> int:
    prof = profile_mod.load(args.profile)
    repo = Path(args.repo).resolve()
    orch, tracker = _orchestrator(args, prof, repo)
    outcome = orch.run_phase_a(tracker.get(args.ticket))
    print(f"{args.ticket}: {outcome.value}")
    return 0 if outcome is Reason.OK else 1


def cmd_phase_b(args) -> int:
    prof = profile_mod.load(args.profile)
    repo = Path(args.repo).resolve()
    orch, tracker = _orchestrator(args, prof, repo)
    outcome = orch.run_phase_b(tracker.get(args.ticket))
    print(f"{args.ticket}: {outcome.value}")
    return {"agent:mr-created": 0, "agent:no-mr": 1}.get(LABEL[outcome], 2)


def cmd_approve(args) -> int:
    prof = profile_mod.load(args.profile)
    orch, tracker = _orchestrator(args, prof, Path(args.repo).resolve())
    print(orch.approve(tracker.get(args.ticket)))
    return 0


def cmd_reject(args) -> int:
    prof = profile_mod.load(args.profile)
    orch, tracker = _orchestrator(args, prof, Path(args.repo).resolve())
    outcome = orch.reject(tracker.get(args.ticket), args.why)
    print("đã ghi nhận từ chối" if outcome is Reason.OK else "quá số lần cho phép → NO_MR")
    return 0


def cmd_new_ticket(args) -> int:
    """Tạo ticket thẳng từ CLI — tiện để thử luồng và để viết kịch bản test."""
    prof = profile_mod.load(args.profile)
    _, tracker = _orchestrator(args, prof, Path(args.repo).resolve())
    body = Path(args.file).read_text(encoding="utf-8") if args.file else (args.body or "")
    if not body.strip():
        print("cần --file hoặc --body: ticket không có mô tả thì Intake sẽ trả NO_MR/not_ready",
              file=sys.stderr)
        return 2
    ticket = tracker.create_ticket(args.title, body, [args.label])
    print(f"{ticket.id}  {ticket.url}")
    return 0


def cmd_tickets(args) -> int:
    prof = profile_mod.load(args.profile)
    _, tracker = _orchestrator(args, prof, Path(args.repo).resolve())
    for label in L.STATE_LABELS:
        for ticket in tracker.list_by_label(label):
            print(f"{label:<22} {ticket.id:<6} {ticket.title[:60]}")
    return 0


def cmd_labels_init(args) -> int:
    """Tạo trước toàn bộ label trên GitLab (mục 1.1 của operations-guide)."""
    prof = profile_mod.load(args.profile)
    _, tracker = _orchestrator(args, prof, Path(args.repo).resolve())
    if not hasattr(tracker, "ensure_labels"):
        print("backend file không cần tạo label trước")
        return 0
    created = tracker.ensure_labels(L.ALL_LABELS)
    print(f"đã tạo {len(created)}: {created}" if created else "đủ rồi")
    print("tracker:", tracker.whoami())
    return 0


def cmd_report(args) -> int:
    run_dir = Path(args.run_dir)
    text = report_mod.render(run_dir)
    out = run_dir / "report.md"
    out.write_text(text, encoding="utf-8")
    print(text if not args.quiet_report else f"đã ghi {out}")
    return 0


def _read_runs_root(value: str | None) -> Path:
    """`--runs-root` không đặt thì đoán như người đứng trong thư mục làm việc.

    `up`/`serve` ghi run vào `runs/<tên repo>/`; mặc định `runs/` thì `metrics`
    gõ ngay tại đó ra 0 run mà không báo gì. Thư mục đó chưa có (còn layout cũ,
    hoặc không phải thư mục làm việc) thì vẫn là `runs/` như trước.
    """
    if value:
        return Path(value)
    if Path(".env").is_file():
        from types import SimpleNamespace
        try:
            runs = _layout(SimpleNamespace(dir="."))["runs"]
        except (ProfileError, ValueError):     # .env dở dang: không đoán nữa
            runs = None
        if runs is not None and runs.is_dir():
            return runs
    return RUNS_DIR


def cmd_label(args) -> int:
    """Reviewer gán nhãn kết quả — đây là dữ liệu đo pilot, không chỉ để merge."""
    runs_root = _read_runs_root(args.runs_root)
    run_dir = Path(args.run_dir) if args.run_dir else metrics_mod.latest_run(runs_root, args.task_id)
    if run_dir is None:
        print(f"không thấy run nào của ticket {args.task_id} trong {runs_root}", file=sys.stderr)
        return 2
    try:
        path = metrics_mod.save_review(run_dir, metrics_mod.Review(
            outcome=args.outcome, test_value=args.test_value, note=args.note,
            reviewer=args.reviewer or ""))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"đã ghi {path}")
    return 0


def cmd_metrics(args) -> int:
    roll = metrics_mod.collect(_read_runs_root(args.runs_root))
    if args.json:
        print(json.dumps(dataclasses.asdict(roll), ensure_ascii=False, indent=2))
    else:
        print(metrics_mod.render(roll))
    return 0


def cmd_reasons(_args) -> int:
    print(report_mod.explain_all())
    return 0


# -- hạ tầng dùng chung ----------------------------------------------------

def _run(args, phase: str):
    run_id = "r-" + secrets.token_hex(3)
    run_dir = Path(args.run_dir) if args.run_dir else RUNS_DIR / args.task_id / run_id
    return run_context(run_dir=run_dir, run_id=run_id, task_id=args.task_id, phase=phase,
                       log_level=args.log_level, console=not args.quiet)


def _exit(ctx: RunContext) -> int:
    """0 khi OK, 1 khi NO_MR, 2 khi cần người. Exit code là hợp đồng với CI."""
    print(f"run dir: {ctx.run_dir}", file=sys.stderr)
    return {"agent:mr-created": 0, "agent:no-mr": 1}.get(LABEL[ctx.outcome or Reason.ERROR], 2)


def _antigaming_reason(report: dict) -> tuple:
    """CI không có vòng tự sửa: luật agent-sửa-được vẫn là NO_MR (exit 1), chỉ luật
    không ai được tự sửa mới là chuyện của người (exit 2). Trả về (reason, why, kind)."""
    why = f"vi phạm: {', '.join(report['failed_rules'])}"
    if report.get("blocking_rules"):
        return Reason.NEEDS_HUMAN, why, Kind.ANTIGAMING_EVIDENCE
    return Reason.NO_MR, why, Kind.ANTIGAMING


def _load_baseline(args):
    path = Path(args.baseline) if args.baseline else None
    if path and path.is_file():
        return baseline_mod.Baseline.load(path)
    return None


def _merge_evidence(ctx: RunContext, extra: dict) -> None:
    path = ctx.run_dir / "evidence.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.update(extra)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="e2ea", description="Lớp cưỡng chế của E2E Coding Agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _onboard(name, help_text):
        sp = sub.add_parser(name, help=help_text, epilog=_ENV_HELP,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
        sp.add_argument("--dir", default=".", help="thư mục làm việc: repos/, profiles/, runs/, work/")
        sp.add_argument("--log-level", default="info", choices=("debug", "info", "warn", "error"))
        sp.add_argument("--quiet", action="store_true")
        sp.add_argument("--no-venv", action="store_true",
                        help="không tạo venv/cài dependency; commands.* dùng python3 của máy")
        sp.set_defaults(task_id="-", run_dir=None)
        return sp

    ini = _onboard("init", "clone repo từ .env và sinh hồ sơ tự động (không ghi đè)")
    ini.add_argument("--force", action="store_true", help="sinh lại hồ sơ dù đã có")
    ini.set_defaults(func=cmd_init)
    up = _onboard("up", "một lệnh cho repo mới: .env → clone → hồ sơ → doctor → category → watch")
    up.add_argument("--force", action="store_true", help="sinh lại hồ sơ dù đã có")
    up.add_argument("--interval", type=int, default=60)
    up.add_argument("--max-cycles", type=int, default=None)
    up.add_argument("--once", action="store_true", help="quét một vòng rồi thoát thay vì watch")
    up.set_defaults(func=cmd_up)
    sv = _onboard("serve", "giao diện web: cấu hình, duyệt plan, xem run")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--host", default="127.0.0.1",
                    help="mặc định chỉ localhost — thư mục làm việc chứa token")
    sv.add_argument("--watch", action="store_true",
                    help="bật luôn vòng quét khi khởi động (Docker/systemd)")
    sv.add_argument("--interval", type=int, default=60, help="giây giữa hai vòng quét (với --watch)")
    sv.set_defaults(func=cmd_serve)
    cl = _onboard("clean", "dọn worktree của các run cũ (run ra MR đã tự dọn)")
    cl.add_argument("--days", type=float, default=7, help="chỉ dọn thứ cũ hơn N ngày (mặc định 7)")
    cl.add_argument("--branches", action="store_true",
                    help="xoá cả nhánh local agent/* không còn worktree (chỉ khi forge là GitLab)")
    cl.add_argument("--dry-run", action="store_true", help="chỉ liệt kê, không xoá")
    cl.set_defaults(func=cmd_clean)

    d = sub.add_parser("doctor", help="soát hồ sơ repo với repo thật")
    d.add_argument("--profile", required=True)
    d.add_argument("--repo", default=".")
    d.set_defaults(func=cmd_doctor)

    b = sub.add_parser("baseline", help="chụp baseline trên code chưa sửa")
    _common(b)
    b.add_argument("--scope", nargs="*", help="các thư mục sắp sửa (quyết định B1)")
    b.set_defaults(func=cmd_baseline)

    g = sub.add_parser("gate", help="chạy gate")
    _common(g)
    g.add_argument("--baseline", help="đường dẫn baseline.json")
    g.set_defaults(func=cmd_gate)

    a = sub.add_parser("antigaming", help="chạy G-1..G-11")
    _common(a)
    a.add_argument("--baseline")
    a.add_argument("--base-sha")
    a.add_argument("--task-type", default="T1", choices=("T1", "T2", "T3"))
    a.add_argument("--ac", nargs="*", help="danh sách id acceptance criteria (T3)")
    a.add_argument("--modules", nargs="*", help="phạm vi plan đã duyệt (spec.scope.modules) cho G-10")
    a.add_argument("--mutation", action="store_true", help="chạy mutation check trước")
    a.set_defaults(func=cmd_antigaming)

    c = sub.add_parser("check", help="gate + anti-gaming (dùng cho CI đối chứng)")
    _common(c)
    c.add_argument("--baseline")
    c.add_argument("--base-sha")
    # Không đặt sẵn "T1": còn None thì `--task-meta-env` mới điền được loại task
    # thật vào. Đặt sẵn là mọi MR trên CI đều bị soi như T1, kể cả T2 và T3.
    c.add_argument("--task-type", default=None, choices=("T1", "T2", "T3"))
    c.add_argument("--ac", nargs="*")
    c.add_argument("--modules", nargs="*", help="phạm vi plan đã duyệt (spec.scope.modules) cho G-10")
    c.add_argument("--task-meta-env", metavar="BIẾN",
                   help="tên biến môi trường chứa mô tả MR, để lấy task_type/ac/modules "
                        "mà CI không tự biết (GitLab: CI_MERGE_REQUEST_DESCRIPTION)")
    c.add_argument("--mutation", action="store_true",
                   help="buộc chạy mutation; mặc định chỉ chạy khi task là T2/T3")
    c.add_argument("--work-root", default=str(WORK_DIR))
    c.add_argument("--verify-from-base", action="store_true",
                   help="tự dựng lại baseline và bằng chứng fail-trước từ base commit "
                        "thay vì tin file agent nộp (dùng cho CI đối chứng)")
    c.set_defaults(func=cmd_check)

    def _life(name, help_text):
        sp = sub.add_parser(name, help=help_text)
        _common(sp)
        sp.add_argument("--runs-root", default=str(RUNS_DIR))
        sp.add_argument("--work-root", default=str(WORK_DIR))
        return sp

    _life("scan", "quét một vòng rồi thoát").set_defaults(func=cmd_scan)
    w = _life("watch", "chạy liên tục: quét → xử lý → lặp (dùng cho vòng tự động)")
    w.add_argument("--interval", type=int, default=60, help="giây giữa hai vòng quét")
    w.add_argument("--max-cycles", type=int, default=None, help="dừng sau N vòng (để test)")
    w.set_defaults(func=cmd_watch)
    _life("tickets", "liệt kê ticket theo trạng thái").set_defaults(func=cmd_tickets)
    nt = _life("new-ticket", "tạo ticket trên backlog (để thử luồng)")
    nt.add_argument("--title", required=True)
    nt.add_argument("--file", help="file markdown chứa mô tả")
    nt.add_argument("--body", help="mô tả ngắn, thay cho --file")
    nt.add_argument("--label", default=L.TRY, choices=L.STATE_LABELS)
    nt.set_defaults(func=cmd_new_ticket)
    _life("labels-init", "tạo trước label trên tracker").set_defaults(func=cmd_labels_init)
    pa = _life("phase-a", "chạy Intake/Discovery/Planning cho một ticket")
    pa.add_argument("--ticket", required=True)
    pa.set_defaults(func=cmd_phase_a)
    pb = _life("phase-b", "chạy Baseline→MR cho một ticket đã có plan")
    pb.add_argument("--ticket", required=True)
    pb.set_defaults(func=cmd_phase_b)
    ap_ = _life("approve", "duyệt plan → vòng quét sau viết code và mở MR")
    ap_.add_argument("--ticket", required=True)
    ap_.set_defaults(func=cmd_approve)
    rj = _life("reject", "từ chối plan kèm lý do")
    rj.add_argument("--ticket", required=True)
    rj.add_argument("--why", required=True)
    rj.set_defaults(func=cmd_reject)

    r = sub.add_parser("report", help="dựng report.md từ events.jsonl")
    r.add_argument("--run-dir", required=True)
    r.add_argument("--quiet-report", action="store_true")
    r.set_defaults(func=cmd_report)

    lb = sub.add_parser("label", help="reviewer gán nhãn kết quả cho một run")
    lb.add_argument("--task-id", required=True)
    lb.add_argument("--run-dir", help="mặc định lấy run mới nhất của ticket")
    lb.add_argument("--runs-root", default=None,
                    help="mặc định runs/<repo trong .env>/ nếu có, không thì runs/")
    lb.add_argument("--outcome", required=True, choices=sorted(metrics_mod.OUTCOMES))
    lb.add_argument("--test-value", required=True, choices=sorted(metrics_mod.TEST_VALUES))
    lb.add_argument("--note", required=True, help="một câu vì sao")
    lb.add_argument("--reviewer", default="")
    lb.set_defaults(func=cmd_label)

    mt = sub.add_parser("metrics", help="rollup metric từ events.jsonl và review.json")
    mt.add_argument("--runs-root", default=None,
                    help="mặc định runs/<repo trong .env>/ nếu có, không thì runs/")
    mt.add_argument("--json", action="store_true")
    mt.set_defaults(func=cmd_metrics)

    sub.add_parser("reasons", help="in bảng mã kết cục").set_defaults(func=cmd_reasons)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ProfileError as exc:
        print(f"Hồ sơ repo sai:\n{exc}", file=sys.stderr)
        return 2
    except Decided:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
