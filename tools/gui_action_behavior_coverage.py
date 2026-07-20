from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_POLICY_PATH = Path("docs/gui_action_behavior_coverage.yaml")
DEFAULT_SOURCE_ROOT = Path("src")
GUI_CONSTRUCTORS = {"QAction", "QPushButton", "QToolButton", "addAction", "addButton"}
GUI_SIGNALS = {"clicked", "pressed", "released", "toggled", "triggered"}
GUI_TEST_CALLS = {"click", "mouseClick", "trigger"}


@dataclass(frozen=True)
class _GuiAction:
    action_id: str
    description: str


class _GuiActionVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str, parents: dict[ast.AST, ast.AST]) -> None:
        self.relative_path = relative_path
        self.parents = parents
        self.scope: list[str] = []
        self.constructors: list[tuple[str, str, str, str]] = []
        self.connections: dict[tuple[str, str], list[str]] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        constructor = _attribute_name(node.func)
        target = _assigned_target(node)
        creates_inline_action = (
            constructor != "addAction"
            or target != "<inline>"
            or bool(node.args and isinstance(node.args[0], (ast.Constant, ast.JoinedStr)))
        )
        if constructor in GUI_CONSTRUCTORS and creates_inline_action:
            signature = ast.dump(node, annotate_fields=True, include_attributes=False)
            self.constructors.append((self._scope_name(), constructor, target, signature))

        if isinstance(node.func, ast.Attribute) and node.func.attr == "connect" and node.args:
            signal_expr = node.func.value
            if isinstance(signal_expr, ast.Attribute) and signal_expr.attr in GUI_SIGNALS:
                target = ast.unparse(signal_expr.value)
                callback = ast.dump(node.args[0], annotate_fields=True, include_attributes=False)
                self.connections.setdefault((self._scope_name(), target), []).append(
                    f"{signal_expr.attr}:{callback}"
                )
        self.generic_visit(node)

    def _scope_name(self) -> str:
        return ".".join(self.scope) or "<module>"


def _attribute_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _assigned_target(node: ast.Call) -> str:
    parent = getattr(node, "_gui_action_parent", None)
    if isinstance(parent, (ast.Assign, ast.AnnAssign)):
        target = parent.targets[0] if isinstance(parent, ast.Assign) else parent.target
        return ast.unparse(target)
    return "<inline>"


def _discover_file(path: Path, source_root: Path) -> list[_GuiAction]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for child, parent in parents.items():
        setattr(child, "_gui_action_parent", parent)
    relative_path = path.relative_to(source_root.parent).as_posix()
    visitor = _GuiActionVisitor(relative_path, parents)
    visitor.visit(tree)

    actions: list[_GuiAction] = []
    duplicate_counts: dict[str, int] = {}
    for scope, constructor, target, signature in visitor.constructors:
        callbacks = sorted(visitor.connections.get((scope, target), []))
        canonical = "|".join((relative_path, scope, constructor, target, signature, *callbacks))
        occurrence = duplicate_counts.get(canonical, 0)
        duplicate_counts[canonical] = occurrence + 1
        unique_canonical = f"{canonical}|occurrence={occurrence}"
        action_id = hashlib.sha256(unique_canonical.encode("utf-8")).hexdigest()[:20]
        description = f"{relative_path}:{scope} {constructor} {target}"
        actions.append(_GuiAction(action_id=action_id, description=description))
    return actions


def _load_policy(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _find_test(nodeid: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    path_text, separator, test_name = nodeid.partition("::")
    if not separator or not path_text.startswith("tests/") or not test_name.startswith("test_"):
        return None
    tests_root = Path("tests").resolve()
    path = Path(path_text).resolve()
    try:
        path.relative_to(tests_root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    base_name = test_name.split("[", 1)[0]
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == base_name
        ),
        None,
    )


def _is_behavioral_gui_test(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    has_assertion = any(isinstance(item, ast.Assert) for item in ast.walk(node))
    has_gui_invocation = any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr in GUI_TEST_CALLS
        for item in ast.walk(node)
    )
    return has_assertion and has_gui_invocation


def _inventory_digest(action_ids: list[str]) -> str:
    payload = "\n".join(sorted(action_ids)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def audit_gui_action_coverage(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    policy_path: Path = DEFAULT_POLICY_PATH,
) -> list[str]:
    """Return fail-closed GUI action-to-behavioral-test policy violations."""
    if not source_root.is_dir():
        return [f"source root not found: {source_root.as_posix()}"]

    policy = _load_policy(policy_path)
    if policy.get("version") != 1:
        return [f"{policy_path.as_posix()}: version must equal 1"]

    grandfathered = policy.get("grandfathered_files")
    covered = policy.get("covered_actions")
    if not isinstance(grandfathered, dict):
        return [f"{policy_path.as_posix()}: grandfathered_files must be a mapping"]
    if not isinstance(covered, dict):
        return [f"{policy_path.as_posix()}: covered_actions must be a mapping"]

    discovered_items = [
        action
        for path in sorted(source_root.rglob("*.py"))
        for action in _discover_file(path, source_root)
    ]
    discovered = {action.action_id: action for action in discovered_items}
    violations = [
        f"stale covered GUI action registration: {action_id}"
        for action_id in sorted(set(covered) - set(discovered))
    ]

    uncovered_by_file: dict[str, list[str]] = {}
    for action in discovered_items:
        if action.action_id in covered:
            continue
        relative_path = action.description.split(":", 1)[0]
        uncovered_by_file.setdefault(relative_path, []).append(action.action_id)

    all_files = set(grandfathered) | set(uncovered_by_file)
    for relative_path in sorted(all_files):
        expected = grandfathered.get(relative_path)
        action_ids = uncovered_by_file.get(relative_path, [])
        if not isinstance(expected, dict):
            fingerprints = ", ".join(
                f"{action.action_id} ({action.description})"
                for action in discovered_items
                if action.description.startswith(f"{relative_path}:")
            )
            violations.append(
                f"new GUI action file needs behavioral coverage: {relative_path}; fingerprints: {fingerprints}"
            )
            continue
        expected_count = expected.get("count")
        expected_sha256 = expected.get("sha256")
        actual_sha256 = _inventory_digest(action_ids)
        if expected_count != len(action_ids) or expected_sha256 != actual_sha256:
            fingerprints = ", ".join(
                f"{action.action_id} ({action.description})"
                for action in discovered_items
                if action.description.startswith(f"{relative_path}:") and action.action_id not in covered
            )
            violations.append(
                f"uncovered GUI action inventory changed in {relative_path}: "
                f"expected {expected_count}/{expected_sha256}, got {len(action_ids)}/{actual_sha256}; "
                "register each new fingerprint under covered_actions with behavioral_tests; "
                f"current uncovered fingerprints: {fingerprints}"
            )

    for action_id, entry in sorted(covered.items()):
        if not isinstance(entry, dict) or not isinstance(entry.get("behavioral_tests"), list):
            violations.append(f"covered GUI action {action_id}: behavioral_tests must be a list")
            continue
        tests = entry["behavioral_tests"]
        if not tests or not all(isinstance(nodeid, str) for nodeid in tests):
            violations.append(f"covered GUI action {action_id}: behavioral_tests must contain node ids")
            continue
        for nodeid in tests:
            node = _find_test(nodeid)
            if node is None:
                violations.append(f"covered GUI action {action_id}: test not found: {nodeid}")
            elif not _is_behavioral_gui_test(node):
                violations.append(
                    f"covered GUI action {action_id}: test must click/trigger GUI and assert behavior: {nodeid}"
                )
    return violations
