"""Tiến trình chạy liên tục: quét Backlog, chạy bước tương ứng, lặp lại.

Đây là thứ biến các lệnh rời rạc thành một luồng tự động. Người chỉ còn chạm
hai lần: giao ticket cho agent, và đổi trạng thái để duyệt plan.
"""
from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.log import EventLog
from .orchestrator import Orchestrator


@dataclass
class Watcher:
    orch: Orchestrator
    interval: int = 60
    runs_root: Path = Path("runs")
    log_level: str = "info"
    console: bool = True
    #: Bao nhiêu lần một ticket được phép kết thúc ERROR trước khi giao cho người.
    max_attempts: int = 3

    def __post_init__(self) -> None:
        self.log = EventLog(self.runs_root / "watch" / "events.jsonl", self.log_level, self.console)
        self._stop = False
        self._fails: dict[str, int] = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._handle_signal)
            except ValueError:
                pass          # không ở main thread thì bỏ qua

    def _handle_signal(self, *_: object) -> None:
        self.log.emit("watch.stopping", level="warn", note="nhận tín hiệu dừng, kết thúc sau vòng này")
        self._stop = True

    def run(self, max_cycles: int | None = None) -> int:
        """Trả về số ticket đã xử lý. Dừng khi nhận SIGINT/SIGTERM."""
        lock = _Lock(self.runs_root / "watch" / "watch.lock")
        if not lock.acquire():
            self.log.emit("watch.busy", level="error",
                          note=f"đã có tiến trình watch khác đang chạy (pid {lock.holder})")
            return -1

        handled = cycle = 0
        self.log.emit("watch.start", interval_sec=self.interval, pid=os.getpid())
        try:
            while not self._stop and (max_cycles is None or cycle < max_cycles):
                cycle += 1
                try:
                    # Không đợi run song song xong: vòng sau vẫn nhận ticket mới vào chỗ trống.
                    done = self.orch.scan_once(wait=False)
                except Exception as exc:          # một ticket hỏng không được giết vòng lặp
                    self.log.emit("watch.error", level="error", cycle=cycle,
                                  error=f"{type(exc).__name__}: {exc}")
                    done = []
                handled += self._report(cycle, done)
                if not done:
                    self.log.emit("watch.idle", level="debug", cycle=cycle,
                                  running=len(getattr(self.orch, "_inflight", {})))
                if self._stop or (max_cycles is not None and cycle >= max_cycles):
                    break
                self._sleep(self.interval)
        finally:
            # Dừng là dừng SAU khi các run đang chạy xong — giết ngang để lại ticket kẹt.
            drain = getattr(self.orch, "drain", None)
            if drain is not None:
                try:
                    handled += self._report(cycle, drain())
                except Exception as exc:
                    self.log.emit("watch.error", level="error", error=f"{type(exc).__name__}: {exc}")
            lock.release()
            self.log.emit("watch.end", cycles=cycle, handled=handled)
            self.log.close()
        return handled

    def _report(self, cycle: int, done: list[tuple[str, str]]) -> int:
        for error in getattr(self.orch, "errors", []):
            self.log.emit("watch.error", level="error", cycle=cycle, error=error)
        if getattr(self.orch, "errors", None):
            self.orch.errors.clear()
        for ticket_id, outcome in done:
            self.log.emit("watch.handled", cycle=cycle, ticket=ticket_id, outcome=outcome)
            self._count(ticket_id, outcome)
        return len(done)

    def _count(self, ticket_id: str, outcome: str) -> None:
        """ERROR lặp lại trên cùng một ticket là hỏng thật, không phải trục trặc thoáng qua."""
        if outcome != "ERROR":
            self._fails.pop(ticket_id, None)
            return
        self._fails[ticket_id] = self._fails.get(ticket_id, 0) + 1
        if self._fails[ticket_id] < self.max_attempts:
            return
        why = f"{self._fails[ticket_id]} lần liên tiếp kết thúc ERROR"
        try:
            self.orch.give_up(ticket_id, why)
            self.log.emit("watch.gave_up", level="error", ticket=ticket_id, why=why)
        except Exception as exc:
            self.log.emit("watch.error", level="error", ticket=ticket_id,
                          error=f"không giao lại được cho người: {type(exc).__name__}: {exc}")

    def _sleep(self, seconds: int) -> None:
        """Ngủ từng giây để tín hiệu dừng có hiệu lực ngay."""
        for _ in range(seconds):
            if self._stop:
                return
            time.sleep(1)


class _Lock:
    """Khoá theo file, chống chạy hai tiến trình watch trên cùng một backlog."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.holder: int | None = None
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            self.holder = _read_pid(self.path)
            if self.holder and _alive(self.holder):
                return False
            self.path.unlink(missing_ok=True)       # khoá mồ côi của tiến trình đã chết
            return self.acquire()
        os.write(self._fd, str(os.getpid()).encode())
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
