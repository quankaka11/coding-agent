"""Đọc kết quả test/coverage ở định dạng chuẩn, không phụ thuộc ngôn ngữ.

JUnit XML: pytest, jest, vitest, gotestsum, maven, gradle, phpunit… đều xuất được.
coverage.json: định dạng của coverage.py.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestReport:
    total: int = 0
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # test id -> file
    #: Tập con của `failed`: những mục có <error> chứ không phải <failure>.
    #: JUnit phân biệt rõ "không chạy được" (import hỏng, cú pháp sai, fixture nổ)
    #: với "chạy rồi và khẳng định sai". Hai thứ này nói về hai loại lỗi khác hẳn
    #: nhau, mà gộp lại thì không còn phân biệt được lỗi của người viết test với
    #: lỗi của người viết code.
    errored: list[str] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return self.total - len(self.failed) - len(self.skipped)


def reset(path: Path) -> None:
    """Xoá kết quả junit cũ trước khi chạy lại lệnh test.

    Cùng một đường dẫn được dùng nhiều lần trong một run (gate, test-first). Lệnh test
    chết trước khi ghi file thì lần đọc sau vớ phải kết quả của lần TRƯỚC — gate xanh
    trên một lần chạy không hề diễn ra.
    """
    import shutil
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def junit(path: Path) -> TestReport:
    """Đọc JUnit XML. `path` là một file, hoặc một THƯ MỤC chứa nhiều file XML.

    Thư mục là cách chung cho các tool ghi mỗi suite một file (maven surefire, gradle,
    phpunit theo suite): lệnh test chỉ cần `mkdir -p {junit} && cp .../*.xml {junit}/`.
    File hỏng không làm đổ run — nó được bỏ qua như không có.
    """
    rep = TestReport()
    files = sorted(path.rglob("*.xml")) if path.is_dir() else [path] if path.is_file() else []
    for one in files:
        try:
            root = ET.parse(one).getroot()
        except (ET.ParseError, OSError):
            continue
        _read_cases(root, rep)
    return rep


def _read_cases(root, rep: TestReport) -> None:
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
            if case.find("error") is not None:
                rep.errored.append(test_id)
        elif case.find("skipped") is not None:
            rep.skipped.append(test_id)


@dataclass
class Coverage:
    total_pct: float = 0.0
    executed: dict[str, set[int]] = field(default_factory=dict)
    #: Dòng LÀ câu lệnh nhưng không chạy qua. `executed | missing` là tập dòng
    #: đo được — dòng trống, dòng tiếp nối của một câu lệnh nhiều dòng, hay dòng
    #: bị loại trừ đều không nằm trong đó và không được tính vào mẫu số.
    missing: dict[str, set[int]] = field(default_factory=dict)

    def measurable(self, file: str) -> set[int]:
        return self.executed.get(file, set()) | self.missing.get(file, set())


def coverage_json(path: Path) -> Coverage | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    cov = Coverage(total_pct=float(raw.get("totals", {}).get("percent_covered", 0.0)))
    for name, entry in (raw.get("files") or {}).items():
        cov.executed[name] = set(entry.get("executed_lines", []))
        cov.missing[name] = set(entry.get("missing_lines", []))
    return cov


#: Fence chỉ tính khi đứng ĐẦU dòng. Spec/plan hay nhắc tới ```mermaid, ```python ngay trong
#: câu chữ; cắt theo mọi ``` thì khối YAML bị chặt giữa chừng và agent phải viết lại.
_FENCE = re.compile(r"^[ \t]*```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n(.*?)^[ \t]*```[ \t]*$",
                    re.MULTILINE | re.DOTALL)


def fenced_block(text: str, langs: tuple[str, ...] = ("yaml", "yml")) -> str:
    """Nội dung khối ```yaml đầu tiên; không có thì khối fence đầu tiên; không có nữa thì cả text."""
    blocks = [(m.group(1).lower(), m.group(2)) for m in _FENCE.finditer(text.strip() + "\n")]
    for lang, body in blocks:
        if lang in langs:
            return body
    return blocks[0][1] if blocks else text.strip()
