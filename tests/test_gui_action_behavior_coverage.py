from __future__ import annotations

import re

import yaml

from tools import gui_action_behavior_coverage


def test_every_gui_button_and_action_has_behavioral_test_coverage() -> None:
    violations = gui_action_behavior_coverage.audit_gui_action_coverage()

    assert violations == [], "GUI action behavioral coverage violations:\n" + "\n".join(violations)


def test_new_gui_action_without_behavioral_test_registration_fails_closed(tmp_path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "sample_gui.py").write_text(
        """
def build(QtWidgets, callback):
    save = QtWidgets.QPushButton("Save")
    save.clicked.connect(callback)
    return save
""".lstrip(),
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        yaml.safe_dump({"version": 1, "grandfathered_files": {}, "covered_actions": {}}),
        encoding="utf-8",
    )

    violations = gui_action_behavior_coverage.audit_gui_action_coverage(source_root, policy_path)

    assert len(violations) == 1
    assert "new GUI action file needs behavioral coverage: src/sample_gui.py" in violations[0]
    assert re.search(r"[0-9a-f]{20} \(src/sample_gui.py:build QPushButton save\)", violations[0])


def test_gui_action_registration_rejects_non_behavioral_test(tmp_path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "sample_gui.py").write_text(
        """
def build(QtWidgets, callback):
    save = QtWidgets.QPushButton("Save")
    save.clicked.connect(callback)
    return save
""".lstrip(),
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        yaml.safe_dump({"version": 1, "grandfathered_files": {}, "covered_actions": {}}),
        encoding="utf-8",
    )
    first_violation = gui_action_behavior_coverage.audit_gui_action_coverage(source_root, policy_path)[0]
    action_id = re.search(r"[0-9a-f]{20}", first_violation).group(0)
    empty_digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    policy_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "grandfathered_files": {
                    "src/sample_gui.py": {"count": 0, "sha256": empty_digest},
                },
                "covered_actions": {
                    action_id: {
                        "behavioral_tests": [
                            "tests/test_gui_action_behavior_coverage.py::"
                            "test_gui_action_registration_rejects_non_behavioral_test"
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    violations = gui_action_behavior_coverage.audit_gui_action_coverage(source_root, policy_path)

    assert violations == [
        f"covered GUI action {action_id}: test must click/trigger GUI and assert behavior: "
        "tests/test_gui_action_behavior_coverage.py::"
        "test_gui_action_registration_rejects_non_behavioral_test"
    ]
