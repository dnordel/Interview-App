from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_app_import_defers_onboarding_pdf_engines_until_document_action() -> None:
    src_dir = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; import pyside_interview_app; "
                "print(json.dumps({"
                "'pypdf': 'pypdf' in sys.modules, "
                "'reportlab': 'reportlab' in sys.modules"
                "}))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    loaded = json.loads(result.stdout.strip())
    assert loaded == {"pypdf": False, "reportlab": False}
