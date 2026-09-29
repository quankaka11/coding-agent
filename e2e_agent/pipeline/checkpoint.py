"""Checkpoint Phase B — người sửa xong nguyên nhân, chuyển ticket về cho agent, agent làm TIẾP.

Trước đây mỗi lần chạy lại Phase B là một worktree mới từ base: token hết hạn đúng ở bước
mở MR là agent viết lại từ đầu thứ code đã qua gate và anti-gaming. Ở đây mỗi bước xong
ghi lại một mốc; lần sau dựng lại worktree tại đúng commit đó và bỏ qua những gì đã xong.

Nguyên tắc:
- Bước dùng LLM (sửa lint, viết test, implement) → giữ kết quả, tức là các commit.
- Kiểm tra tất định (gate, anti-gaming) → chạy lại trên HEAD, trừ khi code, hồ sơ repo
  và comment của người đều y nguyên so với lần đã chấm (khi đó kết quả cũ vẫn đúng).
- Không tin được bằng chứng (anti-gaming chặn cứng) hay plan đã đổi → không resume,
  chạy lại Phase B từ đầu với plan đã duyệt.

Commit giữ sống bằng ref `refs/e2ea/<ticket>` trong repo gốc: `e2ea clean` xoá worktree
và nhánh `agent/*` cũng không mất. Sự thật là `head_sha` ghi trong file checkpoint (nằm
trong `runs/`, agent không đọc/ghi được) — ref chỉ để commit khỏi bị gc.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from ..core.reasons import Kind

#: Thứ tự các bước. `stage` của checkpoint là bước cuối cùng đã XONG.
STAGES = ("baseline", "lint_fix", "test_first", "implement", "verified", "mr")

#: Dừng vì những lý do này thì bằng chứng/plan không còn dùng lại được.
NON_RESUMABLE = {Kind.ANTIGAMING_EVIDENCE.value, Kind.PLAN_STALE.value}

#: Tên bước cho người đọc comment trên ticket.
STAGE_WORDS = {"": "chưa xong bước nào", "baseline": "baseline", "lint_fix": "sửa lint sẵn có",
               "test_first": "viết test", "implement": "implement",
               "verified": "gate + anti-gaming", "mr": "mở MR"}
_NEXT_WORDS = {"": "baseline", "baseline": "viết test", "lint_fix": "viết test",
               "test_first": "implement", "implement": "gate + anti-gaming",
               "verified": "mở MR", "mr": "cập nhật MR theo góp ý"}


@dataclass
class Checkpoint:
    ticket: str
    run_id: str
    branch: str
    plan_hash: str
    stage: str = ""
    head_sha: str = ""
    profile_hash: str = ""
    baseline: dict | None = None
    evidence: dict = field(default_factory=dict)
    gate_report: dict | None = None
    ag: dict | None = None
    #: Comment của người (sau plan) mà lần chạy này đã đưa cho agent. Có comment mới
    #: so với danh sách này → phải có một vòng implement theo comment đó.
    notes: list[str] = field(default_factory=list)
    mr: dict | None = None
    resumable: bool = True
    stopped: str = ""

    def reached(self, stage: str) -> bool:
        return bool(self.stage) and STAGES.index(self.stage) >= STAGES.index(stage)

    def next_step(self) -> str:
        return _NEXT_WORDS.get(self.stage, "baseline")


def plan_hash(spec_yaml: str, plan: str, base_sha: str) -> str:
    return hashlib.sha256("\0".join((spec_yaml, plan, base_sha)).encode()).hexdigest()[:16]


def profile_hash(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def ref_name(ticket_id: str) -> str:
    return "refs/e2ea/" + re.sub(r"[^A-Za-z0-9._-]", "_", ticket_id)


def path(runs_root: Path, ticket_id: str) -> Path:
    return Path(runs_root) / ticket_id / "checkpoint.json"


def load(runs_root: Path, ticket_id: str) -> Checkpoint | None:
    try:
        raw = json.loads(path(runs_root, ticket_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    known = {f.name for f in fields(Checkpoint)}
    try:
        return Checkpoint(**{k: v for k, v in raw.items() if k in known})
    except TypeError:
        return None


def save(runs_root: Path, cp: Checkpoint, repo: Path) -> None:
    target = path(runs_root, cp.ticket)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cp), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    if cp.head_sha:
        subprocess.run(["git", "update-ref", ref_name(cp.ticket), cp.head_sha], cwd=repo,
                       capture_output=True, text=True, check=False)


def usable(cp: Checkpoint | None, *, plan: str, repo: Path) -> tuple[bool, str]:
    """(dùng được không, vì sao không). Không đoán: thiếu điều kiện nào là chạy lại từ đầu."""
    if cp is None:
        return False, "chưa có lần chạy Phase B nào để làm tiếp"
    if cp.plan_hash != plan:
        return False, "plan/spec đã duyệt khác với lần chạy trước"
    if not cp.resumable:
        return False, f"lần trước dừng vì `{cp.stopped}` — bằng chứng không dùng lại được"
    if not cp.stage or not cp.head_sha:
        return False, "lần trước chưa xong bước nào"
    ok = subprocess.run(["git", "cat-file", "-e", f"{cp.head_sha}^{{commit}}"], cwd=repo,
                        capture_output=True).returncode == 0
    if not ok:
        return False, f"commit `{cp.head_sha[:8]}` của lần trước không còn trong repo"
    return True, ""


class Recorder:
    """Ghi checkpoint sau mỗi bước. Phase B gọi `done`, orchestrator gọi `stop` khi run dừng."""

    def __init__(self, runs_root: Path, repo: Path, cp: Checkpoint) -> None:
        self.runs_root, self.repo, self.cp = Path(runs_root), Path(repo), cp
        self.evidence: dict | None = None

    def bind(self, evidence: dict) -> None:
        """Evidence là dict sống của Phase B — lúc dừng giữa chừng vẫn ghi được bản mới nhất."""
        self.evidence = evidence

    def done(self, stage: str, work: Path, *, base=None, gate_report: dict | None = None,
             ag: dict | None = None, mr: dict | None = None) -> None:
        self.cp.stage = stage
        self._snapshot(work)
        if base is not None:
            self.cp.baseline = asdict(base)
        if gate_report is not None:
            self.cp.gate_report = copy.deepcopy(gate_report)
        if ag is not None:
            self.cp.ag = copy.deepcopy(ag)
        if mr is not None:
            self.cp.mr = mr
        save(self.runs_root, self.cp, self.repo)

    def stop(self, work: Path | None, kind: str | None) -> None:
        """Run dừng mà chưa ra MR: ghi HEAD mới nhất (commit của các vòng dở) và có resume được không."""
        if not self.cp.stage:
            return                   # chưa xong bước nào: không có gì để làm tiếp
        if work is not None and work.is_dir():
            self._snapshot(work)
        self.cp.stopped = kind or ""
        self.cp.resumable = (kind or "") not in NON_RESUMABLE
        save(self.runs_root, self.cp, self.repo)

    def _snapshot(self, work: Path) -> None:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work,
                              capture_output=True, text=True).stdout.strip()
        if head:
            self.cp.head_sha = head
        if self.evidence is not None:
            self.cp.evidence = copy.deepcopy(self.evidence)
