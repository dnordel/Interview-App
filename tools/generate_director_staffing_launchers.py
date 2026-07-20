from __future__ import annotations

import json
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED_PATH = ROOT / "config" / "staffing_seed.json"


def load_staffing_schools(seed_path: Path = DEFAULT_SEED_PATH) -> list[str]:
    payload = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    schools = payload.get("schools", [])
    if not isinstance(schools, list):
        raise ValueError("Staffing seed must contain a schools list.")
    names: list[str] = []
    for school in schools:
        if not isinstance(school, dict):
            raise ValueError("Staffing seed school entries must be objects.")
        name = str(school.get("name", "") or "").strip()
        if not name:
            raise ValueError("Staffing seed school name is required.")
        names.append(name)
    return names


def director_launcher_filename(school: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*]+', " ", str(school or "").strip())
    clean = re.sub(r"\s+", " ", clean).strip().rstrip(".")
    if not clean:
        raise ValueError("School name is required.")
    return f"..START DIRECTOR STAFFING - {clean}.bat"


def director_vbs_launcher_filename(school: str) -> str:
    return director_launcher_filename(school).removesuffix(".bat") + ".vbs"


def director_launcher_body(school: str) -> str:
    vbs_name = director_vbs_launcher_filename(school).replace('"', '""')
    lines = [
        "@echo off",
        "setlocal",
        "",
        'cd /d "%~dp0"',
        "",
        f'wscript.exe "%CD%\\{vbs_name}"',
        "",
        "exit /b",
        "",
    ]
    return "\r\n".join(lines)


def director_vbs_launcher_body(school: str) -> str:
    escaped_school = re.sub(r"\s+", " ", str(school or "")).strip().replace('"', '""')
    lines = [
        'Set shell = CreateObject("WScript.Shell")',
        'Set fso = CreateObject("Scripting.FileSystemObject")',
        "appDir = fso.GetParentFolderName(WScript.ScriptFullName)",
        "shell.CurrentDirectory = appDir",
        'If shell.AppActivate("Director Staffing Dashboard") Then',
        "    WScript.Sleep 100",
        '    shell.SendKeys "% x"',
        "    WScript.Quit 0",
        "End If",
        (
            'command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " '
            '& Chr(34) & appDir & "\\setup_director_staffing.ps1" & Chr(34) '
            f'& " -DirectorSchool " & Chr(34) & "{escaped_school}" & Chr(34)'
        ),
        "shell.Run command, 0, False",
        "",
    ]
    return "\r\n".join(lines)


def generate_director_launchers(
    *,
    root: Path = ROOT,
    seed_path: Path = DEFAULT_SEED_PATH,
) -> list[Path]:
    output_paths: list[Path] = []
    for school in load_staffing_schools(seed_path):
        bat_path = Path(root) / director_launcher_filename(school)
        vbs_path = Path(root) / director_vbs_launcher_filename(school)
        bat_path.write_text(director_launcher_body(school), encoding="utf-8", newline="")
        vbs_path.write_text(director_vbs_launcher_body(school), encoding="utf-8", newline="")
        output_paths.extend([bat_path, vbs_path])
    return output_paths


def main() -> int:
    generate_director_launchers()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
