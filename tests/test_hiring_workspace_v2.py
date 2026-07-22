import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_store import InterviewHistoryStore
from hiring_pipeline import HiringPipelineStore, HiringWorkflowService
from visual_test_support import VisualTestDatabaseRegistry, configure_visual_test_app


@pytest.mark.pyside_gui
def test_shared_candidate_editors_capture_offer_ready_identity_and_qualification() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from pyside_interview_components import CandidateIdentityEditor, CandidateQualificationEditor

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    identity = CandidateIdentityEditor(
        QtWidgets=QtWidgets,
        object_prefix="TestOffer",
        school_options=["Palmdale"],
        position_options=[("teacher", "Teacher")],
        include_contact=True,
        email_required=True,
    )
    identity.set_values(
        {
            "candidate_name": "Maya Patel",
            "honorific": "Ms.",
            "candidate_email": "maya@example.com",
            "candidate_phone": "310.555.0199",
            "school": "Palmdale",
            "position_id": "teacher",
        }
    )
    qualification = CandidateQualificationEditor(
        QtWidgets=QtWidgets,
        object_prefix="TestOffer",
        values={
            "has_degree": False,
            "ece_units_completed": "24.5",
            "infant_toddler_class_completed": True,
            "total_units_completed": "40",
            "years_experience": 3,
        },
    )

    assert identity.validated_values() == {
        "candidate_name": "Maya Patel",
        "honorific": "Ms.",
        "candidate_email": "maya@example.com",
        "candidate_phone": "(310) 555-0199",
        "school": "Palmdale",
        "position_id": "teacher",
    }
    assert qualification.validated_values() == {
        "has_degree": False,
        "degree_type": "",
        "degree_in_ece": False,
        "ece_units_completed": "24.5",
        "infant_toddler_class_completed": True,
        "total_units_completed": 40,
        "years_experience": 3,
    }
    identity.widget.close()
    qualification.widget.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_shared_candidate_editors_fail_closed_on_invalid_offer_data() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from pyside_interview_components import CandidateIdentityEditor, CandidateQualificationEditor

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    identity = CandidateIdentityEditor(
        QtWidgets=QtWidgets,
        object_prefix="InvalidOffer",
        school_options=["Palmdale"],
        position_options=[("teacher", "Teacher")],
        include_contact=True,
        email_required=True,
    )
    identity.set_values(
        {
            "candidate_name": "Maya Patel",
            "candidate_email": "not-an-email",
            "school": "Palmdale",
            "position_id": "teacher",
        }
    )
    qualification = CandidateQualificationEditor(
        QtWidgets=QtWidgets,
        object_prefix="InvalidOffer",
        values={"has_degree": False, "ece_units_completed": "bad", "years_experience": 3},
    )

    with pytest.raises(ValueError, match="valid candidate email"):
        identity.validated_values()
    with pytest.raises(ValueError, match="ECE units completed"):
        qualification.validated_values()
    identity.widget.close()
    qualification.widget.close()
    app.processEvents()


def test_hiring_workspace_uses_unified_pipeline_without_horizontal_page_scroll(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtTest, QtWidgets
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-ui",
            "candidate_name": "Maya Patel",
            "school": "Palmdale",
            "position": "Preschool",
            "score": 70,
            "outcome": "Hire",
        }
    )
    service = HiringWorkflowService(HiringPipelineStore(history_path))
    service.backfill_history()

    page = HiringWorkspaceV2Page(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        service=service,
    )
    page.widget.resize(1100, 700)
    page.widget.show()
    app.processEvents()

    assert len(page.stage_buttons) == 7
    assert page.application_table.columnCount() == 5
    assert page.application_table.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    candidate_rect = page.application_table.visualItemRect(page.application_table.item(0, 0))
    QtTest.QTest.mouseClick(
        page.application_table.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        pos=candidate_rect.center(),
    )
    app.processEvents()
    assert page.detail_next_action.text() == "Schedule director review"
    assert page.timeline_list.count() == 1

    page.widget.close()


def test_hiring_workspace_opens_with_application_detail_closed(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    from hiring_pipeline import HiringApplication, HiringStage
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "detail-closed.sqlite3"))
    service.start_application(
        legal_name="Maya Patel",
        email="maya@example.com",
        phone="",
        school="Palmdale",
        position="Preschool",
        actor="Admin",
    )
    page = HiringWorkspaceV2Page(QtCore=QtCore, QtWidgets=QtWidgets, service=service)
    page.widget.resize(1100, 700)
    page.widget.show()
    app.processEvents()

    assert page.application_table.currentRow() == -1
    assert not page.detail_overlay.frame.isVisible()

    page.widget.close()


def test_hiring_workspace_candidate_detail_opens_at_right_and_can_be_closed(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtTest, QtWidgets
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "detail-close.sqlite3"))
    service.start_application(
        legal_name="Maya Patel",
        email="maya@example.com",
        phone="",
        school="Palmdale",
        position="Preschool",
        actor="Admin",
    )
    page = HiringWorkspaceV2Page(QtCore=QtCore, QtWidgets=QtWidgets, service=service)
    page.widget.resize(1100, 700)
    page.widget.show()
    app.processEvents()

    candidate_item = page.application_table.item(0, 0)
    candidate_rect = page.application_table.visualItemRect(candidate_item)
    QtTest.QTest.mouseClick(
        page.application_table.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        pos=candidate_rect.center(),
    )
    app.processEvents()

    assert page.detail_overlay.frame.isVisible()
    assert page.detail_overlay.frame.geometry().right() == page.widget.rect().right()
    close_button = page.widget.findChild(QtWidgets.QPushButton, "HiringV2ApplicationDetailClose")
    assert close_button is not None
    assert close_button.isVisible()

    QtTest.QTest.mouseClick(close_button, QtCore.Qt.MouseButton.LeftButton)
    QtTest.QTest.qWait(1)
    app.processEvents()

    assert not page.detail_overlay.frame.isVisible()

    page.widget.close()


def test_hiring_workspace_exposes_native_interviews_candidates_and_offers_views(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtTest, QtWidgets
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "native-views.sqlite3"))
    application = service.start_application(
        legal_name="Maya Patel",
        email="maya@example.com",
        phone="555-0100",
        school="Palmdale",
        position="assistant_director_enrollment_specialist",
        actor="Admin",
    )
    page = HiringWorkspaceV2Page(QtCore=QtCore, QtWidgets=QtWidgets, service=service)
    page.interviews_widget.resize(1100, 700)
    page.interviews_widget.show()
    app.processEvents()

    assert page.widget is page.interviews_widget
    assert page.candidates_widget.objectName() == "HiringV2CandidatesPage"
    assert page.offers_widget.objectName() == "HiringV2OffersPage"
    assert page.interview_metrics["active"].text() == "1"
    assert page.candidates_table.rowCount() == 1
    assert [
        page.candidates_table.horizontalHeaderItem(column).text()
        for column in range(page.candidates_table.columnCount())
    ] == ["Candidate", "Contact", "Role", "Latest school", "Current stage", "Last activity"]
    assert page.candidates_table.item(0, 2).text() == "Assistant Director"
    role_badge = page.candidates_table.cellWidget(0, 2)
    assert role_badge.objectName() == "HiringV2RoleBadge"
    assert role_badge.accessibleName() == "Role: Assistant Director"
    assert role_badge.property("roleKind") == "director"
    assert not role_badge.findChild(QtWidgets.QLabel, "HiringV2RoleBadgeIcon").pixmap().isNull()
    assert page.candidates_table.item(0, 3).text() == "Palmdale"
    assert page.position_filter.itemText(1) == "Assistant Director"
    assert page.application_table.item(0, 1).text() == "Palmdale\nAssistant Director"
    candidate_rect = page.application_table.visualItemRect(page.application_table.item(0, 0))
    QtTest.QTest.mouseClick(
        page.application_table.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        pos=candidate_rect.center(),
    )
    app.processEvents()
    assert "Palmdale · Assistant Director" in page.detail_candidate.text()
    assert page.offers_table.rowCount() == 0
    assert page.application_table.item(0, 0).data(QtCore.Qt.ItemDataRole.UserRole) == application.application_id
    assert page.detail_overlay.frame.isVisible()

    page.interviews_widget.close()


def test_hiring_workspace_create_offer_action_submits_selected_version(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    from hiring_pipeline import HiringStage
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    history_path = tmp_path / "history.sqlite3"
    service = HiringWorkflowService(HiringPipelineStore(history_path))
    application = service.start_application(
        legal_name="Maya Patel",
        email="maya@example.com",
        phone="",
        school="Palmdale",
        position="Preschool",
        actor="Admin",
    )
    service.finalize_initial_interview(
        application.application_id,
        history_id="hist-ui-offer",
        score=80,
        outcome="Hire",
        actor="Admin",
    )
    service.record_director_decision(application.application_id, decision="Hire", actor="Director")
    page = HiringWorkspaceV2Page(QtCore=QtCore, QtWidgets=QtWidgets, service=service)

    submitted = page.perform_action(
        "create_offer",
        service.store.get_application(application.application_id),
        values={
            "position_id": "teacher",
            "qualification": {
                "has_degree": False,
                "degree_type": "",
                "degree_in_ece": False,
                "ece_units_completed": "12",
                "total_units_completed": "12",
                "years_experience": 7,
            },
            "weekly_hours": "40",
            "template_path": "offer.docx",
            "output_dir": "offers",
        },
    )

    assert submitted.status == "pending_approval"
    assert submitted.terms["hourly_pay"] == "20.00"
    assert service.store.get_application(application.application_id).stage is HiringStage.EXECUTIVE_APPROVAL
    page.widget.close()
    app.processEvents()


def test_hiring_workspace_generate_offer_button_creates_external_offer(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    from hiring_pipeline import HiringApplication, HiringStage
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "external-offer.sqlite3"))
    page = HiringWorkspaceV2Page(QtCore=QtCore, QtWidgets=QtWidgets, service=service)
    page.offers_widget.show()
    app.processEvents()

    button = page.offers_widget.findChild(QtWidgets.QPushButton, "HiringV2PrimaryAction")
    submitted = page.perform_action(
        "create_external_offer",
        HiringApplication(
            application_id="",
            candidate_id="",
            history_id="",
            school="Palmdale",
            position="Preschool",
            cycle_number=0,
            stage=HiringStage.OFFER_DRAFT,
        ),
        values={
            "candidate_name": "External Candidate",
            "candidate_email": "external@example.com",
            "candidate_phone": "310-555-0100",
            "honorific": "Ms.",
            "school": "Palmdale",
            "position_id": "teacher",
            "qualification": {
                "has_degree": False,
                "degree_type": "",
                "degree_in_ece": False,
                "ece_units_completed": "24",
                "total_units_completed": "40",
                "years_experience": 3,
            },
            "weekly_hours": "40",
            "template_path": "offer.docx",
            "output_dir": "offers",
        },
    )

    assert button is not None
    assert button.text() == "+ Generate offer"
    assert submitted.status == "pending_approval"
    assert submitted.terms["hourly_pay"] == "19.00"
    assert page.offers_table.rowCount() == 1
    assert page.offers_table.item(0, 0).text() == "External Candidate"
    assert page.offers_table.item(0, 5).text() == "Pending Approval"
    assert "Approval 1" in page.offers_status.text()
    page.offers_widget.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_generate_offer_dialog_uses_shared_capture_and_automatic_offer_terms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    import hiring_workspace_v2
    from data_store import SchoolOfferSettingsStore
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    template = tmp_path / "full-time-offer.docx"
    template.write_bytes(b"template")
    output_dir = tmp_path / "offers"
    settings_path = tmp_path / "school-offer-settings.json"
    SchoolOfferSettingsStore(settings_path).save(
        {
            "Palmdale": {
                "full_time_template": str(template),
                "part_time_template": str(tmp_path / "part-time-offer.docx"),
                "offer_output_dir": str(output_dir),
            }
        }
    )
    monkeypatch.setattr(hiring_workspace_v2, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(hiring_workspace_v2, "DEFAULT_BASE_DIR", tmp_path)
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "offer-dialog.sqlite3"))
    page = HiringWorkspaceV2Page(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        service=service,
        school_options=["Palmdale"],
    )

    original_exec = QtWidgets.QDialog.exec

    def complete_dialog(dialog):
        dialog.findChild(QtWidgets.QLineEdit, "HiringV2OfferCandidateName").setText("New Candidate")
        dialog.findChild(QtWidgets.QLineEdit, "HiringV2OfferEmail").setText("new@example.com")
        school = dialog.findChild(QtWidgets.QComboBox, "HiringV2OfferSchool")
        school.setCurrentIndex(school.findData("Palmdale"))
        position = dialog.findChild(QtWidgets.QComboBox, "HiringV2OfferPosition")
        position.setCurrentIndex(position.findData("teacher"))
        has_degree = dialog.findChild(QtWidgets.QComboBox, "HiringV2OfferHasDegree")
        has_degree.setCurrentText("No")
        dialog.findChild(QtWidgets.QLineEdit, "HiringV2OfferEceUnits").setText("24")
        dialog.findChild(QtWidgets.QLineEdit, "HiringV2OfferTotalUnits").setText("40")
        dialog.findChild(QtWidgets.QLineEdit, "HiringV2OfferYearsExperience").setText("3")
        submit = next(
            button
            for button in dialog.findChildren(QtWidgets.QPushButton)
            if button.text() == "Submit for approval"
        )
        submit.click()
        return dialog.result()

    monkeypatch.setattr(QtWidgets.QDialog, "exec", complete_dialog)
    values = page._external_offer_editor_values()
    monkeypatch.setattr(QtWidgets.QDialog, "exec", original_exec)

    assert values is not None
    assert values["weekly_hours"] == "40"
    assert values["gross_daily_hours"] == "9"
    assert values["net_daily_hours"] == "8"
    assert values["employment_type"] == "full_time"
    assert values["template_path"] == str(template)
    assert values["output_dir"] == str(output_dir)
    assert values["qualification"]["infant_toddler_class_completed"] is False
    assert "start_date" not in values
    page.widget.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_generate_offer_reuses_existing_candidate_and_updates_profile(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    from hiring_pipeline import HiringApplication, HiringStage
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "existing-offer-ui.sqlite3"))
    prior = service.start_application(
        legal_name="Maya Patel",
        email="old@example.com",
        phone="",
        school="Palmdale",
        position="Preschool",
        actor="Admin",
    )
    page = HiringWorkspaceV2Page(QtCore=QtCore, QtWidgets=QtWidgets, service=service)

    offer = page.perform_action(
        "create_external_offer",
        HiringApplication(
            application_id="",
            candidate_id="",
            history_id="",
            school="Hawthorne",
            position="Teacher",
            cycle_number=0,
            stage=HiringStage.OFFER_DRAFT,
        ),
        values={
            "candidate_id": prior.candidate_id,
            "candidate_name": "Maya Patel-Smith",
            "candidate_email": "maya@example.com",
            "candidate_phone": "310.555.0199",
            "honorific": "Mr.",
            "school": "Hawthorne",
            "position_id": "teacher",
            "qualification": {
                "has_degree": False,
                "degree_type": "",
                "degree_in_ece": False,
                "ece_units_completed": "24",
                "infant_toddler_class_completed": True,
                "total_units_completed": "40",
                "years_experience": 3,
            },
            "weekly_hours": "40",
            "template_path": "offer.docx",
            "output_dir": "offers",
        },
    )

    created = service.store.get_application(offer.application_id)
    candidate = service.store.get_candidate(prior.candidate_id)
    assert created.candidate_id == prior.candidate_id
    assert created.school == "Hawthorne"
    assert candidate.legal_name == "Maya Patel-Smith"
    assert candidate.email == "maya@example.com"
    assert candidate.phone == "(310) 555-0199"
    assert candidate.honorific == "Mr."
    assert service.store.get_application(prior.application_id).stage is HiringStage.INITIAL_INTERVIEW
    page.widget.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_existing_candidate_offer_prefers_visible_report_qualification(tmp_path: Path) -> None:
    import sqlite3

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    from candidate_report import CandidateReportRepository
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    history_path = tmp_path / "qualification-prefill.sqlite3"
    service = HiringWorkflowService(HiringPipelineStore(history_path))
    application = service.start_application(
        legal_name="Maya Patel",
        email="maya@example.com",
        phone="",
        school="Palmdale",
        position="Teacher",
        actor="Admin",
    )
    repository = CandidateReportRepository(history_path)
    repository.initialize()
    expected = {
        "has_degree": True,
        "degree_type": "BA",
        "degree_in_ece": True,
        "ece_units_completed": None,
        "infant_toddler_class_completed": True,
        "total_units_completed": None,
        "years_experience": 6,
    }
    with sqlite3.connect(repository.db_path) as conn:
        CandidateReportRepository.insert_initial_on_connection(
            conn,
            application.history_id,
            {"candidate": {"qualification": expected}},
            actor="Admin",
        )
        conn.commit()
    page = HiringWorkspaceV2Page(QtCore=QtCore, QtWidgets=QtWidgets, service=service)

    prefill = page._external_offer_candidate_prefill(application.candidate_id)

    assert prefill["qualification"] == expected
    assert prefill["school"] == "Palmdale"
    assert prefill["position_id"] == "teacher"
    page.widget.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_generate_offer_for_existing_candidate_preserves_review_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    from candidate_report import CandidateReportRepository
    from data_store import SchoolOfferSettingsStore
    import hiring_workspace_v2
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    template = tmp_path / "full-time-offer.docx"
    template.write_bytes(b"template")
    settings_path = tmp_path / "school-offer-settings.json"
    SchoolOfferSettingsStore(settings_path).save(
        {
            "Palmdale": {
                "full_time_template": str(template),
                "part_time_template": str(tmp_path / "part-time-offer.docx"),
                "offer_output_dir": str(tmp_path / "offers"),
            }
        }
    )
    monkeypatch.setattr(hiring_workspace_v2, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(hiring_workspace_v2, "DEFAULT_BASE_DIR", tmp_path)

    history_path = tmp_path / "existing-candidate-review.sqlite3"
    service = HiringWorkflowService(HiringPipelineStore(history_path))
    prior = service.start_application(
        legal_name="Maya Patel",
        email="maya@example.com",
        phone="",
        school="Palmdale",
        position="Teacher",
        actor="Admin",
    )
    service.finalize_initial_interview(
        prior.application_id,
        history_id="hist-existing-review",
        score=88,
        outcome="Hire",
        actor="Admin",
    )
    service.record_director_decision(prior.application_id, decision="Hire", actor="Director")
    qualification = {
        "has_degree": True,
        "degree_type": "BA",
        "degree_in_ece": True,
        "ece_units_completed": None,
        "infant_toddler_class_completed": True,
        "total_units_completed": None,
        "years_experience": 6,
    }
    repository = CandidateReportRepository(history_path)
    repository.initialize()
    with sqlite3.connect(repository.db_path) as conn:
        CandidateReportRepository.insert_initial_on_connection(
            conn,
            "hist-existing-review",
            {
                "candidate": {"qualification": qualification},
                "scoring": {"percent_of_max": 88},
                "questions": [
                    {"question_id": "Pay", "interviewer_notes": "$24.50 per hour"}
                ],
            },
            actor="Admin",
        )
        conn.commit()
    service.create_offer_draft(
        prior.application_id,
        terms={
            "qualification_snapshot": qualification,
            "director_rating": "4.5",
            "requested_pay_raw": "$24.50 per hour",
            "proposed_classroom": "Chef",
        },
        actor="Admin",
    )
    page = HiringWorkspaceV2Page(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        service=service,
        school_options=["Palmdale"],
        actions={
            "candidate_offer_prefill": lambda _application: {
                "director_rating": "4.75",
                "proposed_classroom": "Harmony 1",
                "start_time": "07:30 AM",
                "end_time": "04:30 PM",
                "position_id": "teacher_floater",
            }
        },
    )

    def complete_dialog(dialog):
        picker = dialog.findChild(QtWidgets.QComboBox, "HiringV2OfferCandidatePicker")
        index = next(
            index
            for index in range(picker.count())
            if picker.itemData(index) == prior.candidate_id
        )
        picker.setCurrentIndex(index)
        submit = next(
            button
            for button in dialog.findChildren(QtWidgets.QPushButton)
            if button.text() == "Submit for approval"
        )
        submit.click()
        return dialog.result()

    monkeypatch.setattr(QtWidgets.QDialog, "exec", complete_dialog)
    values = page._external_offer_editor_values()
    assert values is not None
    assert values["start_time"] == "07:30 AM"
    assert values["end_time"] == "04:30 PM"
    assert values["position_id"] == "teacher_floater"
    submitted = page.perform_action(
        "create_external_offer",
        prior,
        values=values,
    )

    assert submitted.terms["initial_interview_score"] == 88
    assert submitted.terms["director_rating"] == "4.75"
    assert submitted.terms["requested_pay_raw"] == "$24.50 per hour"
    assert submitted.terms["proposed_classroom"] == "Harmony 1"
    assert submitted.terms["position_id"] == "teacher_floater"
    assert submitted.terms["position"] == "Teacher/Floater"
    page.widget.close()
    app.processEvents()


def test_pending_offer_row_has_direct_review_offer_button_beside_menu(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtTest, QtWidgets
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "review-offer.sqlite3"))
    application = service.start_application(
        legal_name="Maya Patel",
        email="maya@example.com",
        phone="",
        school="Palmdale",
        position="Preschool",
        actor="Admin",
    )
    service.finalize_initial_interview(
        application.application_id,
        history_id="hist-review-offer",
        score=80,
        outcome="Hire",
        actor="Admin",
    )
    service.record_director_decision(application.application_id, decision="Hire", actor="Director")
    draft = service.create_offer_draft(
        application.application_id,
        terms={
            "hourly_pay": "24.00",
            "weekly_hours": "40",
            "template_path": "offer.docx",
            "output_dir": "offers",
        },
        actor="Admin",
    )
    service.submit_offer_for_approval(application.application_id, draft.version_id, actor="Admin")
    reviewed: list[str] = []
    page = HiringWorkspaceV2Page(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        service=service,
        actions={"review_approval": lambda item: reviewed.append(item.application_id)},
    )
    page.offers_widget.show()
    app.processEvents()

    actions = page.offers_table.cellWidget(0, 6)
    review_button = actions.findChild(QtWidgets.QPushButton, "HiringV2ReviewOfferAction")
    menu_button = actions.findChild(QtWidgets.QToolButton, "HiringV2OfferOverflowAction")

    assert review_button is not None
    assert review_button.text() == "Review offer"
    assert menu_button is not None
    assert menu_button.text() == "•••"
    QtTest.QTest.mouseClick(review_button, QtCore.Qt.MouseButton.LeftButton)
    app.processEvents()
    assert reviewed == [application.application_id]

    page.offers_widget.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_offers_page_splits_pending_and_approved_tables_and_sizes_pending_rows(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "split-offers.sqlite3"))

    def submitted_offer(name: str, email: str):
        application = service.start_external_offer_application(
            legal_name=name,
            email=email,
            phone="",
            school="Palmdale",
            position="Preschool",
            actor="Admin",
        )
        draft = service.create_offer_draft(
            application.application_id,
            terms={"hourly_pay": "24.00", "weekly_hours": "40"},
            actor="Admin",
        )
        submitted = service.submit_offer_for_approval(
            application.application_id, draft.version_id, actor="Admin"
        )
        return application, submitted

    submitted_offer("Pending Candidate", "pending@example.com")
    approved_application, approved_version = submitted_offer("Approved Candidate", "")
    docx_path = tmp_path / "approved.docx"
    pdf_path = tmp_path / "approved.pdf"
    docx_path.write_bytes(b"docx")
    pdf_path.write_bytes(b"pdf")
    service.approve_offer(
        approved_application.application_id,
        approved_version.version_id,
        approver_name="Executive",
        approver_role="Executive Director",
        approval_date=date(2026, 7, 14),
        docx_path=docx_path,
        pdf_path=pdf_path,
    )

    page = HiringWorkspaceV2Page(QtCore=QtCore, QtWidgets=QtWidgets, service=service)
    page.offers_widget.show()
    app.processEvents()

    assert page.pending_offers_table is page.offers_table
    assert page.pending_offers_table.rowCount() == 1
    assert page.pending_offers_table.item(0, 0).text() == "Pending Candidate"
    assert page.approved_offers_table.rowCount() == 1
    assert page.approved_offers_table.item(0, 0).text() == "Approved Candidate"
    assert (
        page.pending_offers_table.verticalScrollBarPolicy()
        == QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    expected_height = (
        page.pending_offers_table.horizontalHeader().height()
        + page.pending_offers_table.rowHeight(0)
        + 2 * page.pending_offers_table.frameWidth()
    )
    assert page.pending_offers_table.height() == expected_height

    page.offers_widget.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_approved_offer_without_email_has_send_offer_button(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtTest, QtWidgets
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "send-button.sqlite3"))
    application = service.start_external_offer_application(
        legal_name="Approved Candidate",
        email="",
        phone="",
        school="Palmdale",
        position="Preschool",
        actor="Admin",
    )
    draft = service.create_offer_draft(
        application.application_id,
        terms={"hourly_pay": "24.00", "weekly_hours": "40"},
        actor="Admin",
    )
    submitted = service.submit_offer_for_approval(
        application.application_id, draft.version_id, actor="Admin"
    )
    docx_path = tmp_path / "approved.docx"
    pdf_path = tmp_path / "approved.pdf"
    docx_path.write_bytes(b"docx")
    pdf_path.write_bytes(b"pdf")
    approved = service.approve_offer(
        application.application_id,
        submitted.version_id,
        approver_name="Executive",
        approver_role="Executive Director",
        approval_date=date(2026, 7, 14),
        docx_path=docx_path,
        pdf_path=pdf_path,
    )
    requested: list[tuple[str, str]] = []
    page = HiringWorkspaceV2Page(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        service=service,
        actions={
            "send_offer": lambda selected_application, version: requested.append(
                (selected_application.application_id, version.version_id)
            )
        },
    )
    page.offers_widget.show()
    app.processEvents()

    actions = page.approved_offers_table.cellWidget(0, 6)
    send_button = actions.findChild(QtWidgets.QPushButton, "HiringV2SendOfferAction")
    assert send_button is not None
    assert send_button.text() == "Send offer"
    QtTest.QTest.mouseClick(send_button, QtCore.Qt.MouseButton.LeftButton)
    app.processEvents()
    assert requested == [(application.application_id, approved.version_id)]

    page.offers_widget.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_offer_overflow_menu_can_delete_selected_offer_version(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "delete-menu.sqlite3"))
    application = service.start_external_offer_application(
        legal_name="Pending Candidate",
        email="pending@example.com",
        phone="",
        school="Palmdale",
        position="Preschool",
        actor="Admin",
    )
    draft = service.create_offer_draft(
        application.application_id,
        terms={"hourly_pay": "24.00", "weekly_hours": "40"},
        actor="Admin",
    )
    submitted = service.submit_offer_for_approval(
        application.application_id, draft.version_id, actor="Admin"
    )
    invoked: list[tuple[str, str, str]] = []
    page = HiringWorkspaceV2Page(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        service=service,
        actions={
            "review_approval": lambda selected_application: invoked.append(
                ("review", selected_application.application_id, submitted.version_id)
            ),
            "delete_offer": lambda selected_application, version: invoked.append(
                ("delete", selected_application.application_id, version.version_id)
            ),
            "archive": lambda selected_application: invoked.append(
                ("archive", selected_application.application_id, submitted.version_id)
            ),
        },
    )
    menu_button = page.pending_offers_table.cellWidget(0, 6).findChild(
        QtWidgets.QToolButton, "HiringV2OfferOverflowAction"
    )
    menu = menu_button.menu()
    review_action = next(action for action in menu.actions() if action.text() == "Review offer")
    delete_action = next(action for action in menu.actions() if action.text() == "Delete offer")
    archive_action = next(action for action in menu.actions() if action.text() == "Archive application")

    review_action.trigger()
    delete_action.trigger()
    archive_action.trigger()

    assert invoked == [
        ("review", application.application_id, submitted.version_id),
        ("delete", application.application_id, submitted.version_id),
        ("archive", application.application_id, submitted.version_id),
    ]


@pytest.mark.pyside_gui
@pytest.mark.visual_inspection
def test_hiring_workspace_visual_sizes_have_no_page_horizontal_scroll(
    tmp_path: Path,
    visual_test_databases: VisualTestDatabaseRegistry,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtWidgets
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    configure_visual_test_app(app)
    pipeline_path = visual_test_databases.database("hiring_pipeline.sqlite3")
    visual_test_databases.expect_seeded(pipeline_path, table="hiring_applications")
    service = HiringWorkflowService(HiringPipelineStore(pipeline_path))
    service.start_application(
        legal_name="Ghjypq Avery",
        email="avery@example.test",
        phone="555-0100",
        school="Hawthorne",
        position="Infant/Toddler Teacher",
        actor="Visual Test",
    )
    page = HiringWorkspaceV2Page(QtCore=QtCore, QtWidgets=QtWidgets, service=service)
    original_font = app.font()
    for width, height, scale in (
        (1366, 768, 1.0),
        (1600, 900, 1.0),
        (1366, 768, 1.25),
        (1366, 768, 1.5),
    ):
        font = QtGui.QFont(original_font)
        font.setPointSizeF(max(8.0, original_font.pointSizeF() * scale))
        app.setFont(font)
        page.widget.resize(width, height)
        page.widget.show()
        app.processEvents()
        screenshot = page.widget.grab()
        output = tmp_path / f"hiring-{width}x{height}-{scale}.png"
        assert screenshot.save(str(output))
        assert screenshot.width() == width
        assert page.application_table.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    app.setFont(original_font)
    page.widget.close()


def test_offer_approval_dialog_embeds_pdf_and_gates_approval(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtPdf, QtPdfWidgets, QtWidgets
    from hiring_workspace_v2 import HiringOfferApprovalDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pdf_path = tmp_path / "approved-offer.pdf"
    writer = QtGui.QPdfWriter(str(pdf_path))
    painter = QtGui.QPainter(writer)
    painter.drawText(100, 100, "Exact approved offer")
    painter.end()

    approval = HiringOfferApprovalDialog(
        QtCore=QtCore,
        QtPdf=QtPdf,
        QtPdfWidgets=QtPdfWidgets,
        QtWidgets=QtWidgets,
        parent=None,
        title="Approve offer v1",
        summary="Candidate and terms",
        rendered_email="Rendered candidate email",
        pdf_path=pdf_path,
        hourly_pay="24.00",
        approve_label="Approve",
    )
    approval.dialog.show()
    for _ in range(10):
        app.processEvents()
        if approval.pdf_document.pageCount() > 0:
            break

    assert approval.pdf_view.document() is approval.pdf_document
    assert approval.pdf_document.pageCount() == 1
    assert approval.approve_button.isEnabled() is False
    approval.approver_input.setText("Executive Approver")
    app.processEvents()
    assert approval.approve_button.isEnabled() is True
    assert approval.approver_name() == "Executive Approver"
    assert approval.hourly_pay() == "24.00"
    assert approval.change_pay_button.isEnabled() is False
    approval.pay_input.setText("25.50")
    app.processEvents()
    assert approval.approve_button.text() == "Approve"
    assert approval.change_pay_button.text() == "Change Pay & Approve"
    assert approval.change_pay_button.isEnabled() is True
    assert approval.hourly_pay() == "25.50"
    approval.change_pay_button.click()
    assert approval.change_pay_requested() is True
    approval.close()


def test_offer_approval_dialog_requests_degree_in_ece_correction(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtPdf, QtPdfWidgets, QtWidgets
    from hiring_workspace_v2 import HiringOfferApprovalDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pdf_path = tmp_path / "approved-offer.pdf"
    writer = QtGui.QPdfWriter(str(pdf_path))
    painter = QtGui.QPainter(writer)
    painter.drawText(100, 100, "Exact approved offer")
    painter.end()

    approval = HiringOfferApprovalDialog(
        QtCore=QtCore,
        QtPdf=QtPdf,
        QtPdfWidgets=QtPdfWidgets,
        QtWidgets=QtWidgets,
        parent=None,
        title="Approve offer v1",
        summary="Candidate and terms",
        rendered_email="Rendered candidate email",
        pdf_path=pdf_path,
        hourly_pay="19.00",
        degree_in_ece=False,
        approve_label="Approve",
    )
    approval.dialog.show()
    for _ in range(10):
        app.processEvents()
        if approval.pdf_document.pageCount() > 0:
            break

    approval.approver_input.setText("Executive Approver")
    approval.degree_in_ece_input.setChecked(True)
    app.processEvents()

    assert approval.degree_in_ece() is True
    assert approval.correct_qualification_button.isEnabled() is True
    approval.correct_qualification_button.click()
    assert approval.qualification_change_requested() is True
    approval.close()


@pytest.mark.pyside_gui
def test_offer_approval_dialog_shows_nine_review_details_in_three_columns(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtPdf, QtPdfWidgets, QtWidgets
    from hiring_workspace_v2 import HiringOfferApprovalDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pdf_path = tmp_path / "approved-offer.pdf"
    writer = QtGui.QPdfWriter(str(pdf_path))
    painter = QtGui.QPainter(writer)
    painter.drawText(100, 100, "Exact approved offer")
    painter.end()
    expected = {
        "Name": "Maya Patel",
        "Initial Interview Score": "88%",
        "Director Rating": "4.5",
        "Degree": "BA",
        "Years of Experience": "6",
        "Requested Pay": "$24.50 per hour",
        "Offer Amount": "$22.50 per hour",
        "Classroom": "Chef",
        "Hours": "40 weekly",
    }

    approval = HiringOfferApprovalDialog(
        QtCore=QtCore,
        QtPdf=QtPdf,
        QtPdfWidgets=QtPdfWidgets,
        QtWidgets=QtWidgets,
        parent=None,
        title="Approve offer v1",
        summary="",
        review_details=expected,
        rendered_email="Rendered candidate email",
        pdf_path=pdf_path,
        hourly_pay="22.50",
        approve_label="Approve",
    )

    details = approval.dialog.findChild(QtWidgets.QWidget, "HiringOfferReviewDetails")
    assert details is not None
    grid = details.layout()
    assert isinstance(grid, QtWidgets.QGridLayout)
    assert grid.columnCount() == 3
    visible_text = {label.text() for label in details.findChildren(QtWidgets.QLabel)}
    assert visible_text == {*expected.keys(), *expected.values()}
    approval.close()
    app.processEvents()


def test_hiring_interview_guide_routes_inside_hiring_workspace() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from hiring_workspace_v2 import HiringInterviewGuidePage

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pipeline = QtWidgets.QWidget()
    interview = QtWidgets.QWidget()
    closeout = QtWidgets.QWidget()
    router = HiringInterviewGuidePage(
        QtWidgets=QtWidgets,
        pipeline_widget=pipeline,
        interview_widget=interview,
        closeout_widget=closeout,
    )

    assert router.current_route == "pipeline"
    assert router.stack.currentWidget() is pipeline
    router.show_interview()
    assert router.current_route == "interview"
    assert router.stack.currentWidget() is interview
    router.show_closeout()
    assert router.current_route == "closeout"
    assert router.stack.currentWidget() is closeout
    router.show_pipeline()
    assert router.stack.currentWidget() is pipeline
    router.widget.close()
    app.processEvents()


def test_hiring_workspace_contextual_actions_complete_offer_lifecycle(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    from hiring_pipeline import HiringStage
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    delivery = {"succeeds": False}

    def send_offer(_candidate, _version, _pdf_path, _key):
        status = "sent" if delivery["succeeds"] else "failed"
        return [SimpleNamespace(status=status, error="redacted failure")]

    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "history.sqlite3"), send_offer=send_offer)
    application = service.start_application(
        legal_name="Maya Patel",
        email="maya@example.com",
        phone="",
        school="Palmdale",
        position="Preschool",
        actor="Admin",
    )
    service.finalize_initial_interview(
        application.application_id,
        history_id="hist-actions",
        score=80,
        outcome="Hire",
        actor="Admin",
    )
    service.record_director_decision(application.application_id, decision="Hire", actor="Director")
    page = HiringWorkspaceV2Page(QtCore=QtCore, QtWidgets=QtWidgets, service=service)
    pending = page.perform_action(
        "create_offer",
        service.store.get_application(application.application_id),
        values={
            "position_id": "teacher",
            "qualification": {
                "has_degree": True,
                "degree_type": "BA",
                "degree_in_ece": True,
                "ece_units_completed": None,
                "total_units_completed": None,
                "years_experience": 10,
            },
            "weekly_hours": "40",
            "template_path": "offer.docx",
            "output_dir": "offers",
        },
    )
    docx = tmp_path / "approved-v1.docx"
    pdf = tmp_path / "approved-v1.pdf"
    docx.write_bytes(b"docx-v1")
    pdf.write_bytes(b"pdf-v1")
    failed = service.approve_offer(
        application.application_id,
        pending.version_id,
        approver_name="Executive",
        approver_role="Executive Director",
        approval_date=date(2026, 7, 14),
        docx_path=docx,
        pdf_path=pdf,
    )
    assert failed.send_status == "failed"

    delivery["succeeds"] = True
    sent = page.perform_action("retry_send", service.store.get_application(application.application_id))
    assert sent.status == "sent"
    revision = page.perform_action(
        "revise_compensation",
        service.store.get_application(application.application_id),
        values={"hourly_pay": "25", "weekly_hours": "35"},
    )
    docx2 = tmp_path / "approved-v2.docx"
    pdf2 = tmp_path / "approved-v2.pdf"
    docx2.write_bytes(b"docx-v2")
    pdf2.write_bytes(b"pdf-v2")
    revised = service.approve_compensation_revision(
        application.application_id,
        revision.version_id,
        admin_name="Admin",
        approval_date=date(2026, 7, 15),
        docx_path=docx2,
        pdf_path=pdf2,
        rendered_email="Revised offer email",
    )
    assert revised.status == "sent"
    extended = page.perform_action(
        "extend_deadline",
        service.store.get_application(application.application_id),
        values={"reply_by_date": "2026-07-25"},
    )
    assert extended.operational_reply_by_date == "2026-07-25"
    accepted = page.perform_action("accept_offer", service.store.get_application(application.application_id))
    assert accepted.stage is HiringStage.ACCEPTED
    archived = page.perform_action("archive", accepted)
    assert archived.archived_at
    page.widget.close()
    app.processEvents()
