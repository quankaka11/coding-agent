import subprocess

import pytest

from e2e_agent.config import agentprofile, autoprofile
from e2e_agent.config import profile as profile_mod


def _repo(tmp_path, files):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.mark.parametrize("files, expected", [
    ({"pyproject.toml": "", "pkg/a.py": ""}, True),
    ({"src/app.py": ""}, True),
    ({"package.json": "{}", "run.py": "", "src/a.js": ""}, False),   # script .py lẻ không làm nó thành Python
    ({"go.mod": "module x", "main.go": ""}, False),
])
def test_is_python_repo(tmp_path, files, expected):
    assert autoprofile.is_python_repo(_repo(tmp_path, files)) is expected


PROPOSAL = {"language": "javascript", "setup": "true", "build": None,
            "test": "echo '<testsuite><testcase classname=\"a\" name=\"t\"/></testsuite>' > {junit}",
            "lint": "true", "allowed_paths": ["src/**"], "test_globs": ["test/**"],
            "extra_forbidden": ["migrations/**"], "verified": True, "notes": ["n"]}


def test_other_language_profile_from_agent_proposal_is_verified_and_valid(tmp_path):
    repo = _repo(tmp_path / "r", {"package.json": "{}", "src/a.js": ""})
    draft = autoprofile.generate(repo, "r", suggest=lambda r: (dict(PROPOSAL), ""))
    assert draft.todos == []
    assert any("test: 1 test" in n for n in draft.notes)
    out = tmp_path / "p.yaml"
    out.write_text(draft.dumps())
    prof = profile_mod.load(out)
    assert prof.commands["setup"] == "true" and "migrations/**" in prof.forbidden_paths
    assert prof.checks["build"]["status"] == "out_of_scope" and prof.mode == "relaxed"


def test_other_language_broken_test_command_becomes_todo(tmp_path):
    repo = _repo(tmp_path / "r", {"package.json": "{}"})
    bad = dict(PROPOSAL, test="exit 3 # {junit}")
    draft = autoprofile.generate(repo, "r", suggest=lambda r: (bad, ""))
    assert any("commands.test không chạy được" in t for t in draft.todos)


def test_other_language_without_agent_leaves_todo(tmp_path):
    repo = _repo(tmp_path / "r", {"go.mod": "module x"})
    draft = autoprofile.generate(repo, "r", suggest=None)
    assert draft.todos and "không phải Python" in draft.todos[0]


@pytest.mark.parametrize("text, message", [
    ("```yaml\ntest: 'npm test'\nlint: 'x'\nallowed_paths: [src/**]\n```", "{junit}"),
    ("```yaml\ntest: 'x {junit}'\nallowed_paths: [src/**]\n```", "lint"),
    ("```yaml\ntest: 'x {junit}'\nlint: 'x'\n```", "allowed_paths"),
    ("không phải yaml: [", "YAML"),
])
def test_agentprofile_parse_rejects_bad_proposals(text, message):
    with pytest.raises(ValueError, match=message):
        agentprofile.parse(text)


def test_src_layout_gets_pythonpath(tmp_path):
    repo = _repo(tmp_path / "r", {"pyproject.toml": "", "src/pkg/__init__.py": "", "tests/test_a.py": ""})
    draft = autoprofile.generate(repo, "r")
    assert draft.profile["commands"]["test"].startswith("PYTHONPATH=src")
