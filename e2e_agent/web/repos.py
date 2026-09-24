"""Nhiều repo đã cấu hình, mỗi lúc trực đúng một.

`.env` vẫn là repo đang bật — để `e2ea watch`, `doctor`, `scan` gõ tay ở terminal
chạy y như trước. Các repo khác nằm trong `.e2ea/repos/<tên>.env`; đổi repo là
chép file đó đè lên `.env`. Mọi thư mục khác (`repos/`, `profiles/`, `venvs/`,
`work/`, `runs/`) vốn đã tách theo tên repo nên không phải dời gì.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import watch, workspace as ws_mod


def store(root: Path) -> Path:
    return Path(root) / ".e2ea" / "repos"


def save_current(root: Path) -> str | None:
    """Cất .env đang dùng vào kho theo tên repo. Gọi trước mỗi lần đổi."""
    ws = ws_mod.load(root)
    env = Path(root) / ".env"
    if not ws.layout or not env.is_file():
        return None
    name = ws.layout["name"]
    target = store(root) / f"{name}.env"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(env, target)
    os.chmod(target, 0o600)
    return name


def listing(root: Path) -> dict:
    """Repo đang bật + các repo đã cất, kèm tình trạng đủ để chọn mà không cần bấm vào."""
    root = Path(root)
    ws = ws_mod.load(root)
    active = ws.layout["name"] if ws.layout else None
    names = {p.stem for p in store(root).glob("*.env")}
    if active:
        names.add(active)

    out = []
    for name in sorted(names):
        profile = root / "profiles" / f"{name}.yaml"
        view = ws_mod._profile_view(profile)
        runs = root / "runs" / name
        out.append({
            "name": name,
            "active": name == active,
            "cloned": (root / "repos" / name / ".git").exists(),
            "profile_exists": view["exists"],
            "todos": len(view["todos"]),
            "runs": len(list(runs.glob("*/*/events.jsonl"))) if runs.is_dir() else 0,
        })
    return {"active": active, "repos": out}


def activate(root: Path, name: str) -> dict:
    """Dừng vòng quét, đổi .env, để người tự bật lại khi đã xem qua màn hình."""
    root = Path(root)
    try:
        ws_mod.safe_segment(name)
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    target = store(root) / f"{name}.env"
    if not target.is_file():
        return {"ok": False, "detail": f"chưa cất cấu hình của {name}"}
    current = ws_mod.load(root)
    if current.layout and current.layout["name"] == name:
        return {"ok": True, "detail": f"{name} đang bật sẵn"}

    stopped = watch.stop(root)
    if stopped.get("stopping"):
        return {"ok": False, "waiting": True,
                "detail": "vòng quét đang chạy dở một run — đợi run đó xong rồi đổi"}

    save_current(root)
    env = root / ".env"
    shutil.copyfile(target, env)
    os.chmod(env, 0o600)
    return {"ok": True, "detail": f"đã chuyển sang {name}", "name": name}


def forget(root: Path, name: str, purge: bool = False) -> dict:
    """Gỡ khỏi danh sách. `purge` mới xoá clone/venv/work/runs — hỏi kỹ trước."""
    root = Path(root)
    try:
        # `purge` gọi rmtree(root/"repos"/name): name là ".." thì xoá cả thư mục làm việc.
        ws_mod.safe_segment(name)
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    ws = ws_mod.load(root)
    if ws.layout and ws.layout["name"] == name:
        return {"ok": False, "detail": "không gỡ được repo đang bật — chuyển sang repo khác đã"}
    saved = store(root) / f"{name}.env"
    saved.unlink(missing_ok=True)
    removed = ["cấu hình đã cất"]
    if purge:
        for folder in ("repos", "venvs", "work", "runs"):
            path = root / folder / name
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                removed.append(folder)
        profile = root / "profiles" / f"{name}.yaml"
        if profile.exists():
            profile.unlink()
            removed.append("hồ sơ")
    return {"ok": True, "detail": "đã gỡ: " + ", ".join(removed)}


def migrate_runs(root: Path) -> dict:
    """Dồn `runs/<task>/` kiểu cũ vào `runs/<tên repo>/`. Chạy một lần, tự nhận ra.

    Trước đây mọi repo chung một `runs/`. Bỏ qua bước này thì run cũ biến mất khỏi
    giao diện và `e2ea metrics` — bằng chứng không được phép rơi mất như vậy.

    Không bao giờ dời khi còn một vòng quét đang sống: nó đang cầm đường dẫn cũ,
    và dời dưới chân một tiến trình đang ghi là cách chắc chắn để mất bằng chứng.
    """
    root = Path(root)
    ws = ws_mod.load(root)
    if not ws.layout:
        return {"moved": [], "blocked": False, "reason": None}
    name = ws.layout["name"]
    runs, target = root / "runs", root / "runs" / name
    if not runs.is_dir():
        return {"moved": [], "blocked": False, "reason": None}

    holder = _lock_holder(runs / "watch" / "watch.lock")
    if holder:
        return {"moved": [], "blocked": True,
                "reason": f"đang có vòng quét chạy (pid {holder}) giữ runs/ — dừng nó rồi "
                          f"chạy lại `e2ea serve` để dồn run cũ vào runs/{name}/"}
    moved = []
    for child in list(runs.iterdir()):
        if not child.is_dir() or child.name == name:
            continue
        is_task = any(child.glob("*/events.jsonl")) or any(child.glob("*/outcome.json"))
        if not (is_task or child.name == "watch"):
            continue
        target.mkdir(parents=True, exist_ok=True)
        dest = target / child.name
        if dest.exists():
            continue
        shutil.move(str(child), str(dest))
        moved.append(child.name)
    return {"moved": moved, "blocked": False, "reason": None}


def _lock_holder(lock: Path) -> int | None:
    """Pid đang giữ khoá vòng quét, nếu tiến trình đó còn sống."""
    from ..pipeline.watcher import _alive, _read_pid
    if not lock.is_file():
        return None
    pid = _read_pid(lock)
    return pid if pid and _alive(pid) else None
