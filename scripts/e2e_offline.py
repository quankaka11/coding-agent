"""Chạy trọn vòng ticket → MR OFFLINE: repo giả + origin giả + StubAgent + tracker/forge trên đĩa.

Không cần LLM, không cần mạng, không cần Backlog/GitLab. Dùng để kiểm hệ thống còn
chạy đúng sau khi sửa code:   python3 scripts/e2e_offline.py

Kiểm 5 điều: base lấy từ origin (repo local cố tình lạc hậu); artefact test không lọt
vào commit; ticket kẹt ở agent:running được giao cho người; plan lạc hậu → HUMAN_PLAN_STALE;
promote không đẻ hai ticket con. Mọi thứ ghi vào work/e2e-offline/ (đã gitignore).
"""
import shutil, subprocess, sys, textwrap, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "work" / "e2e-offline"
PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from e2e_agent.agent.headless import StubAgent
from e2e_agent.config import profile as profile_mod
from e2e_agent.core.reasons import Reason
from e2e_agent.forge.file import FileForge
from e2e_agent.pipeline.orchestrator import Orchestrator, RUNNING_MARK, DETAIL_MARK
from e2e_agent.tracker import base as L
from e2e_agent.tracker.file import FileTracker


def sh(cmd, cwd, check=True):
    p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True)
    if check and p.returncode:
        raise SystemExit(f"$ {cmd}\n{p.stdout}{p.stderr}")
    return p.stdout.strip()


def git_commit(repo, msg):
    sh(["git", "add", "-A"], repo); sh(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg], repo)
    return sh(["git", "rev-parse", "HEAD"], repo)


def make_project(path):
    (path / "src").mkdir(parents=True); (path / "tests").mkdir()
    (path / "src/__init__.py").write_text("")
    (path / "src/cart.py").write_text(textwrap.dedent('''
        def total(items):
            """Tổng tiền giỏ hàng. BUG: bỏ qua quantity."""
            return sum(i["price"] for i in items)
    '''))
    (path / "tests/test_basic.py").write_text(textwrap.dedent('''
        from src.cart import total
        def test_single():
            assert total([{"price": 5, "quantity": 1}]) == 5
    '''))
    (path / ".gitignore").write_text("")            # cố tình KHÔNG ignore gì để bắt artefact
    sh(["git", "init", "-q", "-b", "main"], path)
    return git_commit(path, "init")


def main():
    shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
    origin = ROOT / "origin.git"; sh(["git", "init", "-q", "--bare", "-b", "main", str(origin)], ROOT)
    seed = ROOT / "seed"; make_project(seed)
    sh(["git", "remote", "add", "origin", str(origin)], seed); sh(["git", "push", "-q", "origin", "main"], seed)

    # repo mà watcher dùng: clone rồi ĐỨNG YÊN, trong khi origin nhận thêm commit
    repo = ROOT / "repo"; sh(["git", "clone", "-q", str(origin), str(repo)], ROOT)
    (seed / "README.md").write_text("mới hơn\n"); newer = git_commit(seed, "docs: readme"); sh(["git", "push", "-q", "origin", "main"], seed)
    assert sh(["git", "rev-parse", "HEAD"], repo) != newer, "repo local phải lạc hậu để kiểm (1)"

    prof_path = ROOT / "profile.yaml"
    prof_path.write_text(textwrap.dedent('''
        repo_id: e2e-offline
        base_branch: main
        allowed_paths: ["src/**", "tests/**"]
        forbidden_paths: ["pyproject.toml", "**/.gitlab-ci.yml"]
        commands:
          # Sau pytest cố tình đẻ artefact ngay trong cây code để kiểm (2)
          test: "python3 -m pytest -q --junitxml={junit} -o junit_family=xunit1; rc=$?; echo x > coverage.json; echo y > .coverage; exit $rc"
          lint: "python3 -m compileall -q src tests"
        checks:
          build: {status: out_of_scope, reason: python}
          test: {status: available}
          lint: {status: available}
          typecheck: {status: out_of_scope, reason: none}
          secret_scan: {status: out_of_scope, reason: none}
          sast: {status: out_of_scope, reason: none}
        limits: {run_timeout_min: 5}
        tracker: {kind: file, root: backlog}
        forge: {kind: file, root: backlog}
    '''))
    prof = profile_mod.load(prof_path)
    tracker = FileTracker(ROOT / "backlog"); forge = FileForge(ROOT / "backlog")

    SPEC = textwrap.dedent('''
        ```yaml
        task_id: ''
        objective: 'total() phải nhân price với quantity'
        task_type: T1
        acceptance_criteria:
          - {id: AC-1, text: 'total([{price: 5, quantity: 3}]) == 15'}
        repro_steps: ['gọi total với quantity 3']
        scope: {modules: ['src/cart.py']}
        readiness: {clear: pass, has_acceptance_criteria: pass, reproducible: pass, scoped: pass, deterministically_verifiable: pass}
        ```
    ''')

    def test_gen(cwd):
        (cwd / "tests/test_cart_qty.py").write_text(textwrap.dedent('''
            from src.cart import total
            def test_quantity():
                assert total([{"price": 5, "quantity": 3}]) == 15
        '''))
        return "đã viết tests/test_cart_qty.py"

    def implement(cwd):
        (cwd / "src/cart.py").write_text(textwrap.dedent('''
            def total(items):
                return sum(i["price"] * i.get("quantity", 1) for i in items)
        '''))
        return "đã sửa src/cart.py"

    agent = StubAgent({"intake": lambda cwd: SPEC, "discovery": lambda cwd: "## File liên quan\n- src/cart.py",
                       "planning": lambda cwd: "## Cách giải\nNhân quantity.\n## File sẽ đụng\n- src/cart.py",
                       "test_gen": test_gen, "implement": implement})
    orch = Orchestrator(prof=prof, profile_path=prof_path, repo=repo, tracker=tracker, agent=agent,
                        runs_root=ROOT / "runs", work_root=Path("work"), package_root=PKG, forge=forge,
                        log_level="warn", console=True)

    # ---- (3) ticket kẹt --------------------------------------------------
    stuck_old = tracker.create_ticket("kẹt có mốc cũ", "x", [L.RUNNING])
    tracker.comment(stuck_old.id, f"{RUNNING_MARK} {int(time.time()) - 3600 * 5} r-dead -->")
    stuck_nomark = tracker.create_ticket("kẹt không mốc", "x", [L.RUNNING])
    fresh = tracker.create_ticket("đang chạy thật", "x", [L.RUNNING])
    tracker.comment(fresh.id, f"{RUNNING_MARK} {int(time.time())} r-live -->")
    parent = tracker.create_ticket("gốc chờ con", "x", [L.RUNNING]); tracker.comment(parent.id, DETAIL_MARK)

    # ---- luồng chính -----------------------------------------------------
    t = tracker.create_ticket("Sửa total bỏ qua quantity", "Giỏ 3 món giá 5 ra 5 thay vì 15.", [L.TRY])
    done = orch.scan_once(); print("vòng 1:", done)
    assert tracker.get(stuck_old.id).labels == [L.NEEDS_HUMAN], tracker.get(stuck_old.id).labels
    assert tracker.get(stuck_nomark.id).labels == [L.NEEDS_HUMAN]
    assert tracker.get(fresh.id).labels == [L.RUNNING] and tracker.get(parent.id).labels == [L.RUNNING]
    assert tracker.get(t.id).labels == [L.PLAN_READY], tracker.get(t.id).labels
    plan_comment = [c for c in tracker.comments(t.id) if "e2ea:handoff" in c][0]
    assert newer[:12] in plan_comment, "(1) base phải là đầu nhánh trên ORIGIN, không phải HEAD local"
    print("(1) OK — base = origin tip", newer[:8])

    tracker.set_state(t.id, L.PLAN_APPROVED)
    # scan_once quét PLAN_APPROVED rồi IMPL trong cùng một vòng → promote + Phase B chạy liền
    done = orch.scan_once(); print("vòng 2:", done)
    children = lambda: [x for lb in L.STATE_LABELS for x in tracker.list_by_label(lb) if x.parent == t.id]
    assert len(children()) == 1, children()
    child = children()[0]
    assert child.labels == [L.MR_CREATED], (child.labels, tracker.comments(child.id)[-1])
    assert tracker.get(t.id).labels == [L.MR_CREATED]
    # (5) đặt lại plan-approved lần nữa → không đẻ thêm con
    tracker.set_state(t.id, L.PLAN_APPROVED); print("vòng 2b:", orch.scan_once())
    assert len(children()) == 1, "(5) đẻ hai ticket con"
    print("(5) OK — promote idempotent")
    mrs = list((ROOT / "backlog/mrs").glob("*.md")); assert len(mrs) == 1
    branch = [l for l in sh(["git", "branch", "--list", "agent/*"], repo).split() if l.startswith("agent/")][0]
    files = sh(["git", "ls-tree", "-r", "--name-only", branch], repo).split()
    bad = [f for f in files if f in ("coverage.json", ".coverage") or "__pycache__" in f or ".pytest_cache" in f]
    assert not bad, f"(2) artefact lọt vào commit: {bad}"
    assert "tests/test_cart_qty.py" in files
    print("(2) OK — commit sạch:", files)
    print("MR:", mrs[0].read_text()[:400].replace("\n", " | "))

    # ---- (4) plan lạc hậu ----------------------------------------------
    t2 = tracker.create_ticket("Ticket 2 sẽ lạc hậu", "mô tả", [L.TRY])
    orch.scan_once(); tracker.set_state(t2.id, L.PLAN_APPROVED)
    print("promote:", orch.promote(tracker.get(t2.id)))          # chỉ promote, chưa Phase B
    (seed / "src/cart.py").write_text((seed / "src/cart.py").read_text() + "\n# ai đó sửa trên main\n")
    git_commit(seed, "feat: đụng cart trên main"); sh(["git", "push", "-q", "origin", "main"], seed)
    done = orch.scan_once(); print("vòng 4:", done)
    assert any(o == Reason.HUMAN_PLAN_STALE.value for _, o in done), done
    print("(4) OK — HUMAN_PLAN_STALE nhờ so với origin")
    print("\nTẤT CẢ ĐẠT")


if __name__ == "__main__":
    main()
