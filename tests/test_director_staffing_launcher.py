from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys


ROOT = Path(".")


def test_director_bat_runs_minimal_staffing_setup_only() -> None:
    assert not (ROOT / "..START DIRECTOR STAFFING DASHBOARD.bat").exists()
    generator = _load_launcher_generator()
    seed = json.loads((ROOT / "config" / "staffing_seed.json").read_text(encoding="utf-8"))
    for school in [school["name"] for school in seed["schools"]]:
        path = ROOT / generator.director_launcher_filename(school)
        text = path.read_text(encoding="utf-8")

        assert "setup_director_staffing.ps1" in text
        assert "setup_and_run.ps1" not in text
        assert "-UiMode pyside" not in text
        assert "-DirectorStaffingMode" not in text
        assert f'-DirectorSchool "{school}"' in text
        assert "--director-staffing-v2" not in text
        assert text.splitlines() == generator.director_launcher_body(school).splitlines()
        assert 'cd /d "%~dp0"' in text


def test_director_launchers_match_staffing_seed_schools() -> None:
    generator = _load_launcher_generator()
    seed = json.loads((ROOT / "config" / "staffing_seed.json").read_text(encoding="utf-8"))
    expected_schools = [school["name"] for school in seed["schools"]]
    expected_paths = {school: ROOT / generator.director_launcher_filename(school) for school in expected_schools}

    for school in expected_schools:
        assert expected_paths[school].exists()


def test_generate_director_staffing_launchers_from_seed(tmp_path: Path) -> None:
    generator = _load_launcher_generator()
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps({"schools": [{"name": "Alpha School"}, {"name": "Beta/School"}]}),
        encoding="utf-8",
    )

    output_paths = generator.generate_director_launchers(root=tmp_path, seed_path=seed_path)

    assert [path.name for path in output_paths] == [
        "..START DIRECTOR STAFFING - Alpha School.bat",
        "..START DIRECTOR STAFFING - Beta School.bat",
    ]
    assert '-DirectorSchool "Alpha School"' in output_paths[0].read_text(encoding="utf-8")
    assert '-DirectorSchool "Beta/School"' in output_paths[1].read_text(encoding="utf-8")


def test_director_entrypoint_uses_staffing_only_modules_and_requirements() -> None:
    assert (ROOT / "src" / "director_staffing_app.py").exists()
    assert (ROOT / "setup_director_staffing.ps1").exists()
    assert (ROOT / "contracts" / "setup_director_staffing.contract.yaml").exists()
    assert (ROOT / "requirements-director.txt").exists()
    assert not (ROOT / "contracts" / "staffing_dashboard_app.contract.yaml").exists()

    director_requirements = (ROOT / "requirements-director.txt").read_text(encoding="utf-8")
    assert "PySide6==6.8.1.1" in director_requirements
    for package in ("python-docx", "soundfile", "faster-whisper", "transformers", "openvino"):
        assert package not in director_requirements


def test_director_setup_skips_full_app_audio_docx_and_ai_installers() -> None:
    script_text = Path("setup_director_staffing.ps1").read_text(encoding="utf-8")

    assert "requirements-director.txt" in script_text
    assert "director_staffing_app.py" in script_text
    assert 'Join-Path $env:LOCALAPPDATA "LPL_InterviewTool\\py311\\.venv\\Scripts\\python.exe"' in script_text
    assert "function Start-DirectorStaffingApp" in script_text
    assert "-WindowStyle Hidden" not in script_text
    assert "setup_and_run.ps1" not in script_text
    assert "requirements.txt" not in script_text
    for forbidden in ("Ensure-FFmpeg", "VB-CABLE", "Ollama", "DeepSeek", "requirements-openvino", "requirements-gpu"):
        assert forbidden not in script_text


def test_director_staffing_app_imports_without_docx_or_audio_modules() -> None:
    code = r"""
import importlib.abc
import sys

BLOCKED = {"docx", "scoring_reporting", "onboarding_operations", "interview_audio_recorder", "interview_runtime"}

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in BLOCKED:
            raise ImportError(f"blocked {fullname}")
        return None

sys.meta_path.insert(0, Blocker())
import director_staffing_app
print(director_staffing_app.parse_args(["--director-school", "Hawthorne"]).director_school)
"""
    env = {**os.environ, "PYTHONPATH": str((ROOT / "src").resolve())}
    result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, check=True)

    assert result.stdout.strip() == "Hawthorne"


def test_director_staffing_app_uses_school_specific_db_path(tmp_path: Path) -> None:
    module = _load_director_staffing_app()
    base = tmp_path / "staffing_dashboard.sqlite3"

    assert module.staffing_db_path_for_school("Palmdale", base_path=base) == tmp_path / "staffing_dashboard_palmdale.sqlite3"
    assert module.staffing_db_path_for_school("", base_path=base) == base


def test_director_staffing_app_backfills_palmdale_history_referrals(tmp_path: Path) -> None:
    module = _load_director_staffing_app()
    staffing_db = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    history_db = tmp_path / "interview_history.sqlite3"
    with sqlite3.connect(history_db) as conn:
        conn.execute(
            """
            CREATE TABLE interview_history (
                id INTEGER PRIMARY KEY,
                payload_json TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        for payload in (
            {
                "history_id": "hist-hire",
                "candidate_name": "Hire Candidate",
                "candidate_email": "hire@example.org",
                "school": "Palmdale",
                "position": "Teacher",
                "interview_date": "2026-07-08",
                "outcome": "Hire",
                "score": "88%",
            },
            {
                "history_id": "hist-borderline",
                "candidate_name": "Borderline Candidate",
                "school": "Palmdale",
                "position": "Assistant Teacher",
                "interview_date": "2026-07-08",
                "outcome": "Borderline",
                "score": "72%",
            },
            {
                "history_id": "hist-no-hire",
                "candidate_name": "No Hire Candidate",
                "school": "Palmdale",
                "position": "Teacher",
                "interview_date": "2026-07-08",
                "outcome": "No Hire",
                "score": "42%",
            },
            {
                "history_id": "hist-other-school",
                "candidate_name": "Other School Candidate",
                "school": "Hawthorne",
                "position": "Teacher",
                "interview_date": "2026-07-08",
                "outcome": "Hire",
                "score": "90%",
            },
        ):
            conn.execute(
                "INSERT INTO interview_history (payload_json, sort_order, created_at) VALUES (?, 0, '2026-07-08')",
                (json.dumps(payload),),
            )
        conn.commit()
    store = module.StaffingStore(staffing_db)
    store.initialize()

    imported = module.sync_director_referrals(
        store,
        school="Palmdale",
        history_db_path=history_db,
        history_json_path=tmp_path / "missing.json",
        queue_db_path=tmp_path / "staffing_referrals.sqlite3",
        queue_legacy_path=tmp_path / "missing.pending.jsonl",
    )

    pending = module.StaffingService(store).list_pending_director_interviews(school="Palmdale")
    assert imported == 2
    assert [candidate.candidate_name for candidate in pending] == ["Borderline Candidate", "Hire Candidate"]


def test_setup_and_run_passes_director_staffing_mode_to_pyside() -> None:
    script_text = Path("setup_and_run.ps1").read_text(encoding="utf-8")

    assert "[switch]$DirectorStaffingMode" in script_text
    assert "[string]$DirectorSchool = \"\"" in script_text
    assert "$Cfg.App.PreferredUiMode = \"pyside\"" in script_text
    assert '$wrapperArgs += "--director-staffing"' in script_text
    assert '$wrapperArgs += @("--director-school", $DirectorSchool.Trim())' in script_text


def _load_launcher_generator():
    path = ROOT / "tools" / "generate_director_staffing_launchers.py"
    spec = importlib.util.spec_from_file_location("generate_director_staffing_launchers", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_director_staffing_app():
    path = ROOT / "src" / "director_staffing_app.py"
    spec = importlib.util.spec_from_file_location("director_staffing_app", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
