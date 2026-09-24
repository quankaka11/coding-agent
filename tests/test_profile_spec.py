import pytest

from e2e_agent.agent import spec as spec_mod
from e2e_agent.config import profile as profile_mod
from e2e_agent.config.profile import ProfileError


def test_defaults_target_getting_mrs(profile_file):
    prof = profile_mod.load(profile_file())
    assert prof.mode == "relaxed" and prof.relaxed
    assert prof.auto_approve == []
    assert prof.limit("max_parallel") == 1
    assert prof.agent_cfg("sandbox") == "auto"
    assert "**/package.json" in prof.dependency_files


@pytest.mark.parametrize("extra, message", [
    ("conventions: {mode: loose}", "conventions.mode"),
    ("conventions: {auto_approve: [T9]}", "auto_approve"),
    ("limits: {max_parallel: 0}", "max_parallel"),
    ("agent: {sandbox: maybe}", "agent.sandbox"),
])
def test_invalid_new_keys_rejected(profile_file, extra, message):
    with pytest.raises(ProfileError, match=message):
        profile_mod.load(profile_file(extra))


def test_matches_handles_nested_dependency_files():
    deps = profile_mod.DEFAULT_DEPENDENCY_FILES
    assert profile_mod.matches("package.json", deps)
    assert profile_mod.matches("apps/web/package.json", deps)
    assert profile_mod.matches("requirements-dev.txt", deps)
    assert not profile_mod.matches("src/package_json.py", deps)


def _spec(**readiness) -> spec_mod.Spec:
    ready = {k: "pass" for k in spec_mod.READY_KEYS} | readiness
    return spec_mod.Spec(task_id="T-1", objective="x", task_type="T1",
                         acceptance_criteria=[{"id": "AC-1", "text": "x"}], readiness=ready)


def test_relaxed_only_blocks_on_clear_and_scoped():
    spec = _spec(reproducible="fail", deterministically_verifiable="fail")
    assert spec.blocking(relaxed=True) == []
    assert set(spec.soft_gaps(relaxed=True)) == {"reproducible", "deterministically_verifiable"}
    assert set(spec.blocking(relaxed=False)) == {"reproducible", "deterministically_verifiable"}
    assert _spec(clear="fail").blocking(relaxed=True) == ["clear"]
