"""Trọn vòng ticket → MR offline (StubAgent, tracker/forge trên đĩa) — 15 kịch bản."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.slow
def test_e2e_offline_all_scenarios_pass():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "e2e_offline.py")],
                          capture_output=True, text=True, timeout=900, cwd=ROOT)
    tail = (proc.stdout + proc.stderr)[-3000:]
    assert proc.returncode == 0 and "TẤT CẢ ĐẠT" in proc.stdout, tail
