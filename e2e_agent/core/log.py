"""Log hai luồng từ một nguồn (build-plan mục 4).

events.jsonl cho máy, console cho người. Không nơi nào trong gói được print()
thẳng — mọi thứ đi qua EventLog.emit().
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}

_COLORS = {"debug": "\033[90m", "info": "\033[36m", "warn": "\033[33m", "error": "\033[31m"}
_RESET = "\033[0m"

# Che dữ liệu nhạy cảm trước khi ghi (mục 4.6).
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(glpat|ghp|github_pat|sk-ant|sk-|xox[baprs])-?[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key)\b\s*[=:]\s*\S+"),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
]


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pat in _SECRET_PATTERNS:
            value = pat.sub("«redacted»", value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def tail(text: str, lines: int = 20) -> str:
    """Cắt bớt nhưng không vứt: giữ phần cuối, bản đầy đủ nằm trong run dir."""
    parts = text.splitlines()
    if len(parts) <= lines:
        return text.strip()
    return "…(+%d dòng)\n" % (len(parts) - lines) + "\n".join(parts[-lines:]).strip()


class EventLog:
    def __init__(self, path: Path | None, level: str = "info", console: bool = True) -> None:
        self.level = LEVELS[level]
        self.console = console
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")
        self._color = console and sys.stderr.isatty() and not os.environ.get("NO_COLOR")

    def emit(self, event: str, level: str = "info", **fields: Any) -> dict:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
            "event": event,
            "level": level,
            **{k: v for k, v in fields.items() if k in ("run_id", "task_id", "phase", "step", "duration_ms")},
        }
        data = {k: v for k, v in fields.items() if k not in rec}
        if data:
            rec["data"] = data
        rec = redact(rec)
        if self._fh is not None:
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fh.flush()
        if self.console and LEVELS[level] >= self.level:
            self._render(rec)
        return rec

    def _render(self, rec: dict) -> None:
        pieces = []
        for k, v in (rec.get("data") or {}).items():
            if v is None or k in ("stdout", "stderr"):
                continue
            text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            pieces.append(f"{k}={text if len(text) <= 60 else text[:57] + '…'}")
        step = rec.get("step") or "-"
        head = f"{rec['ts'][11:19]} {rec['level']:<5} {step:<11} {rec['event']:<18}"
        if self._color:
            head = _COLORS[rec["level"]] + head + _RESET
        line = head + " " + " ".join(pieces)
        print(line.rstrip(), file=sys.stderr)
        for key in ("stdout", "stderr"):
            body = (rec.get("data") or {}).get(key)
            if body:
                for row in str(body).splitlines():
                    print(f"{'':>19}│ {row}", file=sys.stderr)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
