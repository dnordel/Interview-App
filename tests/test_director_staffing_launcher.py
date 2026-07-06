from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(".")


def test_director_bat_runs_full_pyside_setup_in_staffing_only_mode() -> None:
    assert not (ROOT / "..START DIRECTOR STAFFING DASHBOARD.bat").exists()
    generator = _load_launcher_generator()
    seed = json.loads((ROOT / "config" / "staffing_seed.json").read_text(encoding="utf-8"))
    for school in [school["name"] for school in seed["schools"]]:
        path = ROOT / generator.director_launcher_filename(school)
        text = path.read_text(encoding="utf-8")

        assert "setup_and_run.ps1" in text
        assert "-UiMode pyside" in text
        assert "-DirectorStaffingMode" in text
        assert f'-DirectorSchool "{school}"' in text
        assert "--director-staffing-v2" not in text
        assert text.splitlines() == generator.director_launcher_body(school).splitlines()
        assert "setup_director_staffing.ps1" not in text
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


def test_director_entrypoint_uses_full_app_modules_not_separate_gui() -> None:
    assert not (ROOT / "src" / "staffing_dashboard_app.py").exists()
    assert not (ROOT / "contracts" / "staffing_dashboard_app.contract.yaml").exists()
    assert not (ROOT / "setup_director_staffing.ps1").exists()
    assert not (ROOT / "contracts" / "setup_director_staffing.contract.yaml").exists()
    assert not (ROOT / "requirements-director.txt").exists()


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
