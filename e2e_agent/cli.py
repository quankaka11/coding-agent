"""CLI e2ea — lớp cưỡng chế. Mỗi lệnh là một bước có thể chạy độc lập."""
from __future__ import annotations

import argparse
import dataclasses
import json
import secrets
import sys
from pathlib import Path

from . import metrics as metrics_mod, report as report_mod
from .config import profile as profile_mod
from .core import gitutil
from .enforce import antigaming, baseline as baseline_mod, gate as gate_mod, mutation
from . import agent as agent_mod, forge as forge_mod, notify as notify_mod, tracker as tracker_mod
from .pipeline.orchestrator import Orchestrator
from .pipeline.watcher import Watcher
from .tracker import base as L
from .config.profile import ProfileError
from .core.reasons import LABEL, Reason
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
            ctx.decide(Reason.NOMR_GATE_FAIL, f"gate fail: {', '.join(rep['failed_checks'])}")
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
            ctx.decide(Reason.HUMAN_ANTIGAMING, f"vi phạm: {', '.join(rep['failed_rules'])}")
        ctx.decide(Reason.OK, "anti-gaming PASS")
    return _exit(ctx)


def cmd_check(args) -> int:
    """Gate + anti-gaming trong một run — dùng cho CI đối chứng."""
    prof = profile_mod.load(args.profile)
    repo = Path(args.repo).resolve()
    base = _load_baseline(args)
    base_sha = args.base_sha or (base.base_sha if base else gitutil.head_sha(repo) + "~1")
    with _run(args, "B") as ctx:
        if args.verify_from_base:
            from .pipeline import verify_from_base
            base, ev = verify_from_base(ctx, prof, repo, base_sha, args.work_root)
            _merge_evidence(ctx, ev)
        with ctx.stage("gate"):
            rep = gate_mod.run(ctx, prof, repo, base)
        if args.mutation:
            with ctx.stage("mutation"):
                _merge_evidence(ctx, {"mutation": mutation.run(ctx, prof, repo, base_sha)})
        with ctx.stage("antigaming"):
            ag = antigaming.run(ctx, prof, repo, base_sha, base, rep,
                                task_type=args.task_type, acceptance_ids=args.ac or [],
                                modules=args.modules or [])
        if ag["verdict"] == "FAIL":
            ctx.decide(Reason.HUMAN_ANTIGAMING, f"vi phạm: {', '.join(ag['failed_rules'])}")
        if rep["verdict"] == "FAIL":
            ctx.decide(Reason.NOMR_GATE_FAIL, f"gate fail: {', '.join(rep['failed_checks'])}")
        ctx.decide(Reason.OK, "gate PASS và anti-gaming PASS")
    return _exit(ctx)


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
    print(orch.promote(tracker.get(args.ticket)))
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
        print("cần --file hoặc --body: ticket không có mô tả thì Intake sẽ trả NOMR_NOT_READY",
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


def cmd_label(args) -> int:
    """Reviewer gán nhãn kết quả — đây là dữ liệu đo pilot, không chỉ để merge."""
    run_dir = Path(args.run_dir) if args.run_dir else metrics_mod.latest_run(
        Path(args.runs_root), args.task_id)
    if run_dir is None:
        print(f"không thấy run nào của ticket {args.task_id} trong {args.runs_root}", file=sys.stderr)
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
    roll = metrics_mod.collect(Path(args.runs_root))
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

    a = sub.add_parser("antigaming", help="chạy G-1..G-8")
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
    c.add_argument("--task-type", default="T1", choices=("T1", "T2", "T3"))
    c.add_argument("--ac", nargs="*")
    c.add_argument("--modules", nargs="*", help="phạm vi plan đã duyệt (spec.scope.modules) cho G-10")
    c.add_argument("--mutation", action="store_true")
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
    pb = _life("phase-b", "chạy Baseline→MR cho một ticket chi tiết")
    pb.add_argument("--ticket", required=True)
    pb.set_defaults(func=cmd_phase_b)
    ap_ = _life("approve", "duyệt plan → tạo ticket chi tiết")
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
    lb.add_argument("--runs-root", default=str(RUNS_DIR))
    lb.add_argument("--outcome", required=True, choices=sorted(metrics_mod.OUTCOMES))
    lb.add_argument("--test-value", required=True, choices=sorted(metrics_mod.TEST_VALUES))
    lb.add_argument("--note", required=True, help="một câu vì sao")
    lb.add_argument("--reviewer", default="")
    lb.set_defaults(func=cmd_label)

    mt = sub.add_parser("metrics", help="rollup metric từ events.jsonl và review.json")
    mt.add_argument("--runs-root", default=str(RUNS_DIR))
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
