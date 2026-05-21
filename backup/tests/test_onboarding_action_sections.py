from onboarding_action_sections import onboarding_action_sections


def test_onboarding_actions_clustered_by_intent_in_expected_order():
    sections = onboarding_action_sections()

    assert [section.title for section in sections] == [
        "Daily workflow",
        "Candidate management",
        "Communications",
        "Admin & advanced",
    ]


def test_daily_workflow_primary_action_is_promoted_and_discoverable():
    daily_section = onboarding_action_sections()[0]
    primary_action = daily_section.actions[0]

    assert primary_action.label == "Run Reminders Now"
    assert primary_action.emphasis == "primary"
    assert primary_action.command_name == "_on_primary_reminder_cta_click"
    assert "Enter/Space" in primary_action.shortcut_hint


def test_ambiguous_actions_include_helper_labels():
    sections = onboarding_action_sections()
    all_actions = [action for section in sections for action in section.actions]

    dry_run = next(action for action in all_actions if action.label == "Run Reminders (Dry Run)")
    storage = next(action for action in all_actions if action.label == "Use Dropbox Folder")

    assert dry_run.helper_text
    assert storage.helper_text
