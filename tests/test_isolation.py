"""P0: agent không được với tới token — môi trường, file, hook."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from e2e_agent.core import secrets
from e2e_agent.pipeline import sandbox

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("gitlab_token", "G")
    monkeypatch.setenv("BACKLOG_API_KEY", "B")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "A")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "C")
    monkeypatch.setenv("MY_CUSTOM_TOKEN", "X")
    monkeypatch.setenv("PATH", os.environ["PATH"])


def test_command_env_strips_system_tokens_and_claude_credentials(env):
    e = secrets.command_env({"COVERAGE_FILE": "/x"})
    assert not {"gitlab_token", "BACKLOG_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"} & set(e)
    assert e["COVERAGE_FILE"] == "/x" and "PATH" in e


def test_agent_env_keeps_claude_credentials_only(env):
    e = secrets.agent_env()
    assert "gitlab_token" not in e and "BACKLOG_API_KEY" not in e
    assert e["ANTHROPIC_API_KEY"] == "A" and e["CLAUDE_CODE_OAUTH_TOKEN"] == "C"
    # Không có sandbox đầy đủ thì KHÔNG bật scrub: nó ép sandbox và làm Bash hỏng (đã thấy thật).
    assert "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB" not in e
    assert secrets.agent_env(scrub=True)["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"


def test_protect_adds_profile_declared_names(env):
    assert "MY_CUSTOM_TOKEN" in secrets.command_env()
    secrets.protect("my_custom_token")
    assert "MY_CUSTOM_TOKEN" not in secrets.command_env()


def test_run_context_cmd_uses_clean_env_by_default(env, tmp_path):
    from e2e_agent.core.run import RunContext
    ctx = RunContext(run_dir=tmp_path / "run", console=False)
    out = ctx.cmd("env", cwd=tmp_path).stdout
    assert "gitlab_token=" not in out and "ANTHROPIC_API_KEY=" not in out
    ctx.log.close()


def test_agent_settings_deny_secret_files(monkeypatch, tmp_path):
    envfile = tmp_path / ".env"
    envfile.write_text("gitlab_token=x\n")
    monkeypatch.setattr(secrets, "EXTRA_ENV_FILES", [envfile])
    monkeypatch.setattr(sandbox, "_SANDBOX_CHECK", (None, "thiếu socat"))
    st = sandbox.agent_settings(None, "hook")
    deny = st["permissions"]["deny"]
    assert f"Read(//{envfile.as_posix().lstrip('/')})" in deny
    assert f"Read(//{(tmp_path / '.e2ea').as_posix().lstrip('/')}/**)" in deny
    assert "Read(~/.ssh/**)" in deny and "Read(~/.claude/.credentials.json)" in deny
    assert "sandbox" not in st                       # auto + máy không có → không bật


@pytest.mark.parametrize("level, weak", [("full", False), ("weak", True)])
def test_agent_settings_enable_sandbox_when_available(monkeypatch, level, weak):
    monkeypatch.setattr(sandbox, "_SANDBOX_CHECK", (level, "x"))
    sb = sandbox.agent_settings(None, "hook")["sandbox"]
    assert sb["enabled"] and sb["allowUnsandboxedCommands"] is False
    assert sb["enableWeakerNestedSandbox"] is weak
    assert "registry.npmjs.org" in sb["network"]["allowedDomains"]
    assert "~/.ssh" in sb["filesystem"]["denyRead"]


def _hook(tmp_path, target, profile):
    payload = json.dumps({"cwd": str(tmp_path), "tool_input": {"file_path": target}})
    proc = subprocess.run([sys.executable, "-m", "e2e_agent.hooks.forbidden_paths", "--profile", str(profile)],
                          input=payload, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})
    return proc.returncode


def test_hook_blocks_writes_outside_worktree(tmp_path, profile_file):
    prof = profile_file()
    work = tmp_path / "wt"
    work.mkdir()
    assert _hook(work, "src/a.py", prof) == 0
    assert _hook(work, "/tmp/scratch.txt", prof) == 0
    assert _hook(work, "../.env", prof) == 2
    assert _hook(work, str(Path.home() / ".bashrc"), prof) == 2
    assert _hook(work, ".gitlab-ci.yml", prof) == 2
