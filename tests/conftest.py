import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def profile_file(tmp_path):
    """Hồ sơ tối thiểu hợp lệ; `extra` là yaml nối thêm vào cuối."""
    def make(extra: str = "") -> Path:
        path = tmp_path / "profile.yaml"
        path.write_text(textwrap.dedent('''
            repo_id: t
            allowed_paths: ["src/**", "tests/**"]
            forbidden_paths: ["**/.gitlab-ci.yml"]
            commands: {test: "pytest --junitxml={junit}", lint: "true"}
            checks:
              build: {status: out_of_scope, reason: x}
              test: {status: available}
              lint: {status: available}
        ''') + textwrap.dedent(extra))
        return path
    return make
