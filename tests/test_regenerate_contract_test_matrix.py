from __future__ import annotations

from pathlib import Path

import yaml

from tools import regenerate_contract_test_matrix as matrix_tool


REPORTING_EXPORTER_SYMBOLS = {
    "scoring_reporting.DocxExporter.__init__",
    "scoring_reporting.DocxExporter._require_candidate",
    "scoring_reporting.DocxExporter._require_candidate_field",
    "scoring_reporting.DocxExporter._extract_full_candidate_transcript",
    "scoring_reporting.DocxExporter.export",
}


def test_symbol_entries_include_contract_scoring_reporting_docx_exporter_methods() -> None:
    contract_path = Path("contracts/scoring_reporting.contract.yaml")
    payload = yaml.safe_load(contract_path.read_text(encoding="utf-8"))

    entries = matrix_tool._symbol_entries(contract_path, payload)
    symbols = {entry["symbol"] for entry in entries}

    assert REPORTING_EXPORTER_SYMBOLS.issubset(symbols)


def test_build_matrix_is_sorted_and_covers_contract_scoring_reporting_docx_exporter_methods() -> None:
    matrix = matrix_tool.build_matrix()
    entries = matrix["entries"]
    symbols = [entry["symbol"] for entry in entries]

    assert symbols == sorted(symbols)
    assert REPORTING_EXPORTER_SYMBOLS.issubset(set(symbols))


def test_dedupe_entries_rejects_duplicate_symbols() -> None:
    duplicate_entries = [
        {"contract": "contracts/example.contract.yaml", "symbol": "example.run", "kind": "function", "tests": []},
        {"contract": "contracts/example.contract.yaml", "symbol": "example.run", "kind": "function", "tests": []},
    ]

    try:
        matrix_tool._dedupe_entries(duplicate_entries)
    except ValueError as exc:
        assert "Duplicate contract symbol" in str(exc)
        return

    raise AssertionError("Expected duplicate symbol detection to raise ValueError")


def test_main_writes_matrix_file(tmp_path: Path, monkeypatch) -> None:
    matrix_path = tmp_path / "contract_test_coverage_matrix.yaml"

    monkeypatch.setattr(matrix_tool, "MATRIX_PATH", matrix_path)

    exit_code = matrix_tool.main()

    assert exit_code == 0
    written = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    written_symbols = {entry["symbol"] for entry in written["entries"]}
    assert REPORTING_EXPORTER_SYMBOLS.issubset(written_symbols)
