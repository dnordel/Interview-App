"""Contract scaffolding path normalization tests.

These tests guard module-name normalization used by contract scaffolding so
source files with different semantics cannot collide on a single contract key.
"""

from pathlib import Path


def _module_name_for_path(path: Path, source_root: Path) -> str:
    """Return a stable module-like identifier for a source path.

    `__init__.py` files keep an explicit `.__init__` suffix so they don't
    collide with sibling modules like `package.py` or `package.pyw`.
    """

    rel = path.relative_to(source_root)

    if rel.name == "__init__.py":
        parts = rel.with_suffix("").parts
        return ".".join(parts)

    return ".".join(rel.with_suffix("").parts)


def test_init_module_and_sibling_py_have_distinct_names():
    source_root = Path("src")

    init_name = _module_name_for_path(Path("src/interview_app/__init__.py"), source_root)
    py_name = _module_name_for_path(Path("src/pyside_interview_app.py"), source_root)

    assert init_name == "interview_app.__init__"
    assert py_name == "pyside_interview_app"
    assert init_name != py_name


def test_regular_module_name_mapping_drops_suffix_only():
    source_root = Path("src")

    module_name = _module_name_for_path(Path("src/interview_app/history_actions.py"), source_root)

    assert module_name == "interview_app.history_actions"
