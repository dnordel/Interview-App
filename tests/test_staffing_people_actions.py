from __future__ import annotations

import os
from pathlib import Path

import pytest

from staffing_dashboard_v2 import StaffingDashboardV2Page
from staffing_service import StaffingService
from staffing_store import StaffingStore


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _page(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store)
    service.add_person(
        name="Nina Patel",
        role="Aide",
        permit_status="permit_in_process",
        units=5,
    )
    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: StaffingService(store),
    )
    page._show_people_view()
    app.processEvents()
    return qt_widgets, app, store, page


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_edit_person_button_opens_populated_dialog_and_saves_changes(tmp_path: Path) -> None:
    qt_widgets, app, store, page = _page(tmp_path)
    assignments_before = store.list_assignments()

    edit = page.widget.findChild(qt_widgets.QPushButton, "StaffingV2PeopleEditButton")
    assert edit is not None and edit.isEnabled()
    edit.click()
    app.processEvents()

    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2EditPersonDialog")
    assert dialog is not None and dialog.isVisible()
    name = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2EditPersonName")
    role = dialog.findChild(qt_widgets.QComboBox, "StaffingV2EditPersonRole")
    permit = dialog.findChild(qt_widgets.QComboBox, "StaffingV2EditPersonPermit")
    units = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2EditPersonUnits")
    assert name.text() == "Nina Patel"
    assert role.currentText() == "Aide"
    assert permit.currentText() == "Permit in Process"
    assert units.text() == "5"

    name.setText("Nina Shah")
    role.setCurrentText("Teacher")
    permit.setCurrentText("Teacher Permit")
    units.setText("18")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2EditPersonSave").click()
    app.processEvents()

    people = store.list_people()
    assert [(person.name, person.role, person.permit_status, person.units) for person in people] == [
        ("Nina Shah", "Teacher", "teacher_permit_approved", 18),
    ]
    assert store.list_assignments() == assignments_before
    assert dialog.isVisible() is False
    page.widget.close()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_deactivate_employee_button_confirms_and_soft_deactivates_unassigned_person(tmp_path: Path) -> None:
    qt_widgets, app, store, page = _page(tmp_path)

    deactivate = page.widget.findChild(qt_widgets.QPushButton, "StaffingV2PeopleDeactivateButton")
    assert deactivate is not None and deactivate.isEnabled()
    deactivate.click()
    app.processEvents()

    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2DeactivatePersonDialog")
    assert dialog is not None and dialog.isVisible()
    assert "Nina Patel" in " ".join(label.text() for label in dialog.findChildren(qt_widgets.QLabel))
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2DeactivatePersonConfirm").click()
    app.processEvents()

    person = store.list_people()[0]
    assert person.name == "Nina Patel"
    assert person.active is False
    assert dialog.isVisible() is False
    page.widget.close()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_assign_or_create_person_button_opens_mark_coming_workflow(tmp_path: Path) -> None:
    qt_widgets, app, store, page = _page(tmp_path)
    result = StaffingService(store).add_position(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
        initial_status="need_now",
    )
    page.refresh_all()
    page._show_position_drawer(result.assignment_id)
    app.processEvents()

    assign = page.widget.findChild(qt_widgets.QPushButton, "StaffingV2DrawerAssignPerson")
    assert assign is not None and assign.isEnabled()
    assign.click()
    app.processEvents()

    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2MarkComingDialog")
    assert dialog is not None and dialog.isVisible()
    assert dialog.findChild(qt_widgets.QPushButton, "StaffingV2ComingSelectPerson").isEnabled()
    assert dialog.findChild(qt_widgets.QPushButton, "StaffingV2ComingCreatePerson").isEnabled()
    dialog.close()
    page.widget.close()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_read_only_position_drawer_has_no_draft_or_save_buttons(tmp_path: Path) -> None:
    qt_widgets, app, store, page = _page(tmp_path)
    result = StaffingService(store).add_position(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    page.refresh_all()
    page._show_position_drawer(result.assignment_id)
    app.processEvents()

    assert page.widget.findChild(qt_widgets.QPushButton, "StaffingV2DrawerSaveDraft") is None
    assert page.widget.findChild(qt_widgets.QPushButton, "StaffingV2DrawerSaveChanges") is None
    page.widget.close()


def test_permit_update_copies_attachment_into_managed_storage(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store)
    person = service.add_person(name="Nina Patel", role="Teacher")
    source = tmp_path / "teacher-permit.pdf"
    source.write_bytes(b"permit-document")

    service.update_permit_status(
        person.id,
        "teacher_permit_approved",
        effective_date="2026-07-16",
        documentation_received=True,
        attachment_path=source,
    )

    updated = store.list_people()[0]
    managed = Path(updated.permit_document_path)
    assert managed.parent.resolve() == (tmp_path / "staffing_attachments").resolve()
    assert managed.name.startswith(f"person-{person.id}-permit-")
    assert managed.suffix == ".pdf"
    assert managed.read_bytes() == b"permit-document"

    service.update_permit_status(
        person.id,
        "teacher_permit_approved",
        effective_date="2026-07-17",
        documentation_received=True,
    )
    assert store.list_people()[0].permit_document_path == str(managed)


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_permit_dialog_attach_file_button_persists_selected_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qt_widgets, app, store, page = _page(tmp_path)
    result = StaffingService(store).add_position(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
        initial_status="filled",
        person_name="Nina Patel",
        start_date="2026-07-16",
    )
    source = tmp_path / "selected-permit.pdf"
    source.write_bytes(b"selected-document")
    monkeypatch.setattr(
        qt_widgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(source), "PDF files (*.pdf)"),
    )
    page.refresh_all()
    page._open_update_permit_dialog(result.assignment_id)
    app.processEvents()

    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2UpdatePermitDialog")
    attach = dialog.findChild(qt_widgets.QPushButton, "StaffingV2PermitAttachFile")
    assert attach is not None and attach.isEnabled()
    attach.click()
    app.processEvents()
    selected = dialog.findChild(qt_widgets.QLabel, "StaffingV2PermitAttachmentName")
    assert selected.text() == "selected-permit.pdf"

    dialog.findChild(qt_widgets.QPushButton, "StaffingV2PermitSubmit").click()
    app.processEvents()

    managed = Path(store.list_people()[0].permit_document_path)
    assert managed.read_bytes() == b"selected-document"
    assert dialog.isVisible() is False
    page.widget.close()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_manage_filled_card_continue_button_opens_selected_workflow_directly(tmp_path: Path) -> None:
    qt_widgets, app, store, page = _page(tmp_path)
    result = StaffingService(store).add_position(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
        initial_status="filled",
        person_name="Nina Patel",
        start_date="2026-07-16",
    )
    page.refresh_all()
    page._open_manage_filled_dialog(result.assignment_id)
    app.processEvents()

    manage = page.widget.findChild(qt_widgets.QDialog, "StaffingV2ManageFilledDialog")
    manage.findChild(qt_widgets.QPushButton, "StaffingV2ManagePermitContinue").click()
    app.processEvents()

    permit = page.widget.findChild(qt_widgets.QDialog, "StaffingV2UpdatePermitDialog")
    assert permit is not None and permit.isVisible()
    assert manage.isVisible() is False
    permit.close()
    page.widget.close()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_mark_coming_select_existing_person_popup_fills_candidate_fields(tmp_path: Path) -> None:
    qt_widgets, app, store, page = _page(tmp_path)
    result = StaffingService(store).add_position(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
        initial_status="need_now",
    )
    page.refresh_all()
    page._open_mark_coming_dialog(result.assignment_id)
    app.processEvents()

    coming = page.widget.findChild(qt_widgets.QDialog, "StaffingV2MarkComingDialog")
    coming.findChild(qt_widgets.QPushButton, "StaffingV2ComingSelectPerson").click()
    app.processEvents()

    selector = page.widget.findChild(qt_widgets.QDialog, "StaffingV2SelectPersonDialog")
    assert selector is not None and selector.isVisible()
    table = selector.findChild(qt_widgets.QTableWidget, "StaffingV2SelectPersonTable")
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "Nina Patel"
    selector.findChild(qt_widgets.QPushButton, "StaffingV2SelectPersonConfirm").click()
    app.processEvents()

    assert coming.findChild(qt_widgets.QLineEdit, "StaffingV2ComingFullName").text() == "Nina Patel"
    assert coming.findChild(qt_widgets.QComboBox, "StaffingV2ComingRole").currentText() == "Aide"
    assert selector.isVisible() is False
    coming.close()
    page.widget.close()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_employee_detail_tabs_switch_visible_content(tmp_path: Path) -> None:
    qt_widgets, app, _store, page = _page(tmp_path)
    cards = page.widget.findChildren(qt_widgets.QFrame, "StaffingV2PeopleDetailCard")
    assert len(cards) == 4
    assert all(not card.isHidden() for card in cards)

    page.widget.findChild(qt_widgets.QPushButton, "StaffingV2PeopleDocumentsTab").click()
    app.processEvents()

    visible_text = " ".join(
        label.text()
        for card in cards
        if not card.isHidden()
        for label in card.findChildren(qt_widgets.QLabel)
    )
    assert "Additional Information" in visible_text
    assert "Employee Information" not in visible_text
    assert "Current Assignment" not in visible_text
    page.widget.close()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_employee_pagination_buttons_navigate_people_rows(tmp_path: Path) -> None:
    qt_widgets, app, store, page = _page(tmp_path)
    service = StaffingService(store)
    for index in range(11):
        service.add_person(name=f"Person {index:02d}", role="Aide")
    page._refresh_people()
    app.processEvents()

    table = page.widget.findChild(qt_widgets.QTableWidget, "StaffingV2PeopleTable")
    next_page = page.widget.findChild(qt_widgets.QPushButton, "StaffingV2PeopleNextPage")
    previous_page = page.widget.findChild(qt_widgets.QPushButton, "StaffingV2PeoplePreviousPage")
    page_label = page.widget.findChild(qt_widgets.QLabel, "StaffingV2PeopleCurrentPage")
    assert table.rowCount() == 10
    assert next_page.isEnabled()
    assert previous_page.isEnabled() is False
    assert page_label.text() == "1"

    next_page.click()
    app.processEvents()

    assert table.rowCount() == 2
    assert previous_page.isEnabled()
    assert next_page.isEnabled() is False
    assert page_label.text() == "2"
    assert page.widget.findChild(qt_widgets.QLabel, "StaffingV2PeopleResultCount").text() == "Showing 11 to 12 of 12 people"
    page.widget.close()
