"""Đọc trạng thái thư mục làm việc. Không ném ngoại lệ, không ghi gì.

Màn hình đầu tiên phải mở được cả khi chưa cấu hình gì — lúc đó nó chính là
chỗ để cấu hình. Nên mọi hàm ở đây trả về "chưa có" thay vì lỗi.

Bí mật KHÔNG bao giờ rời khỏi tiến trình này: chỉ trả về đã đặt hay chưa và ba
ký tự cuối, đủ để người nhận ra mình dán nhầm token nào.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from ..config.profile import ProfileError
from ..core import gitutil
from ..core import secrets as secrets_mod
from . import progress

#: Toàn bộ khoá .env mà hệ thống đọc. Sáu khoá đầu sửa được trên giao diện;
#: `agent_model`/`agent_effort` chỉ đặt trong .env — xem README.
EDITABLE_KEYS = ("repo_url", "gitlab_token", "backlog_space", "backlog_project",
                 "backlog_api_key", "google_chat_webhook")
READONLY_KEYS = ("agent_model", "agent_effort")
SECRET_KEYS = frozenset({"gitlab_token", "backlog_api_key", "google_chat_webhook"})

#: Một đoạn đường dẫn đến từ URL (mã ticket, run_id, tên repo, tên file của run).
#: Mở đầu bằng chữ/số nên `.`, `..`, `.env` không lọt; không có `/` nên không
#: đi ra ngoài được. Starlette giải mã `%2E%2E` thành `..` TRƯỚC khi tới đây —
#: thiếu chốt này thì `/api/runs/%2E%2E/%2E%2E/artifact/.env` trả nguyên token.
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,199}")


def safe_segment(value: str) -> str:
    if not _SEGMENT.fullmatch(value or "") or ".." in value:
        raise ValueError(f"tên không hợp lệ: {value!r}")
    return value


@dataclass
class Workspace:
    root: Path
    layout: dict | None = None
    #: Vì sao chưa dựng được layout — câu này của ProfileError, đã đủ rõ cho người.
    problem: str | None = None
    env_file: Path = field(init=False)

    def __post_init__(self) -> None:
        self.env_file = self.root / ".env"

    @property
    def runs_root(self) -> Path:
        return self.layout["runs"] if self.layout else self.root / "runs"


def load(root: Path) -> Workspace:
    """Dựng layout từ .env. Thiếu khoá nào thì `problem` nói thiếu gì."""
    root = Path(root).resolve()
    env = root / ".env"
    if env.is_file() and env not in secrets_mod.EXTRA_ENV_FILES:
        secrets_mod.EXTRA_ENV_FILES.append(env)
    from ..cli import _layout                      # nạp muộn: cli kéo theo cả pipeline
    try:
        return Workspace(root=root, layout=_layout(SimpleNamespace(dir=str(root))))
    except ProfileError as exc:
        return Workspace(root=root, problem=str(exc).split("\n", 1)[0])
    except Exception as exc:                       # .env hỏng không được làm sập trang
        return Workspace(root=root, problem=f"{type(exc).__name__}: {exc}")


# -- .env ------------------------------------------------------------------

def env_view() -> dict[str, dict]:
    """Giá trị 8 khoá, bí mật đã che. Dùng cho form kết nối."""
    out: dict[str, dict] = {}
    for key in EDITABLE_KEYS + READONLY_KEYS:
        value = secrets_mod.read_secret(key)
        if key in SECRET_KEYS:
            out[key] = {"set": bool(value), "hint": value[-3:] if value else "",
                        "secret": True, "editable": key in EDITABLE_KEYS}
        else:
            out[key] = {"set": bool(value), "value": value, "secret": False,
                        "editable": key in EDITABLE_KEYS}
    return out


# -- trạng thái tổng ---------------------------------------------------------

def state(ws: Workspace) -> dict:
    lay, env = ws.layout, env_view()
    repo_dir = lay["repo"] if lay else None
    cloned = bool(repo_dir and (repo_dir / ".git").exists())
    profile_path = lay["profile"] if lay else None
    prof = _profile_view(profile_path) if profile_path else None

    return {
        "dir": str(ws.root),
        "configured": bool(lay and cloned and prof and prof["exists"]),
        "problem": ws.problem,
        "repo": {
            "name": lay["name"] if lay else None,
            "url": gitutil.redact_url(lay["url"]) if lay else None,
            "cloned": cloned,
            "path": str(repo_dir) if repo_dir else None,
            "head": gitutil.head_sha(repo_dir)[:8] if cloned else None,
        },
        "connections": {
            "code": _code_view(lay, env),
            "tracker": _tracker_view(lay, env),
            "notify": {"kind": "webhook" if env["google_chat_webhook"]["set"] else None,
                       "set": env["google_chat_webhook"]["set"]},
            "agent": {"model": env["agent_model"]["value"] or None,
                      "effort": env["agent_effort"]["value"] or None},
        },
        "profile": prof,
        "watcher": watcher_state(ws.runs_root),
    }


def _code_view(lay: dict | None, env: dict) -> dict:
    forge = (lay or {}).get("forge")
    return {"kind": forge["kind"] if forge else ("file" if lay else None),
            "project": forge["project"] if forge else None,
            "token_set": env["gitlab_token"]["set"]}


def _tracker_view(lay: dict | None, env: dict) -> dict:
    tracker = (lay or {}).get("tracker")
    if not tracker:
        return {"kind": "file", "offline": True, "space": None, "project": None,
                "key_set": env["backlog_api_key"]["set"]}
    return {"kind": tracker["kind"], "offline": False, "space": tracker["space"],
            "project": tracker["project"], "key_set": env["backlog_api_key"]["set"]}


def _profile_view(path: Path) -> dict:
    """Hồ sơ + những dòng ĐOÁN/TODO mà autoprofile để lại ở đầu file.

    TODO là thứ chặn `up`, nên phải nổi lên giao diện chứ không nằm im trong yaml.
    """
    if not path.is_file():
        return {"exists": False, "path": str(path), "notes": [], "todos": [], "repo_id": None}
    notes, todos = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("#"):
            if line:
                break                       # hết khối chú thích đầu file
            continue
        if line.startswith("# ĐOÁN:"):
            notes.append(line.removeprefix("# ĐOÁN:").strip())
        elif line.startswith("# TODO:"):
            todos.append(line.removeprefix("# TODO:").strip())
    repo_id = None
    try:
        from ..config import profile as profile_mod
        repo_id = profile_mod.load(str(path)).repo_id
    except Exception:
        pass
    return {"exists": True, "path": str(path), "notes": notes, "todos": todos,
            "repo_id": repo_id}


# -- watcher -----------------------------------------------------------------

def watcher_state(runs_root: Path) -> dict:
    """Còn sống không, đang làm gì. Đọc từ khoá file và nhật ký vòng quét."""
    from ..pipeline.watcher import _alive, _read_pid
    lock = runs_root / "watch" / "watch.lock"
    pid = _read_pid(lock) if lock.is_file() else None
    running = bool(pid and _alive(pid))
    last = _last_watch_events(runs_root / "watch" / "events.jsonl")
    data = last.get("data") or {}
    # `EventLog.emit` chỉ nâng run_id/task_id/phase/step/duration_ms lên top-level,
    # mọi field khác nằm trong `data` — đọc sai chỗ thì interval luôn None, và
    # cảnh báo "watcher treo" không bao giờ bật.
    return {"running": running, "pid": pid if running else None,
            "last_event": last.get("event"), "last_at": last.get("ts"),
            "cycle": data.get("cycle") or last.get("cycle"),
            "interval": data.get("interval_sec") or last.get("interval_sec")}


def _last_watch_events(path: Path, tail: int = 4096) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - tail))
            lines = fh.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


# -- danh sách run -----------------------------------------------------------

_CACHE: dict[tuple[str, float], dict] = {}


def runs_index(runs_root: Path, limit: int = 25,
               current: dict[str, tuple[str, float]] | None = None) -> list[dict]:
    """Run gần đây nhất trước. `watch/` không phải run nên bỏ qua.

    Bám theo `events.jsonl` chứ không phải `outcome.json`: run đang chạy chưa
    có kết cục, mà đó lại đúng là run người muốn nhìn nhất.
    """
    paths = [p for p in runs_root.glob("*/*/events.jsonl") if p.parent.parent.name != "watch"]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    now = time.time()
    rows = [_run_row(p.parent, now) for p in paths[:limit]]
    later = {task: next_runs(runs_root, task) for task in {r["task_id"] for r in rows}}
    current = current or {}
    return [{**r, **history(r, later[r["task_id"]].get(r["run_id"]), current.get(r["task_id"]))}
            for r in rows]


# -- run đã có run sau ------------------------------------------------------------
#
# Phase A và Phase B chạy trên CÙNG một ticket, nên danh sách có hai dòng cùng mã. Dòng
# Phase A giữ nguyên kết luận lúc nó xong — "plan chờ duyệt" — kể cả khi plan đã được
# duyệt và Phase B đang chạy ngay dòng trên. Run nào đã có run sau cùng ticket là LỊCH
# SỬ: nhãn của nó nói về quá khứ, không phải việc đang chờ ai.

def next_runs(runs_root: Path, task_id: str) -> dict[str, dict]:
    """{run_id: run kế tiếp của cùng ticket {"run_id", "phase"}} theo thứ tự bắt đầu."""
    order = []
    for path in (runs_root / task_id).glob("*/events.jsonl"):
        try:
            with path.open(encoding="utf-8") as fh:
                first = json.loads(fh.readline() or "{}")
        except (OSError, json.JSONDecodeError):
            continue
        order.append((first.get("ts") or "", path.parent.name, first.get("phase")))
    order.sort()
    return {run_id: {"run_id": nxt, "phase": phase}
            for (_, run_id, _), (_, nxt, phase) in zip(order, order[1:])}


#: Người đã quyết plan nhưng vòng quét chưa chạy tiếp: chữ cho run "plan chờ duyệt".
_DECIDED = {
    "agent:plan-approved": ("plan đã duyệt", "Đã duyệt — agent viết code ở vòng quét tới."),
    "agent:plan-rejected": ("plan bị từ chối", "Đã từ chối — agent lập plan mới ở vòng quét tới."),
    "agent:try": ("chờ lập plan lại", "Ticket đã về hàng chờ lập plan."),
    "agent:running": ("đang xử lý", "Agent đang xử lý ticket này."),
}


def history(row: dict, later: dict | None, now_state: tuple[str, float] | None = None) -> dict:
    """Chữ và câu cho run không còn là trạng thái hiện tại của ticket.

    `later`: run kế tiếp của cùng ticket. `now_state`: (nhãn, lúc biết) của ticket theo
    tracker — chỉ dùng khi biết SAU lúc run này xong, để một plan mới lập lại không bị
    đọc nhầm thành "đã duyệt" theo một lần duyệt cũ.
    """
    none = {"superseded_by": None, "history_word": None, "history_why": None}
    if row.get("live") or row.get("stale"):
        return none
    if not later:
        label = row.get("label")
        if (now_state and label == "agent:plan-ready" and now_state[0] != label
                and now_state[1] >= float(row.get("updated_at") or 0)):
            word, why = _DECIDED.get(now_state[0], ("đã xử lý", f"Ticket hiện ở `{now_state[0]}`."))
            return {**none, "history_word": word, "history_why": why}
        return none
    nxt, label = later["run_id"], row.get("label")
    word = why = None
    if label == "agent:plan-ready":
        if later.get("phase") == "B":
            word, why = "plan đã duyệt", f"Plan đã được duyệt — viết code ở run {nxt}."
        else:
            word, why = "plan cũ", f"Đã lập plan mới ở run {nxt}."
    elif label == "agent:needs-human":
        word, why = "đã chạy lại", f"Đã chạy lại ở run {nxt}. Lý do lần này: {row.get('why') or '—'}"
    elif label not in ("agent:mr-created",):
        why = f"{row.get('why') or '—'} · Đã chạy lại ở run {nxt}."
    return {"superseded_by": nxt, "history_word": word, "history_why": why}


#: Những field đổi theo ĐỒNG HỒ chứ không theo nội dung file. Cache theo mtime
#: mà giữ luôn mấy field này thì một run chết vẫn báo "vừa cập nhật 3 giây trước"
#: mãi mãi — đúng cái bệnh mà `stale` sinh ra để chữa.
_VOLATILE = ("live", "stale", "idle_sec")


def _run_row(run_dir: Path, now: float) -> dict:
    events_path = run_dir / "events.jsonl"
    outcome_path = run_dir / "outcome.json"
    key = (str(run_dir), events_path.stat().st_mtime if events_path.is_file() else 0.0)
    row = _CACHE.get(key)
    if row is None:
        try:
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            outcome = {}
        row = {
            "task_id": outcome.get("task_id") or run_dir.parent.name,
            "run_id": outcome.get("run_id") or run_dir.name,
            "reason": outcome.get("reason"),
            "kind": outcome.get("kind"),
            "label": outcome.get("label"),
            "why": outcome.get("why", ""),
            "duration_ms": outcome.get("duration_ms"),
            **_from_events(run_dir),
        }
        if len(_CACHE) > 512:               # phiên serve dài ngày không phình mãi
            _CACHE.clear()
        _CACHE[key] = row
    return {**row, **progress.liveness(row, now)}


def _from_events(run_dir: Path) -> dict:
    """Pha, bước hiện tại, chi phí và nhịp sống — chỉ có trong events.jsonl.

    Một nguồn duy nhất (`progress.track`) trả lời "đang ở bước nào" và "còn sống
    không", để màn hình danh sách và màn hình chi tiết không nói hai điều khác nhau.
    """
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return dict(progress.track(path, [], time.time()))
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return dict(progress.track(path, events, time.time()))
