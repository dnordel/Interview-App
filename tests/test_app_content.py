from __future__ import annotations

import re

import platform_services


def test_app_content_helpers_preserve_behavior():
    assert platform_services.sanitize_filename(' A/B:*?"<>|  Name ') == "A_B_ Name"
    assert platform_services.sanitize_filename("   ") == "Unknown"
    assert platform_services.is_valid_date_yyyy_mm_dd("2026-06-16")
    assert not platform_services.is_valid_date_yyyy_mm_dd("06/16/2026")
    assert platform_services.text_suggests_no_example("That has never happened to me") is False
    assert platform_services.text_suggests_no_example("that's never happened to me") is True

    script = platform_services.compose_intro_script("Palmdale")
    assert "Palmdale is open weekdays from 5:30 AM to 7 PM" in script
    assert re.match(r"\d{8}_\d{6}", platform_services.now_stamp())
    assert platform_services.USER_ARTIFACTS_DIR == platform_services.REPO_ROOT / "user_artifacts"
    assert platform_services.DEFAULT_BASE_DIR == platform_services.USER_ARTIFACTS_DIR / "interviews"
    assert platform_services.INTERVIEW_HISTORY_PATH == platform_services.USER_ARTIFACTS_DIR / "interview_history.sqlite3"
    assert platform_services.INTERVIEW_HISTORY_DB_PATH == platform_services.INTERVIEW_HISTORY_PATH
    assert platform_services.INTERVIEW_HISTORY_LEGACY_JSON_PATH == platform_services.USER_ARTIFACTS_DIR / "interview_history.json"
