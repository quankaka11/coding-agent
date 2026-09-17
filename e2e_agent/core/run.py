"""RunContext — mọi bước đi qua đây: log, đo thời gian, gọi lệnh, chốt kết cục.

Luật N-3: mỗi run phải kết thúc bằng đúng một decide(). Không return/raise trần.
"""
from __future__ import annotations

import contextlib
import json
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .log import EventLog, tail
from .reasons import LABEL, Reason, explain


class Decided(Exception):
    """Thoát khỏi pipeline sau khi đã chốt kết cục."""

    def __init__(self, reason: Reason, why: str) -> None:
        super().__init__(f"{reason.value}: {why}")
        self.reason = reason
        self.why = why


@dataclass
class CmdResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class RunContext:
    run_dir: Path
    task_id: str = "-"
    phase: str = "-"
    run_id: str = field(default_factory=lambda: "r-" + secrets.token_hex(3))
    timeout_min: int = 60
    cost_cap_usd: float = 15.0
    log_level: str = "info"
    console: bool = True

    def __post_init__(self) -> None:
        # Bắt buộc tuyệt đối: lệnh chạy với cwd là worktree, nên đường dẫn tương đối
        # sẽ ghi một nơi và đọc một nẻo — junit rỗng, luật báo oan.
        self.run_dir = Path(self.run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log = EventLog(self.run_dir / "events.jsonl", self.log_level, self.console)
        self.step = "-"
        self.started = time.monotonic()
        self.cost_usd = 0.0
        self.outcome: Reason | None = None
        self.outcome_label: str | None = None
        self.outcome_why: str = ""

    # -- sự kiện ---------------------------------------------------------
    def emit(self, event: str, level: str = "info", **data: Any) -> None:
        self.log.emit(event, level, run_id=self.run_id, task_id=self.task_id,
                      phase=self.phase, step=self.step, **data)

    @contextlib.contextmanager
    def stage(self, name: str) -> Iterator[None]:
        prev, self.step = self.step, name
        t0 = time.monotonic()
        self.emit("step.start")
        try:
            yield
        finally:
            self.emit("step.end", duration_ms=int((time.monotonic() - t0) * 1000))
            self.step = prev

    # -- lệnh ------------------------------------------------------------
    def cmd(self, argv: list[str] | str, cwd: Path, timeout: int = 900,
            env: dict[str, str] | None = None) -> CmdResult:
        shell = isinstance(argv, str)
        t0 = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(argv, cwd=cwd, shell=shell, capture_output=True,
                                  text=True, timeout=timeout, env=env)
            code, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            code, out, err = 124, exc.stdout or "", (exc.stderr or "") + f"\n[timeout sau {timeout}s]"
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
        except OSError as exc:
            timed_out = False
            code, out, err = 127, "", str(exc)
        res = CmdResult(argv if isinstance(argv, list) else [argv], code, out, err,
                        int((time.monotonic() - t0) * 1000), timed_out)
        self.emit("cmd.exec", level="debug", argv=argv if shell else " ".join(argv),
                  cwd=str(cwd), exit_code=code, duration_ms=res.duration_ms,
                  stdout=tail(out), stderr=tail(err))
        return res

    # -- ngân sách -------------------------------------------------------
    def check_budget(self) -> None:
        elapsed_min = (time.monotonic() - self.started) / 60
        if elapsed_min > self.timeout_min:
            self.decide(Reason.HUMAN_BUDGET, f"vượt trần thời gian {self.timeout_min} phút",
                        elapsed_min=round(elapsed_min, 1))
        if self.cost_usd > self.cost_cap_usd:
            self.decide(Reason.HUMAN_BUDGET, f"vượt trần chi phí {self.cost_cap_usd} USD",
                        cost_usd=self.cost_usd)

    # -- kết cục ---------------------------------------------------------
    def decide(self, reason: Reason, why: str, *, label: str | None = None, **data: Any) -> None:
        """Chốt kết cục và thoát pipeline. Đây là lối ra duy nhất được phép.

        `label` ghi đè nhãn mặc định cho các bước kết thúc giữa chừng —
        Phase A xong là `agent:plan-ready`, chưa phải `agent:mr-created`.
        """
        if self.outcome is not None:
            raise RuntimeError(f"run đã chốt {self.outcome.value}, không được chốt lại")
        self.outcome = reason
        self.outcome_label = label or LABEL[reason]
        self.outcome_why = why
        level = "info" if reason is Reason.OK else "warn" if reason.name.startswith("NOMR_") else "error"
        self.emit("decision", level=level, reason=reason.value, label=self.outcome_label,
                  why=why, meaning=explain(reason), **data)
        raise Decided(reason, why)

    def finish(self) -> Reason:
        if self.outcome is None:
            self.outcome = Reason.ERROR
            self.outcome_label = LABEL[Reason.ERROR]
            self.outcome_why = "run kết thúc mà không chốt kết cục (vi phạm N-3)"
            self.emit("decision", level="error", reason=Reason.ERROR.value,
                      label=self.outcome_label, why="run kết thúc mà không chốt kết cục (vi phạm N-3)")
        elapsed = int((time.monotonic() - self.started) * 1000)
        label = self.outcome_label or LABEL[self.outcome]
        self.emit("run.end", reason=self.outcome.value, label=label,
                  duration_ms=elapsed, cost_usd=round(self.cost_usd, 4))
        (self.run_dir / "outcome.json").write_text(json.dumps(
            {"run_id": self.run_id, "task_id": self.task_id, "reason": self.outcome.value,
             "label": label, "why": self.outcome_why, "duration_ms": elapsed},
            ensure_ascii=False, indent=2),
            encoding="utf-8")
        self.log.close()
        return self.outcome


@contextlib.contextmanager
def run_context(**kwargs: Any) -> Iterator[RunContext]:
    ctx = RunContext(**kwargs)
    ctx.emit("run.start")
    try:
        yield ctx
    except Decided:
        pass
    except Exception as exc:  # lỗi hệ thống vẫn phải để lại dấu vết
        ctx.outcome = None
        with contextlib.suppress(Decided):
            ctx.decide(Reason.ERROR, f"{type(exc).__name__}: {exc}")
    finally:
        ctx.finish()
