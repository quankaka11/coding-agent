"""Chạy trọn vòng ticket → MR OFFLINE: repo giả + origin giả + StubAgent + tracker/forge trên đĩa.

Không cần LLM, không cần mạng, không cần Backlog/GitLab. Dùng để kiểm hệ thống còn
chạy đúng sau khi sửa code:   python3 scripts/e2e_offline.py

Kiểm: (1) base lấy từ origin (repo local cố tình lạc hậu); (2) artefact test không lọt
vào commit, worktree của run ra MR được dọn; (3) ticket kẹt ở agent:running được giao cho
người; (4) plan lạc hậu → ticket tự quay về agent:try, lập plan lại, duyệt lại thì ra MR;
(5) duyệt lại plan đã ra MR không mở MR thứ hai; (6) G-10 fail → agent tự sửa rồi vẫn ra
MR; (7) lint đỏ sẵn → commit riêng; (8) test pass sẵn → agent viết lại rồi ra MR;
(9) relaxed + T4 → Draft MR; (10) strict + T4 → NO_MR; (11) tự duyệt T1 và (12) hai ticket
chạy song song; (13) relaxed + test pass sẵn mãi → Draft MR; (14) đổi file dependency →
G-11 cần review; (15) agent không đổi code → NO_MR/no_change; (16) agent hỏi → người trả
lời bằng comment → chạy lại thì agent đọc được, không hỏi lại; comment sau khi duyệt đi vào
prompt implement và mô tả MR. Mọi việc diễn ra trên MỘT
ticket (không còn ticket [impl] con). Mọi thứ ghi vào work/e2e-offline/ (đã gitignore).
"""
import json, shutil, subprocess, sys, textwrap, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "work" / "e2e-offline"
PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from e2e_agent.agent.headless import StubAgent
from e2e_agent.config import profile as profile_mod
from e2e_agent.core.reasons import Reason
from e2e_agent.forge.file import FileForge
from e2e_agent.pipeline.orchestrator import Orchestrator, RUNNING_MARK, DETAIL_MARK, MR_MARK
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
    profile_mod.load(prof_path)                  # hồ sơ gốc phải hợp lệ trước khi dựng biến thể
    tracker = FileTracker(ROOT / "backlog"); forge = FileForge(ROOT / "backlog")

    def spec_text(task_type="T1"):
        return textwrap.dedent(f"""
            ```yaml
            task_id: ''
            objective: 'total() phải nhân price với quantity'
            task_type: {task_type}
            acceptance_criteria:
              - {{id: AC-1, text: 'total([{{price: 5, quantity: 3}}]) == 15'}}
            repro_steps: ['gọi total với quantity 3']
            scope: {{modules: ['src/cart.py']}}
            readiness: {{clear: pass, has_acceptance_criteria: pass, reproducible: pass, scoped: pass, deterministically_verifiable: pass}}
            ```
        """)

    state = {"violate": False,           # (6): lượt implement đầu cố tình đụng ngoài phạm vi plan
             "weak_test": False,         # (8): lượt test_gen đầu viết test pass sẵn trên code cũ
             "always_weak": False,       # (13): mọi lượt test_gen đều viết test pass sẵn
             "task_type": "T1",          # (9)(10): intake trả về loại task này
             "deps": False,              # (14): implement thêm requirements.txt
             "noop": False,              # (15): implement không sửa gì
             "ask": False}               # (16): intake hỏi lại cho tới khi prompt có câu trả lời

    def test_gen(cwd):
        if state["weak_test"] or state["always_weak"]:
            state["weak_test"] = False   # lượt sau nhận feedback "pass sẵn" thì viết đúng
            (cwd / "tests/test_cart_qty.py").write_text(textwrap.dedent('''
                from src.cart import total
                def test_quantity():
                    assert total([{"price": 5, "quantity": 1}]) == 5   # pass trên code cũ
            '''))
            return "đã viết test (yếu)"
        (cwd / "tests/test_cart_qty.py").write_text(textwrap.dedent('''
            from src.cart import total
            def test_quantity():
                assert total([{"price": 5, "quantity": 3}]) == 15
        '''))
        return "đã viết tests/test_cart_qty.py"

    def implement(cwd):
        if state["noop"]:
            return "code đã đúng, không sửa gì"
        (cwd / "src/cart.py").write_text(textwrap.dedent('''
            def total(items):
                return sum(i["price"] * i.get("quantity", 1) for i in items)
        '''))
        if state["deps"]:
            (cwd / "requirements.txt").write_text("requests==2.32.3\n")
        extra = cwd / "src/extra.py"
        if state["violate"] and not extra.exists():
            extra.write_text("# file ngoài scope.modules nhưng trong allowed_paths\n")
            return "đã sửa src/cart.py và (lỡ) thêm src/extra.py"
        if extra.exists():                 # lượt khắc phục: nhận feedback G-10 → hoàn nguyên
            extra.unlink()
            return "đã xoá src/extra.py theo feedback G-10"
        return "đã sửa src/cart.py"

    def lint_fix(cwd):
        (cwd / "src/broken.py").write_text("def broken():\n    pass\n")
        return "đã sửa src/broken.py"

    ANSWER = "quantity không có thì coi là 1"
    NOT_READY = textwrap.dedent("""
        ```yaml
        task_id: ''
        objective: 'total() tính theo quantity'
        task_type: T1
        acceptance_criteria:
          - {id: AC-1, text: 'total([{price: 5, quantity: 3}]) == 15'}
        scope: {modules: ['src/cart.py']}
        readiness: {clear: fail, has_acceptance_criteria: pass, reproducible: pass, scoped: pass, deterministically_verifiable: pass}
        readiness_notes: {clear: 'không rõ quantity thiếu thì tính thế nào'}
        questions: ['Món không có quantity thì tính là bao nhiêu?']
        ```
    """)

    def intake(cwd):
        # (16) Agent giả đọc chính prompt nó nhận: chưa thấy câu trả lời của người → hỏi.
        if state["ask"] and ANSWER not in agent.prompts["intake"]:
            return NOT_READY
        return spec_text(state["task_type"])

    agent = StubAgent({"intake": intake,
                       "discovery": lambda cwd: "## File liên quan\n- src/cart.py",
                       "planning": lambda cwd: "## Giải pháp\nNhân quantity.\n## File sẽ thay đổi\n- src/cart.py\n"
                                               "## Test sẽ viết\n- tests/test_cart_qty.py::test_quantity — AC-1\n"
                                               "## Rủi ro\n- không",
                       "test_gen": test_gen, "implement": implement, "lint_fix": lint_fix})

    def make_orch(profile_path):
        return Orchestrator(prof=profile_mod.load(profile_path), profile_path=profile_path, repo=repo,
                            tracker=tracker, agent=agent, runs_root=ROOT / "runs", work_root=Path("work"),
                            package_root=PKG, forge=forge, log_level="warn", console=True)

    def variant(name, extra_yaml):
        """Hồ sơ biến thể: hồ sơ gốc + vài khoá yaml ghi đè (conventions/limits)."""
        import yaml
        raw = yaml.safe_load(prof_path.read_text())
        for key, val in yaml.safe_load(extra_yaml).items():
            raw[key] = {**(raw.get(key) or {}), **val} if isinstance(val, dict) else val
        path = ROOT / f"profile-{name}.yaml"; path.write_text(yaml.safe_dump(raw, allow_unicode=True))
        return make_orch(path)

    orch = make_orch(prof_path)

    def mrs():
        return sorted((ROOT / "backlog/mrs").glob("*.md"))

    def last_run(ticket_id):
        return max((ROOT / "runs" / ticket_id).glob("*/outcome.json"), key=lambda p: p.stat().st_mtime).parent

    def events(run_dir):
        return [json.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines() if l.strip()]

    # ---- (3) ticket kẹt --------------------------------------------------
    stuck_old = tracker.create_ticket("kẹt có mốc cũ", "x", [L.RUNNING])
    tracker.comment(stuck_old.id, f"{RUNNING_MARK} {int(time.time()) - 3600 * 5} r-dead -->")
    stuck_nomark = tracker.create_ticket("kẹt không mốc", "x", [L.RUNNING])
    fresh = tracker.create_ticket("đang chạy thật", "x", [L.RUNNING])
    tracker.comment(fresh.id, f"{RUNNING_MARK} {int(time.time())} r-live -->")
    # Ticket gốc chờ ticket [impl] của phiên bản cũ: vẫn phải được để yên (tương thích).
    parent = tracker.create_ticket("gốc chờ con", "x", [L.RUNNING]); tracker.comment(parent.id, DETAIL_MARK)

    # ---- luồng chính: MỘT ticket từ đầu tới MR ---------------------------
    t = tracker.create_ticket("Sửa total bỏ qua quantity", "Giỏ 3 món giá 5 ra 5 thay vì 15.", [L.TRY])
    done = orch.scan_once(); print("vòng 1:", done)
    assert tracker.get(stuck_old.id).labels == [L.NEEDS_HUMAN], tracker.get(stuck_old.id).labels
    assert tracker.get(stuck_nomark.id).labels == [L.NEEDS_HUMAN]
    assert tracker.get(fresh.id).labels == [L.RUNNING] and tracker.get(parent.id).labels == [L.RUNNING]
    assert tracker.get(t.id).labels == [L.PLAN_READY], tracker.get(t.id).labels
    plan_comment = [c for c in tracker.comments(t.id) if "e2ea:handoff" in c][0]
    assert newer[:12] in plan_comment, "(1) base phải là đầu nhánh trên ORIGIN, không phải HEAD local"
    print("(1) OK — base = origin tip", newer[:8])

    tickets_before = len(list((ROOT / "backlog/tickets").glob("*.yaml")))
    tracker.set_state(t.id, L.PLAN_APPROVED)
    done = orch.scan_once(); print("vòng 2:", done)
    assert tracker.get(t.id).labels == [L.MR_CREATED], (tracker.get(t.id).labels, tracker.comments(t.id)[-1])
    assert len(list((ROOT / "backlog/tickets").glob("*.yaml"))) == tickets_before, "không được đẻ ticket con"
    assert any(MR_MARK in c for c in tracker.comments(t.id)), "link MR phải nằm trên ticket"
    assert len(mrs()) == 1
    # (5) đặt lại plan-approved lần nữa → không mở MR thứ hai
    tracker.set_state(t.id, L.PLAN_APPROVED); print("vòng 2b:", orch.scan_once())
    assert len(mrs()) == 1 and tracker.get(t.id).labels == [L.MR_CREATED], "(5) mở hai MR cho một plan"
    print("(5) OK — duyệt lại plan đã ra MR không mở MR mới")
    branch = [l for l in sh(["git", "branch", "--list", f"agent/{t.id}-*"], repo).split() if l.startswith("agent/")][0]
    files = sh(["git", "ls-tree", "-r", "--name-only", branch], repo).split()
    bad = [f for f in files if f in ("coverage.json", ".coverage") or "__pycache__" in f or ".pytest_cache" in f]
    assert not bad, f"(2) artefact lọt vào commit: {bad}"
    assert "tests/test_cart_qty.py" in files
    assert not list((repo / "work").glob(f"agent_{t.id}-*")), "worktree của run ra MR phải được dọn"
    print("(2) OK — commit sạch, worktree đã dọn:", files)
    print("MR:", mrs()[0].read_text()[:300].replace("\n", " | "))

    # ---- (4) plan lạc hậu ----------------------------------------------
    t2 = tracker.create_ticket("Ticket 2 sẽ lạc hậu", "mô tả", [L.TRY])
    orch.scan_once()                                            # Phase A trên code hiện tại
    (seed / "src/cart.py").write_text((seed / "src/cart.py").read_text() + "\n# ai đó sửa trên main\n")
    git_commit(seed, "feat: đụng cart trên main"); sh(["git", "push", "-q", "origin", "main"], seed)
    tracker.set_state(t2.id, L.PLAN_APPROVED)
    done = orch.scan_once(); print("vòng 4:", done)
    assert any(o == Reason.NO_MR.value for _, o in done), done
    assert tracker.get(t2.id).labels == [L.TRY], "(4) ticket phải tự quay về agent:try để lập plan lại"
    assert "plan_stale" in tracker.comments(t2.id)[-1], tracker.comments(t2.id)[-1]
    done = orch.scan_once(); print("vòng 4b:", done)            # Phase A chạy lại trên code mới
    assert tracker.get(t2.id).labels == [L.PLAN_READY], tracker.get(t2.id).labels
    tracker.set_state(t2.id, L.PLAN_APPROVED)
    done = orch.scan_once(); print("vòng 4c:", done)            # plan mới được duyệt → MR
    assert tracker.get(t2.id).labels == [L.MR_CREATED], tracker.get(t2.id).labels
    print("(4) OK — plan lạc hậu → về agent:try, plan lại, duyệt lại → MR")

    def run_ticket(title, o=None):
        """Tạo ticket → Phase A → duyệt → Phase B. Trả về (ticket, run_dir của lần chạy cuối)."""
        o = o or orch
        t = tracker.create_ticket(title, "Giỏ 3 món giá 5 ra 5 thay vì 15.", [L.TRY])
        o.scan_once()
        if tracker.get(t.id).labels == [L.PLAN_READY]:
            tracker.set_state(t.id, L.PLAN_APPROVED)
            done = o.scan_once(); print(f"vòng [{title}]:", done)
        return tracker.get(t.id), last_run(t.id)

    def mr_of(ticket_id):
        return [m for m in mrs() if f"[{ticket_id}]" in m.read_text().splitlines()[0]][-1].read_text()

    # ---- (6) anti-gaming fail mà agent sửa được → agent tự sửa, vẫn ra MR ----------
    state["violate"] = True
    t6, run6 = run_ticket("Ticket 6: agent lỡ đụng ngoài phạm vi plan")
    state["violate"] = False
    assert t6.labels == [L.MR_CREATED], (t6.labels, tracker.comments(t6.id)[-1])
    ev6 = json.loads((run6 / "evidence.json").read_text())
    ag6 = json.loads((run6 / "antigaming-report.json").read_text())
    retries = [e for e in events(run6) if e["event"] == "antigaming.retry"]
    assert retries and "G-10" in retries[0]["data"]["failed_rules"], retries
    assert ev6["self_fix"]["antigaming_rounds"] == 1 and ag6["verdict"] == "PASS", (ev6["self_fix"], ag6["verdict"])
    branch6 = sh(["git", "for-each-ref", "--format=%(refname:short)", f"refs/heads/agent/{t6.id}-*"], repo).split()[0]
    assert "src/extra.py" not in sh(["git", "ls-tree", "-r", "--name-only", branch6], repo).split()
    assert "Vòng khắc phục anti-gaming: 1" in mr_of(t6.id)
    print("(6) OK — G-10 fail → agent tự sửa → MR, không NEEDS_HUMAN")

    # ---- (7) lint đỏ sẵn → agent sửa bằng commit riêng, không NO_MR -----------------
    (seed / "src/broken.py").write_text("def broken(:\n    pass\n")
    git_commit(seed, "chore: lỗi cú pháp sẵn trên main"); sh(["git", "push", "-q", "origin", "main"], seed)
    t7, run7 = run_ticket("Ticket 7: lint đỏ sẵn")
    assert t7.labels == [L.MR_CREATED], (t7.labels, tracker.comments(t7.id)[-1])
    ev7 = json.loads((run7 / "evidence.json").read_text())
    assert ev7["self_fix"]["lint"] and "src/broken.py" in ev7["lint_fix"]["files"], ev7
    branch7 = sh(["git", "for-each-ref", "--format=%(refname:short)", f"refs/heads/agent/{t7.id}-*"], repo).split()[0]
    subjects = sh(["git", "log", "--format=%s", f"origin/main..{branch7}"], repo).splitlines()
    assert subjects[-1].startswith("chore: sửa lint") and any(x.startswith("test:") for x in subjects) \
        and any(x.startswith("fix:") for x in subjects), subjects
    print("(7) OK — lint đỏ sẵn → commit riêng", subjects[::-1])

    # ---- (8) test viết ra pass sẵn trên code cũ → agent viết lại, không NO_MR ngay ----
    state["weak_test"] = True
    t8, run8 = run_ticket("Ticket 8: test đầu pass sẵn")
    assert t8.labels == [L.MR_CREATED], (t8.labels, tracker.comments(t8.id)[-1])
    retries8 = [e for e in events(run8) if e["event"] == "testgen.retry"]
    assert retries8 and retries8[0]["data"]["problem"] == "test_passes_pre", retries8
    assert json.loads((run8 / "evidence.json").read_text())["self_fix"]["testgen_rounds"] == 1
    assert "Draft" not in mr_of(t8.id).splitlines()[0], "có bằng chứng thật thì không phải Draft"
    print("(8) OK — test pass sẵn → agent viết lại → MR thường")

    # ---- (9) relaxed + T4 → vẫn làm, MR Draft -----------------------------------------
    state["task_type"] = "T4"
    t9, run9 = run_ticket("Ticket 9: T4 ở chế độ relaxed")
    assert t9.labels == [L.MR_CREATED], (t9.labels, tracker.comments(t9.id)[-1])
    mr9 = mr_of(t9.id)
    assert mr9.startswith("# Draft: ") and "Vì sao MR này là Draft" in mr9, mr9[:400]
    ag9 = json.loads((run9 / "antigaming-report.json").read_text())
    assert ag9["rules"]["G-4"]["status"] == "needs_review", ag9["rules"]["G-4"]
    assert any("MR Draft đã mở" in c for c in tracker.comments(t9.id))
    print("(9) OK — T4 relaxed → Draft MR, G-4 cần review")

    # ---- (10) strict + T4 → NO_MR ở Phase A -------------------------------------------
    strict = variant("strict", "conventions: {mode: strict}")
    t10 = tracker.create_ticket("Ticket 10: T4 ở chế độ strict", "x", [L.TRY])
    print("vòng [strict T4]:", strict.scan_once())
    state["task_type"] = "T1"
    assert tracker.get(t10.id).labels == [L.NO_MR], tracker.get(t10.id).labels
    print("(10) OK — strict + T4 → NO_MR")

    # ---- (11)(12) tự duyệt T1 + hai ticket song song ----------------------------------
    fast = variant("fast", "conventions: {auto_approve: [T1]}\nlimits: {max_parallel: 2}")
    ta = tracker.create_ticket("Ticket 11a: tự duyệt", "Giỏ 3 món giá 5 ra 5 thay vì 15.", [L.TRY])
    tb = tracker.create_ticket("Ticket 11b: tự duyệt", "Giỏ 3 món giá 5 ra 5 thay vì 15.", [L.TRY])
    done = fast.scan_once(); print("vòng [song song A]:", done)
    assert {x for x, _ in done} == {ta.id, tb.id}, "(12) hai ticket phải chạy Phase A trong cùng một vòng"
    assert tracker.get(ta.id).labels == [L.PLAN_APPROVED], "(11) T1 phải được tự duyệt"
    done = fast.scan_once(); print("vòng [song song B]:", done)
    assert {x for x, _ in done} == {ta.id, tb.id} and all(o == "OK" for _, o in done), done
    assert tracker.get(ta.id).labels == tracker.get(tb.id).labels == [L.MR_CREATED]
    spans = []
    for tid in (ta.id, tb.id):
        evs = events(last_run(tid))
        spans.append((min(e["ts"] for e in evs), max(e["ts"] for e in evs)))
    assert max(s for s, _ in spans) <= min(e for _, e in spans), f"(12) hai run phải chồng nhau: {spans}"
    fast.drain()
    print("(11)(12) OK — T1 tự duyệt, hai ticket chạy song song, cả hai ra MR")

    # ---- (13) relaxed + test pass sẵn mãi → Draft MR, giữ test hồi quy -----------------
    state["always_weak"] = True
    t13, _ = run_ticket("Ticket 13: test luôn pass sẵn")
    state["always_weak"] = False
    assert t13.labels == [L.MR_CREATED], (t13.labels, tracker.comments(t13.id)[-1])
    assert mr_of(t13.id).startswith("# Draft: "), mr_of(t13.id)[:200]
    print("(13) OK — test pass sẵn sau mọi lượt → Draft MR thay vì NO_MR")

    # ---- (14) đổi file dependency → G-11 cần review, không chặn -------------------------
    state["deps"] = True
    t14, run14 = run_ticket("Ticket 14: thêm thư viện")
    state["deps"] = False
    assert t14.labels == [L.MR_CREATED], (t14.labels, tracker.comments(t14.id)[-1])
    ag14 = json.loads((run14 / "antigaming-report.json").read_text())
    assert ag14["rules"]["G-11"]["status"] == "needs_review" and ag14["rules"]["G-3"]["status"] == "pass", ag14["rules"]
    print("(14) OK — requirements.txt đổi → G-11 cần review, G-3 không bắt oan")

    # ---- (15) gate xanh mà agent không đổi code → NO_MR/no_change ----------------------
    # (T4: không có test đỏ nào ép phải sửa, nên "không sửa gì" vẫn qua gate.)
    state["noop"], state["task_type"] = True, "T4"
    t15, _ = run_ticket("Ticket 15: agent không sửa gì")
    state["noop"], state["task_type"] = False, "T1"
    assert t15.labels == [L.NO_MR] and "no_change" in tracker.comments(t15.id)[-1], tracker.comments(t15.id)[-1]
    print("(15) OK — không đổi code nguồn → NO_MR/no_change")

    # ---- (16) agent hỏi → người trả lời bằng comment → agent đọc được, không hỏi lại ------
    state["ask"] = True
    t16 = tracker.create_ticket("Ticket 16: thiếu thông tin", "total() sai khi có quantity.", [L.TRY])
    orch.scan_once()
    assert tracker.get(t16.id).labels == [L.NO_MR] and "not_ready" in tracker.comments(t16.id)[-1]
    tracker.comment(t16.id, ANSWER)                     # người trả lời ngay trên ticket
    tracker.set_state(t16.id, L.TRY)
    print("vòng [16 hỏi lại]:", orch.scan_once())
    assert tracker.get(t16.id).labels == [L.PLAN_READY], (tracker.get(t16.id).labels, tracker.comments(t16.id)[-1])
    assert "Món không có quantity" in agent.prompts["intake"], "prompt phải có cả câu agent đã hỏi"
    assert ANSWER in agent.prompts["planning"], "planning cũng phải thấy câu trả lời"
    state["ask"] = False
    # Người bổ sung SAU khi duyệt → vào prompt implement và mô tả MR
    NOTE = "giữ nguyên chữ ký hàm total(items)"
    tracker.comment(t16.id, NOTE)
    tracker.set_state(t16.id, L.PLAN_APPROVED)
    print("vòng [16 phase B]:", orch.scan_once())
    assert tracker.get(t16.id).labels == [L.MR_CREATED], tracker.comments(t16.id)[-1]
    impl_prompt = next(v for k, v in agent.prompts.items() if k.startswith("implement"))
    assert NOTE in agent.prompts["implement-1"], impl_prompt[-500:]
    assert ANSWER not in agent.prompts["implement-1"], "comment TRƯỚC plan đã nằm trong plan, không lặp lại"
    assert NOTE not in agent.prompts["test_gen"], "prompt viết test không được thấy chỉ dẫn cách sửa"
    assert NOTE in mr_of(t16.id)
    print("(16) OK — agent đọc câu trả lời trong comment, không hỏi lại; comment sau duyệt vào implement + MR")

    # Mọi kết cục chỉ thuộc 4 loại; ticket cần người chỉ khi thật sự cần (kẹt run, sai category).
    outcomes = {json.loads(p.read_text())["reason"] for p in (ROOT / "runs").glob("*/*/outcome.json")}
    assert outcomes <= {"OK", "NO_MR", "NEEDS_HUMAN", "ERROR"}, outcomes
    print("\nTẤT CẢ ĐẠT")


if __name__ == "__main__":
    main()
