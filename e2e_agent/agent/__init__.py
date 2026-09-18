"""Gọi Claude Code headless và đọc spec YAML nó sinh ra."""
from __future__ import annotations

from ..config.profile import EFFORT_LEVELS, ProfileError
from ..core.secrets import read_secret


def make(prof):
    from .headless import ClaudeCode
    # .env ghi đè hồ sơ: `agent_model=opus`, `agent_effort=high`.
    model = read_secret("agent_model") or prof.agent_cfg("model")
    effort = read_secret("agent_effort") or prof.agent_cfg("effort")
    if effort and effort not in EFFORT_LEVELS:
        raise ProfileError(f"agent_effort={effort!r} không hợp lệ, phải thuộc {EFFORT_LEVELS}")
    return ClaudeCode(binary=prof.agent_cfg("binary"), model=model, effort=effort,
                      permission_mode=prof.agent_cfg("permission_mode"),
                      allowed_tools=prof.agent_cfg("allowed_tools"),
                      timeout_sec=prof.agent_cfg("timeout_sec"))
