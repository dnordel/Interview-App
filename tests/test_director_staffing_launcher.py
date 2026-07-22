from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys

import pytest


ROOT = Path(".")


def test_director_bat_runs_minimal_staffing_setup_only() -> None:
    assert not (ROOT / "..START DIRECTOR STAFFING DASHBOARD.bat").exists()
    generator = _load_launcher_generator()
    seed = json.loads((ROOT / "config" / "staffing_seed.json").read_text(encoding="utf-8"))
    for school in [school["name"] for school in seed["schools"]]:
        path = ROOT / generator.director_launcher_filename(school)
        text = path.read_text(encoding="utf-8")
        vbs_path = ROOT / generator.director_vbs_launcher_filename(school)
        vbs_text = vbs_path.read_text(encoding="utf-8")

        assert "setup_director_staffing.ps1" not in text
        assert "setup_director_staffing.ps1" in vbs_text
        assert "setup_and_run.ps1" not in text
        assert "setup_and_run.ps1" not in vbs_text
        assert "-UiMode pyside" not in text
        assert "-DirectorStaffingMode" not in text
        assert f'-DirectorSchool " & Chr(34) & "{school}" & Chr(34)' in vbs_text
        assert "--director-staffing-v2" not in text
        assert text.splitlines() == generator.director_launcher_body(school).splitlines()
        assert vbs_text.splitlines() == generator.director_vbs_launcher_body(school).splitlines()
        assert 'cd /d "%~dp0"' in text
        assert 'shell.Run command, 0, False' in vbs_text
        assert "CreateShortcut" not in vbs_text
        assert f'wscript.exe "%CD%\\{generator.director_vbs_launcher_filename(school)}"' in text


def test_director_launchers_match_staffing_seed_schools() -> None:
    generator = _load_launcher_generator()
    seed = json.loads((ROOT / "config" / "staffing_seed.json").read_text(encoding="utf-8"))
    expected_schools = [school["name"] for school in seed["schools"]]
    expected_paths = {school: ROOT / generator.director_launcher_filename(school) for school in expected_schools}

    for school in expected_schools:
        assert expected_paths[school].exists()
        assert (ROOT / generator.director_vbs_launcher_filename(school)).exists()


def test_director_launcher_scope_is_exactly_three_canonical_locations() -> None:
    generator = _load_launcher_generator()

    assert generator.load_staffing_schools() == ["Hawthorne", "North Long Beach", "Palmdale"]


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
        "..START DIRECTOR STAFFING - Alpha School.vbs",
        "..START DIRECTOR STAFFING - Beta School.bat",
        "..START DIRECTOR STAFFING - Beta School.vbs",
    ]
    assert 'wscript.exe "%CD%\\..START DIRECTOR STAFFING - Alpha School.vbs"' in output_paths[0].read_text(encoding="utf-8")
    assert '-DirectorSchool " & Chr(34) & "Alpha School" & Chr(34)' in output_paths[1].read_text(encoding="utf-8")
    assert 'shell.Run command, 0, False' in output_paths[1].read_text(encoding="utf-8")
    assert 'wscript.exe "%CD%\\..START DIRECTOR STAFFING - Beta School.vbs"' in output_paths[2].read_text(encoding="utf-8")
    assert '-DirectorSchool " & Chr(34) & "Beta/School" & Chr(34)' in output_paths[3].read_text(encoding="utf-8")


def test_director_vbs_activates_existing_dashboard_instead_of_launching_again() -> None:
    body = _load_launcher_generator().director_vbs_launcher_body("Palmdale")

    activation = 'If shell.AppActivate("Director Staffing Dashboard") Then'
    assert activation in body
    assert "WScript.Quit 0" in body
    assert body.index(activation) < body.index("shell.Run command, 0, False")


def test_director_vbs_restores_existing_dashboard_before_exiting() -> None:
    body = _load_launcher_generator().director_vbs_launcher_body("Palmdale")

    assert "WScript.Sleep 100" in body
    assert 'shell.SendKeys "% x"' in body
    assert body.index('shell.SendKeys "% x"') < body.index("WScript.Quit 0")


def test_director_setup_does_not_create_desktop_shortcuts() -> None:
    setup_text = Path("setup_director_staffing.ps1").read_text(encoding="utf-8")

    assert "CreateShortcut" not in setup_text
    assert "Install-DirectorStaffingDesktopShortcut" not in setup_text
    assert '"Director Staffing - $safeSchool.lnk"' not in setup_text


def test_director_entrypoint_uses_staffing_only_modules_and_requirements() -> None:
    assert (ROOT / "src" / "director_staffing_app.py").exists()
    assert (ROOT / "setup_director_staffing.ps1").exists()
    assert (ROOT / "contracts" / "setup_director_staffing.contract.yaml").exists()
    assert (ROOT / "requirements-director.txt").exists()
    assert not (ROOT / "contracts" / "staffing_dashboard_app.contract.yaml").exists()

    director_requirements = (ROOT / "requirements-director.txt").read_text(encoding="utf-8")
    assert "PySide6==6.8.1.1" in director_requirements
    assert "python-docx==1.2.0" in director_requirements
    for package in ("soundfile", "faster-whisper", "transformers", "openvino"):
        assert package not in director_requirements


def test_director_requirements_include_windows_vault_dependency() -> None:
    director_requirements = (ROOT / "requirements-director.txt").read_text(encoding="utf-8")

    assert 'pywin32==312; sys_platform == "win32"' in director_requirements


def test_director_setup_repairs_existing_environment_missing_windows_vault_dependency() -> None:
    script_text = Path("setup_director_staffing.ps1").read_text(encoding="utf-8")

    assert '@("-c", "import PySide6, docx, win32crypt")' in script_text


def test_director_setup_installs_docx_without_full_app_audio_installers() -> None:
    script_text = Path("setup_director_staffing.ps1").read_text(encoding="utf-8")

    assert "requirements-director.txt" in script_text
    assert "director_staffing_app.py" in script_text
    assert 'Join-Path $env:LOCALAPPDATA "LPL_InterviewTool\\py311\\.venv\\Scripts\\python.exe"' in script_text
    assert "function Start-DirectorStaffingApp" in script_text
    assert "-WindowStyle Hidden" not in script_text
    assert "setup_and_run.ps1" not in script_text
    assert "requirements.txt" not in script_text
    assert '@("-c", "import PySide6, docx, win32crypt")' in script_text
    for forbidden in ("Ensure-FFmpeg", "VB-CABLE", "requirements-openvino", "requirements-gpu"):
        assert forbidden not in script_text


def test_director_setup_starts_gui_process_without_console_window() -> None:
    script_text = Path("setup_director_staffing.ps1").read_text(encoding="utf-8")

    assert "System.Diagnostics.ProcessStartInfo" in script_text
    assert "$startInfo.UseShellExecute = $false" in script_text
    assert "$startInfo.CreateNoWindow = $true" in script_text
    assert "$startInfo.RedirectStandardOutput = $true" in script_text
    assert "$startInfo.RedirectStandardError = $true" in script_text
    assert "[System.Diagnostics.Process]::Start($startInfo)" in script_text
    assert "Start-Process -FilePath $launcher" not in script_text


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


def test_director_staffing_window_launches_maximized_to_available_screen() -> None:
    module = _load_director_staffing_app()
    calls: list[tuple[str, int, int, int, int] | tuple[str]] = []

    class FakeRect:
        def __init__(self, x: int, y: int, width: int, height: int) -> None:
            self._x = x
            self._y = y
            self._width = width
            self._height = height

        def x(self) -> int:
            return self._x

        def y(self) -> int:
            return self._y

        def width(self) -> int:
            return self._width

        def height(self) -> int:
            return self._height

    class FakeScreen:
        def availableGeometry(self) -> FakeRect:
            return FakeRect(0, 0, 1920, 1040)

    class FakeApplication:
        @staticmethod
        def primaryScreen() -> FakeScreen:
            return FakeScreen()

    FakeQtWidgets = type("FakeQtWidgets", (), {"Q" + "Application": FakeApplication})

    class FakeWindow:
        def screen(self) -> FakeScreen:
            return FakeScreen()

        def setGeometry(self, rect: FakeRect) -> None:
            calls.append(("setGeometry", rect.x(), rect.y(), rect.width(), rect.height()))

        def showMaximized(self) -> None:
            calls.append(("showMaximized",))

    module._show_director_staffing_window_maximized(FakeWindow(), FakeQtWidgets)

    assert calls == [("setGeometry", 0, 0, 1920, 1040), ("showMaximized",)]


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_staffing_v2_forces_light_fusion_theme_for_consistent_colors() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    app.setPalette(qt_gui.QPalette())
    module = _load_director_staffing_app()

    module.apply_staffing_v2_light_theme(qt_widgets, qt_gui, app)

    assert app.style().objectName().lower() == "fusion"
    assert app.property("_staffing_v2_forced_light_theme") is True
    assert app.palette().color(qt_gui.QPalette.ColorRole.Window).name().lower() == "#f8fafc"
    assert app.palette().color(qt_gui.QPalette.ColorRole.Base).name().lower() == "#ffffff"
    assert app.palette().color(qt_gui.QPalette.ColorRole.Text).name().lower() == "#0f172a"


@pytest.mark.pyside_gui
def test_director_window_close_guard_cancels_or_cleans_onboarding_close() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    module = _load_director_staffing_app()
    window = qt_widgets.QMainWindow()
    allowed = {"value": False}
    cleaned: list[bool] = []
    module.install_director_window_close_guard(
        qt_core, window,
        request_close=lambda: allowed["value"],
        cleanup=lambda: cleaned.append(True),
    )
    window.show()

    assert window.close() is False
    assert cleaned == []
    allowed["value"] = True
    assert window.close() is True
    app.processEvents()
    assert cleaned == [True]


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


def test_director_staffing_app_syncs_removed_candidate_to_admin_audit(tmp_path: Path) -> None:
    module = _load_director_staffing_app()
    admin_store = module.StaffingStore(tmp_path / "staffing_dashboard.sqlite3")
    director_store = module.StaffingStore(module.staffing_db_path_for_school("Palmdale", base_path=tmp_path / "staffing_dashboard.sqlite3"))
    admin_store.initialize()
    director_store.initialize()
    admin_service = module.StaffingService(admin_store)
    director_service = module.StaffingService(director_store)
    for service in (admin_service, director_service):
        service.upsert_director_candidate_referral(
            history_id="hist-palmdale-remove",
            candidate_name="Remove Me",
            school="Palmdale",
            position="Teacher",
            interviewer_rating=8.8,
            interviewer_outcome="hire",
            interview_date="2026-07-08",
        )
    director_candidate = director_service.list_pending_director_interviews(school="Palmdale")[0]
    assert director_service.delete_pending_director_interviews(
        [director_candidate.id],
        removed_by="director",
        removal_source="director_staffing_dashboard",
    ) == 1
    module.append_director_referral_dismissal_event(
        history_id=director_candidate.history_id,
        school=director_candidate.school,
        candidate_name=director_candidate.candidate_name,
        removed_by="director",
        removal_source="director_staffing_dashboard",
        queue_db_path=tmp_path / "staffing_referrals.sqlite3",
        queue_legacy_path=tmp_path / "missing.pending.jsonl",
    )

    imported = module.sync_director_referrals(
        admin_store,
        school="Palmdale",
        history_db_path=tmp_path / "missing-history.sqlite3",
        history_json_path=tmp_path / "missing-history.json",
        queue_db_path=tmp_path / "staffing_referrals.sqlite3",
        queue_legacy_path=tmp_path / "missing.pending.jsonl",
    )

    audit = admin_store.list_director_referral_removal_audit()
    assert imported == 1
    assert admin_service.list_pending_director_interviews(school="Palmdale") == []
    assert len(audit) == 1
    assert audit[0].candidate_name == "Remove Me"
    assert audit[0].removed_by == "director"
    assert audit[0].removal_source == "director_staffing_dashboard"


def test_director_dashboard_delete_applies_to_admin_db_before_queue_poll(tmp_path: Path) -> None:
    module = _load_director_staffing_app()
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    admin_store = module.StaffingStore(admin_path)
    director_store = module.StaffingStore(module.staffing_db_path_for_school("Palmdale", base_path=admin_path))
    admin_store.initialize()
    director_store.initialize()
    admin_service = module.StaffingService(admin_store)
    director_service = module.StaffingService(director_store)
    for service in (admin_service, director_service):
        service.upsert_director_candidate_referral(
            history_id="hist-direct-admin-delete",
            candidate_name="Director Removes First",
            school="Palmdale",
            position="Teacher",
            interviewer_rating=8.4,
            interviewer_outcome="hire",
            interview_date="2026-07-08",
        )
    director_candidate = director_service.list_pending_director_interviews(school="Palmdale")[0]

    module.apply_director_referral_dismissal_to_store(
        db_path=admin_path,
        history_id=director_candidate.history_id,
        school=director_candidate.school,
        candidate_name=director_candidate.candidate_name,
        removed_by="director",
        removal_source="director_staffing_dashboard",
    )

    audit = admin_store.list_director_referral_removal_audit(school="Palmdale")
    assert admin_service.list_pending_director_interviews(school="Palmdale") == []
    assert len(audit) == 1
    assert audit[0].candidate_name == "Director Removes First"
    assert audit[0].removed_by == "director"


def test_director_referral_double_delete_records_both_actors_without_readding(tmp_path: Path) -> None:
    module = _load_director_staffing_app()
    admin_store = module.StaffingStore(tmp_path / "staffing_dashboard.sqlite3")
    director_store = module.StaffingStore(module.staffing_db_path_for_school("Palmdale", base_path=tmp_path / "staffing_dashboard.sqlite3"))
    admin_store.initialize()
    director_store.initialize()
    admin_service = module.StaffingService(admin_store)
    director_service = module.StaffingService(director_store)
    for service in (admin_service, director_service):
        service.upsert_director_candidate_referral(
            history_id="hist-double-delete",
            candidate_name="Double Delete Candidate",
            school="Palmdale",
            position="Teacher",
            interviewer_rating=8.2,
            interviewer_outcome="hire",
            interview_date="2026-07-08",
        )
    queue_path = tmp_path / "staffing_referrals.sqlite3"
    admin_candidate = admin_service.list_pending_director_interviews(school="Palmdale")[0]
    director_candidate = director_service.list_pending_director_interviews(school="Palmdale")[0]

    assert admin_service.delete_pending_director_interviews(
        [admin_candidate.id],
        removed_by="admin",
        removal_source="admin_staffing_dashboard",
    ) == 1
    module.append_director_referral_dismissal_event(
        history_id=admin_candidate.history_id,
        school=admin_candidate.school,
        candidate_name=admin_candidate.candidate_name,
        removed_by="admin",
        removal_source="admin_staffing_dashboard",
        queue_db_path=queue_path,
        queue_legacy_path=tmp_path / "missing.pending.jsonl",
    )
    assert director_service.delete_pending_director_interviews(
        [director_candidate.id],
        removed_by="director",
        removal_source="director_staffing_dashboard",
    ) == 1
    module.append_director_referral_dismissal_event(
        history_id=director_candidate.history_id,
        school=director_candidate.school,
        candidate_name=director_candidate.candidate_name,
        removed_by="director",
        removal_source="director_staffing_dashboard",
        queue_db_path=queue_path,
        queue_legacy_path=tmp_path / "missing.pending.jsonl",
    )

    admin_imported = module.sync_director_referrals(
        admin_store,
        school="Palmdale",
        history_db_path=tmp_path / "missing-history.sqlite3",
        history_json_path=tmp_path / "missing-history.json",
        queue_db_path=queue_path,
        queue_legacy_path=tmp_path / "missing.pending.jsonl",
    )
    director_imported = module.sync_director_referrals(
        director_store,
        school="Palmdale",
        history_db_path=tmp_path / "missing-history.sqlite3",
        history_json_path=tmp_path / "missing-history.json",
        queue_db_path=queue_path,
        queue_legacy_path=tmp_path / "missing.pending.jsonl",
    )

    admin_audit = admin_store.list_director_referral_removal_audit(school="Palmdale")
    director_audit = director_store.list_director_referral_removal_audit(school="Palmdale")
    assert admin_imported == 2
    assert director_imported == 0
    assert admin_service.list_pending_director_interviews(school="Palmdale") == []
    assert director_service.list_pending_director_interviews(school="Palmdale") == []
    assert {row.removed_by for row in admin_audit} == {"admin", "director"}
    assert {row.removed_by for row in director_audit} == {"director"}


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
