"""Gọi Claude Code headless và đọc spec YAML nó sinh ra."""
from __future__ import annotations


def make(prof):
    from .headless import ClaudeCode
    return ClaudeCode(binary=prof.agent_cfg("binary"), model=prof.agent_cfg("model"),
                      permission_mode=prof.agent_cfg("permission_mode"),
                      allowed_tools=prof.agent_cfg("allowed_tools"),
                      timeout_sec=prof.agent_cfg("timeout_sec"))
