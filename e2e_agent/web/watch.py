"""Bật/tắt vòng quét ngay trong tiến trình `serve`.

Watcher chạy bằng thread nền chứ không phải tiến trình con: nó đã được viết để
chạy ngoài main thread (đăng ký signal có bắt `ValueError`), và `watch.lock` vẫn
là thứ chặn hai vòng quét cùng lúc — kể cả khi cái kia gõ từ terminal.

Dừng là dừng SAU vòng hiện tại. Giết ngang một run đang chạy để lại ticket kẹt ở
`agent:running` và một sandbox bẩn, nên ở đây không có "giết ngang".
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

_LOCK = threading.Lock()
_STATE: dict = {"watcher": None, "thread": None, "started": None,
                "interval": 60, "stopping": False, "error": None}


def start(root: Path, interval: int = 60) -> dict:
    from ..cli import _orchestrator
    from ..config import profile as profile_mod
    from ..pipeline.watcher import Watcher
    from . import workspace as ws_mod

    with _LOCK:
        if _alive():
            return {"ok": True, "detail": "vòng quét đã chạy sẵn", **status(root)}
        ws = ws_mod.load(root)
        if not ws.layout or not ws.layout["profile"].is_file():
            return {"ok": False, "detail": "chưa có hồ sơ repo — dựng repo xong đã"}

        lay = ws.layout
        args = SimpleNamespace(profile=str(lay["profile"]), repo=str(lay["repo"]),
                               runs_root=str(lay["runs"]), work_root=str(lay["work"]),
                               log_level="info", quiet=True)
        prof = profile_mod.load(args.profile)
        orch, _ = _orchestrator(args, prof, Path(args.repo).resolve())
        watcher = Watcher(orch=orch, interval=interval, runs_root=lay["runs"],
                          log_level="info", console=False)

        def loop() -> None:
            try:
                handled = watcher.run()
                if handled < 0:
                    _STATE["error"] = "đã có tiến trình watch khác giữ khoá"
            except Exception as exc:
                _STATE["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                _STATE["stopping"] = False

        thread = threading.Thread(target=loop, name="e2ea-watch", daemon=True)
        _STATE.update(watcher=watcher, thread=thread, started=time.time(),
                      interval=interval, stopping=False, error=None)
        thread.start()
        time.sleep(0.4)                     # đủ để lỗi giữ khoá kịp hiện ra
        return {"ok": _STATE["error"] is None, "detail": _STATE["error"] or "đã bật vòng quét",
                **status(root)}


def stop(root: Path, timeout: float | None = 2.0) -> dict:
    """Xin dừng. Đang chạy dở một run thì vẫn phải đợi run đó xong."""
    with _LOCK:
        watcher, thread = _STATE["watcher"], _STATE["thread"]
        if not _alive():
            return {"ok": True, "detail": "vòng quét không chạy", **status(root)}
        watcher._stop = True                # đúng cờ mà tín hiệu dừng vẫn dùng
        _STATE["stopping"] = True
    thread.join(timeout=timeout)
    detail = ("đã dừng" if not thread.is_alive()
              else "đang chờ vòng hiện tại xong rồi mới dừng")
    return {"ok": True, "detail": detail, **status(root)}


def status(root: Path) -> dict:
    from . import workspace as ws_mod
    ws = ws_mod.load(root)
    outside = ws_mod.watcher_state(ws.runs_root)
    mine = _alive()
    return {"running": mine or outside["running"], "mine": mine,
            "stopping": _STATE["stopping"], "error": _STATE["error"],
            "interval": _STATE["interval"] if mine else outside["interval"],
            "pid": outside["pid"], "last_event": outside["last_event"],
            "last_at": outside["last_at"]}


def _alive() -> bool:
    thread = _STATE["thread"]
    return bool(thread and thread.is_alive())
