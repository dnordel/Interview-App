from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


SOURCE_ROOT = Path("src")
BUTTON_TYPES = {"QPushButton", "QToolButton"}
SIGNAL_ATTRS = {"clicked", "pressed", "released", "toggled", "triggered"}
ACTION_METHODS = {"addAction", "setDefaultAction", "setMenu"}


@dataclass(frozen=True)
class ButtonFinding:
    key: str
    location: str


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _is_button_constructor(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id in BUTTON_TYPES
    return isinstance(node.func, ast.Attribute) and node.func.attr in BUTTON_TYPES


def _button_text(node: ast.Call) -> str:
    if not node.args:
        return ""
    first_arg = node.args[0]
    return first_arg.value if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str) else ""


def _assigned_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign) and _is_button_constructor(node.value):
        return [name for target in node.targets if (name := _dotted_name(target))]
    if isinstance(node, ast.AnnAssign) and _is_button_constructor(node.value):
        return [name] if (name := _dotted_name(node.target)) else []
    return []


def _call_receiver(node: ast.Call) -> tuple[str | None, str]:
    if not isinstance(node.func, ast.Attribute):
        return None, ""
    method = node.func.attr
    receiver = node.func.value
    if method == "connect" and isinstance(receiver, ast.Attribute) and receiver.attr in SIGNAL_ATTRS:
        return _dotted_name(receiver.value), method
    if method in ACTION_METHODS or method == "setEnabled":
        return _dotted_name(receiver), method
    return None, method


def _is_helper_wiring_call(node: ast.Call, name: str) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    if not node.func.attr.startswith("_wire") or not node.args:
        return False
    return _dotted_name(node.args[0]) == name


def _literal_call_arg(node: ast.Call, method_name: str) -> object | None:
    if not isinstance(node.func, ast.Attribute) or node.func.attr != method_name or not node.args:
        return None
    arg = node.args[0]
    return arg.value if isinstance(arg, ast.Constant) else None


def _button_metadata(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str, fallback_text: str) -> str:
    object_name = ""
    text = fallback_text
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        receiver, method = _call_receiver(node)
        if receiver != name:
            continue
        if method == "setObjectName":
            object_name = str(_literal_call_arg(node, "setObjectName") or "")
        if method == "setText":
            text = str(_literal_call_arg(node, "setText") or text)
    return object_name or text or name


def _is_returned(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    return any(
        isinstance(node, ast.Return) and _dotted_name(node.value) == name
        for node in ast.walk(function)
    )


def _is_disabled(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        receiver, method = _call_receiver(node)
        if receiver == name and method == "setEnabled" and _literal_call_arg(node, "setEnabled") is False:
            return True
    return False


def _has_action(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if _is_helper_wiring_call(node, name):
            return True
        receiver, method = _call_receiver(node)
        if receiver == name and (method == "connect" or method in ACTION_METHODS):
            return True
    return False


def _iter_button_findings() -> list[ButtonFinding]:
    findings: list[ButtonFinding] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for function in functions:
            for node in ast.walk(function):
                assigned_names = _assigned_names(node)
                if not assigned_names:
                    continue
                constructor = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
                fallback_text = _button_text(constructor) if isinstance(constructor, ast.Call) else ""
                for name in assigned_names:
                    if _has_action(function, name) or _is_disabled(function, name) or _is_returned(function, name):
                        continue
                    metadata = _button_metadata(function, name, fallback_text)
                    key = f"{path.as_posix()}::{function.name}::{name}::{metadata}"
                    findings.append(ButtonFinding(key=key, location=f"{path.as_posix()}:{node.lineno}"))
    return findings


def test_new_pyside_buttons_are_connected_to_actions() -> None:
    findings = _iter_button_findings()

    assert not findings, (
        "New enabled PySide buttons must be connected to an action signal, menu, or default action. "
        "Disabled passive controls and factory-returned buttons are allowed.\n"
        + "\n".join(f"{finding.location} -> {finding.key}" for finding in findings)
    )
