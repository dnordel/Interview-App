from src.ui_windows import SettingsWindow


def test_settings_window_uses_task_oriented_tab_constants() -> None:
    assert SettingsWindow._TAB_GENERAL == "general"
    assert SettingsWindow._TAB_TEMPLATES == "templates"
    assert SettingsWindow._TAB_NOTIFICATIONS == "notifications"
    assert SettingsWindow._TAB_STORAGE == "storage"
    assert SettingsWindow._TAB_SECURITY == "security"
