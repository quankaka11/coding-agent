"""Spec YAML — hợp đồng giữa Phase A và Phase B (schema A4)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

TASK_TYPES = ("T1", "T2", "T3", "T4")
READY_KEYS = ("clear", "has_acceptance_criteria", "reproducible", "scoped",
              "deterministically_verifiable")
T3_READY_KEYS = ("interface_specified", "ac_testable", "modules_declared")


class SpecError(ValueError):
    """Spec agent sinh ra không đúng schema — không đoán, trả về cho agent viết lại."""


@dataclass
class Spec:
    task_id: str
    objective: str
    task_type: str
    acceptance_criteria: list[dict] = field(default_factory=list)
    repro_steps: list[str] = field(default_factory=list)
    scope: dict = field(default_factory=dict)
    readiness: dict = field(default_factory=dict)
    source_ticket: str = ""

    @property
    def ac_ids(self) -> list[str]:
        return [ac["id"] for ac in self.acceptance_criteria]

    @property
    def modules(self) -> list[str]:
        return list(self.scope.get("modules") or [])

    def not_ready(self) -> list[str]:
        keys = READY_KEYS + (T3_READY_KEYS if self.task_type == "T3" else ())
        return [k for k in keys if self.readiness.get(k) != "pass"]

    def save(self, path: Path) -> None:
        path.write_text(yaml.safe_dump(asdict(self), allow_unicode=True, sort_keys=False),
                        encoding="utf-8")


def parse(text: str) -> Spec:
    """Đọc spec từ YAML thô, kiểm schema. Chấp nhận cả khối ```yaml."""
    body = text.strip()
    if "```" in body:
        # Ưu tiên khối gắn nhãn yaml. Lấy bừa khối đầu tiên sẽ vớ phải ```python
        # mà agent trích từ repro_steps của ticket.
        blocks = body.split("```")[1::2]
        tagged = [b[4:] for b in blocks if b.startswith("yaml")]
        body = tagged[0] if tagged else (blocks[0] if blocks else body)
    try:
        raw = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise SpecError(f"không parse được YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise SpecError("spec phải là một mapping YAML")

    known = {f for f in Spec.__dataclass_fields__}
    spec = Spec(**{k: v for k, v in raw.items() if k in known},
                **{k: "" for k in ("task_id",) if k not in raw})
    errs = []
    if not spec.objective:
        errs.append("thiếu objective")
    if spec.task_type not in TASK_TYPES:
        errs.append(f"task_type phải thuộc {TASK_TYPES}, đang là {spec.task_type!r}")
    for i, ac in enumerate(spec.acceptance_criteria):
        if not isinstance(ac, dict) or not ac.get("id") or not ac.get("text"):
            errs.append(f"acceptance_criteria[{i}] phải có id và text")
    if spec.task_type != "T4" and not spec.acceptance_criteria:
        errs.append("thiếu acceptance_criteria")
    missing_ready = [k for k in READY_KEYS if k not in spec.readiness]
    if missing_ready:
        errs.append(f"readiness thiếu {missing_ready}")
    if errs:
        raise SpecError("; ".join(errs))
    return spec


def load(path: Path) -> Spec:
    return parse(path.read_text(encoding="utf-8"))
