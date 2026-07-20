from __future__ import annotations

from pathlib import Path

from tools.check_contract_review import _check_coverage_matrix, run_checks


def test_run_checks_returns_requested_sections() -> None:
    results = run_checks(["baseline", "schema"], max_age_days=45)

    names = [item.name for item in results]
    assert names == ["baseline", "schema"]
    assert all(isinstance(item.ok, bool) for item in results)


def test_coverage_matrix_check_fails_for_stale_date(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text(
        "\n".join(
            [
                "last_updated: 2020-01-01",
                "sections:",
                "  - name: sample",
                "    tests:",
                "      - tests/test_check_contract_review.py",
            ]
        ),
        encoding="utf-8",
    )

    result = _check_coverage_matrix(matrix, max_age_days=30)

    assert result.ok is False
    assert any("stale" in message for message in result.messages)


def test_coverage_matrix_check_rejects_missing_test_paths(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text(
        "\n".join(
            [
                "last_updated: 2026-03-07",
                "sections:",
                "  - name: sample",
                "    tests:",
                "      - tests/does_not_exist.py",
            ]
        ),
        encoding="utf-8",
    )

    result = _check_coverage_matrix(matrix, max_age_days=3650)

    assert result.ok is False
    assert any("not found" in message for message in result.messages)
