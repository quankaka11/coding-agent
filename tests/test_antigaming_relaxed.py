from pathlib import Path

from e2e_agent.config import profile as profile_mod
from e2e_agent.enforce import antigaming as ag


def _ctx(profile_file, changed, extra="", **kw):
    prof = profile_mod.load(profile_file(extra))
    return ag.Context(prof=prof, repo=Path("."), base_sha="x", baseline=None, gate_report={},
                      changed=changed, new_lines={}, evidence=kw.pop("evidence", {}), **kw)


def test_g3_does_not_flag_dependency_file_outside_allowed(profile_file):
    c = _ctx(profile_file, ["src/a.py", "package.json", "docs/x.md"])
    r = ag.g3_forbidden_paths(c)
    assert r.status == ag.FAIL and r.evidence["outside_allowed"] == ["docs/x.md"]
    assert ag.g3_forbidden_paths(_ctx(profile_file, ["src/a.py", "package.json"])).status == ag.PASS


def test_g11_needs_review_when_relaxed_fail_when_strict(profile_file):
    changed = ["src/a.py", "requirements.txt"]
    assert ag.g11_dependencies(_ctx(profile_file, changed)).status == ag.NEEDS_REVIEW
    strict = ag.g11_dependencies(_ctx(profile_file, changed, "conventions: {mode: strict}"))
    assert strict.status == ag.FAIL and strict.remedy == ag.IMPLEMENT
    assert ag.g11_dependencies(_ctx(profile_file, ["src/a.py"])).status == ag.PASS


def test_g10_exempts_dependency_files(profile_file):
    c = _ctx(profile_file, ["src/cart.py", "pyproject.toml"], modules=["src/cart.py"])
    assert ag.g10_plan_scope(c).status == ag.PASS


def test_g4_no_proof_is_review_not_blocking(profile_file):
    c = _ctx(profile_file, ["src/a.py"], evidence={"no_proof": "task T4"})
    r = ag.g4_fail_before_pass_after(c)
    assert r.status == ag.NEEDS_REVIEW and r.remedy is None


def test_g8_finds_ac_ids_in_non_python_tests(profile_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "cart.test.ts").write_text('it("AC-1: tổng nhân quantity", () => {})\n')
    c = _ctx(profile_file, ["tests/cart.test.ts"], task_type="T3", acceptance_ids=["AC-1", "AC-2"])
    r = ag.g8_ac_markers(c)
    assert r.evidence["found"] == ["AC-1"] and r.evidence["missing"] == ["AC-2"]


def _report(*results):
    return ag.summarize(list(results))


def test_soften_only_touches_soft_rules_with_remedy():
    report = _report(ag.RuleResult("G-7", ag.FAIL, "thiếu", remedy=ag.TEST_GEN),
                     ag.RuleResult("G-10", ag.FAIL, "ngoài", remedy=ag.IMPLEMENT),
                     ag.RuleResult("G-1", ag.PASS, "ok"))
    assert ag.soften(report) == ["G-7", "G-10"]
    assert report["verdict"] == "PASS" and set(report["needs_review"]) == {"G-7", "G-10"}


def test_soften_never_downgrades_safety_rules():
    report = _report(ag.RuleResult("G-3", ag.FAIL, "ngoài vùng", remedy=ag.IMPLEMENT),
                     ag.RuleResult("G-7", ag.FAIL, "thiếu", remedy=ag.TEST_GEN))
    assert ag.soften(report) == ["G-7"]
    assert report["verdict"] == "FAIL" and report["failed_rules"] == ["G-3"]
    blocking = _report(ag.RuleResult("G-4", ag.FAIL, "thiếu bằng chứng"))   # không remedy
    assert ag.soften(blocking) == [] and blocking["blocking_rules"] == ["G-4"]
