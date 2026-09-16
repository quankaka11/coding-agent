"""Soi test bằng AST (quyết định C3) và sinh đột biến giới hạn (C1). Chỉ Python."""
from __future__ import annotations

import ast
import re
from pathlib import Path

# Các assert chỉ khẳng định "đã được gọi", không khẳng định giá trị.
_CALL_ASSERTS = re.compile(r"^assert_(called|awaited|any_call|has_calls|not_called|not_awaited)")
# Khẳng định về hành vi, không đi qua câu lệnh `assert`: pytest.raises, unittest
# assertRaises… Đây là assert thật, không phải mock.
_BEHAVIOUR_ASSERTS = re.compile(r"^(raises|warns|deprecated_call|assertRaises\w*|assertWarns\w*)$")


def parse(source: str) -> ast.AST | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def test_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]


def has_value_assert(func: ast.AST) -> bool:
    """C3a: có ít nhất một khẳng định về giá trị, không chỉ về việc đã gọi."""
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", ""))
            if _BEHAVIOUR_ASSERTS.match(name):
                return True
            if name.startswith("assert") and not _CALL_ASSERTS.match(name):
                return True
    return False


def mocked_targets(tree: ast.AST) -> set[str]:
    """C3b: các chuỗi đích bị patch — patch("a.b.C"), mock.patch.object(...)."""
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name not in ("patch", "object", "patch_object"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                targets.add(arg.value)
    return targets


def ac_markers(func: ast.AST, marker: str = "pytest.mark.ac") -> set[str]:
    """C2: @pytest.mark.ac("AC-1")."""
    found: set[str] = set()
    want = marker.split(".")[-1]
    for deco in getattr(func, "decorator_list", []):
        call = deco if isinstance(deco, ast.Call) else None
        target = call.func if call else deco
        if isinstance(target, ast.Attribute) and target.attr == want and call:
            found |= {a.value for a in call.args
                      if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    return found


class _Mutator(ast.NodeTransformer):
    """Đột biến đúng một nút, chỉ trong các dòng được chỉ định."""

    FLIP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE,
            ast.GtE: ast.Lt, ast.Gt: ast.LtE, ast.LtE: ast.Gt}

    def __init__(self, lines: set[int], index: int) -> None:
        self.lines, self.index, self.seen, self.applied = lines, index, 0, ""

    def _take(self, node: ast.AST) -> bool:
        if getattr(node, "lineno", None) not in self.lines:
            return False
        self.seen += 1
        return self.seen - 1 == self.index

    def visit_Compare(self, node: ast.Compare):
        self.generic_visit(node)
        if len(node.ops) == 1 and type(node.ops[0]) in self.FLIP and self._take(node):
            old = type(node.ops[0]).__name__
            node.ops = [self.FLIP[type(node.ops[0])]()]
            self.applied = f"dòng {node.lineno}: đảo toán tử {old}"
        return node

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, bool) and self._take(node):
            self.applied = f"dòng {node.lineno}: {node.value} → {not node.value}"
            return ast.copy_location(ast.Constant(value=not node.value), node)
        return node


def mutants(source: str, lines: set[int], limit: int = 20) -> list[tuple[str, str]]:
    """[(mô tả, source đã đột biến)] — chỉ đụng các dòng được chỉ định."""
    tree = parse(source)
    if tree is None:
        return []
    total = _Mutator(lines, -1)
    total.visit(ast.parse(source))
    out: list[tuple[str, str]] = []
    for i in range(min(total.seen, limit)):
        mut = _Mutator(lines, i)
        new_tree = mut.visit(ast.parse(source))
        if mut.applied:
            out.append((mut.applied, ast.unparse(ast.fix_missing_locations(new_tree))))
    return out


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
