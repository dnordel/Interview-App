from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _decorator_name(node: ast.expr) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _renders_qt_screenshot(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    has_grab = False
    has_save = False
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        has_grab = has_grab or child.func.attr == "grab"
        has_save = has_save or child.func.attr == "save"
    return has_grab and has_save


def _registers_seeded_visual_database(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(child.func, ast.Attribute)
        and child.func.attr == "expect_seeded"
        and isinstance(child.func.value, ast.Name)
        and child.func.value.id == "visual_test_databases"
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
    )


def test_qt_screenshot_tests_require_seeded_visual_test_db() -> None:
    failures: list[str] = []
    rendered_tests = 0
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _renders_qt_screenshot(node):
                continue
            rendered_tests += 1
            decorators = {_decorator_name(value) for value in node.decorator_list}
            parameters = {argument.arg for argument in node.args.args}
            prefix = f"{path.name}::{node.name}"
            if "pytest.mark.pyside_gui" not in decorators:
                failures.append(f"{prefix} missing @pytest.mark.pyside_gui")
            if "pytest.mark.visual_inspection" not in decorators:
                failures.append(f"{prefix} missing @pytest.mark.visual_inspection")
            if "visual_test_databases" not in parameters:
                failures.append(f"{prefix} missing visual_test_databases fixture")
            if not _registers_seeded_visual_database(node):
                failures.append(f"{prefix} does not register a seeded domain DB")
    assert rendered_tests > 0, "No Qt screenshot tests found"
    assert not failures, "\n".join(failures)
