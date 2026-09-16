"""Đọc kết quả test/coverage ở định dạng chuẩn, không phụ thuộc ngôn ngữ.

JUnit XML: pytest, jest, go-junit-report, maven… đều xuất được.
coverage.json: định dạng của coverage.py.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestReport:
    total: int = 0
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # test id -> file

    @property
    def passed(self) -> int:
        return self.total - len(self.failed) - len(self.skipped)


def junit(path: Path) -> TestReport:
    rep = TestReport()
    if not path.is_file():
        return rep
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        name = case.get("name", "?")
        classname = case.get("classname", "")
        test_id = f"{classname}::{name}" if classname else name
        rep.total += 1
        # junit_family=xunit2 (mặc định của pytest) không có attribute file.
        # Fallback sang classname dạng module để còn so được với scope.
        rep.files[test_id] = case.get("file") or classname.replace(".", "/")
        if case.find("failure") is not None or case.find("error") is not None:
            rep.failed.append(test_id)
        elif case.find("skipped") is not None:
            rep.skipped.append(test_id)
    return rep


@dataclass
class Coverage:
    total_pct: float = 0.0
    executed: dict[str, set[int]] = field(default_factory=dict)


def coverage_json(path: Path) -> Coverage | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    cov = Coverage(total_pct=float(raw.get("totals", {}).get("percent_covered", 0.0)))
    for name, entry in (raw.get("files") or {}).items():
        cov.executed[name] = set(entry.get("executed_lines", []))
    return cov
