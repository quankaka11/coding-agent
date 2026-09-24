"""Việc chạy lâu (clone, cài venv, doctor) chạy nền và kể lại từng dòng.

Người bấm "Clone và sinh hồ sơ" phải thấy nó đang làm gì, chứ không phải một
vòng xoay ba phút. Nên job giữ nguyên dòng in ra của chính các lệnh CLI đang
có — không viết lại thông báo lần thứ hai.
"""
from __future__ import annotations

import io
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Job:
    id: str
    kind: str
    lines: list[str] = field(default_factory=list)
    done: bool = False
    ok: bool | None = None
    error: str | None = None
    started: float = field(default_factory=time.time)
    result: dict | None = None

    def view(self, since: int = 0) -> dict:
        return {"id": self.id, "kind": self.kind, "done": self.done, "ok": self.ok,
                "error": self.error, "lines": self.lines[since:], "total": len(self.lines),
                "seconds": round(time.time() - self.started, 1), "result": self.result}


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()
_LOCAL = threading.local()


class _PerThread:
    """Đứng thay sys.stdout/stderr, chuyển mỗi dòng về sink của thread đang in.

    `redirect_stdout` thay biến TOÀN CỤC: trong lúc dựng repo, mọi thứ thread
    khác in (vòng quét, request khác) lọt vào log của job; hai job chạy chồng thì
    job xong trước trả lại stdout gốc, job xong sau trả lại sink đã chết — và
    sys.stdout kẹt ở đó tới hết đời tiến trình.
    """

    def __init__(self, fallback) -> None:
        self._fallback = fallback

    def _target(self):
        return getattr(_LOCAL, "sink", None) or self._fallback

    def write(self, text: str) -> int:
        return self._target().write(text)

    def flush(self) -> None:
        self._target().flush()

    def __getattr__(self, name: str):
        return getattr(self._fallback, name)


def _install() -> None:
    """Cài một lần, không bao giờ gỡ: gỡ giữa chừng là lặp lại đúng lỗi ở trên."""
    with _LOCK:
        if not isinstance(sys.stdout, _PerThread):
            sys.stdout = _PerThread(sys.stdout)
        if not isinstance(sys.stderr, _PerThread):
            sys.stderr = _PerThread(sys.stderr)


class _Sink(io.TextIOBase):
    """Gom stdout của lệnh CLI thành từng dòng cho giao diện đọc."""

    def __init__(self, job: Job) -> None:
        self.job = job
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, _, self._buf = self._buf.partition("\n")
            if line.strip():
                self.job.lines.append(line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buf.strip():
            self.job.lines.append(self._buf.rstrip())
            self._buf = ""


def start(kind: str, work: Callable[[Job], dict | None], *, only_one: bool = True) -> Job:
    """Chạy `work` trong thread. `only_one` chặn hai job cùng loại chạy chồng."""
    with _LOCK:
        if only_one:
            for job in _JOBS.values():
                if job.kind == kind and not job.done:
                    return job
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        _JOBS[job.id] = job

    _install()

    def runner() -> None:
        sink = _Sink(job)
        _LOCAL.sink = sink
        try:
            job.result = work(job)
            sink.flush()
            job.ok = True
        except Exception as exc:
            sink.flush()
            job.ok = False
            job.error = f"{type(exc).__name__}: {' '.join(str(exc).split())}"[:500]
        finally:
            _LOCAL.sink = None
            job.done = True
            _prune()

    threading.Thread(target=runner, name=f"e2ea-{kind}", daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def latest(kind: str) -> Job | None:
    jobs = [j for j in _JOBS.values() if j.kind == kind]
    return max(jobs, key=lambda j: j.started) if jobs else None


def _prune(keep: int = 20) -> None:
    with _LOCK:
        if len(_JOBS) <= keep:
            return
        for job in sorted(_JOBS.values(), key=lambda j: j.started)[:-keep]:
            if job.done:
                _JOBS.pop(job.id, None)
