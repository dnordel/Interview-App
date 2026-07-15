import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from data_store import InterviewHistoryStore
from hiring_pipeline import HiringPipelineStore, HiringWorkflowService


def test_hiring_workspace_uses_unified_pipeline_without_horizontal_page_scroll(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
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
    assert page.detail_next_action.text() == "Schedule director review"
    assert page.timeline_list.count() == 1

    page.widget.close()


def test_hiring_workspace_exposes_native_interviews_candidates_and_offers_views(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "native-views.sqlite3"))
    application = service.start_application(
        legal_name="Maya Patel",
        email="maya@example.com",
        phone="555-0100",
        school="Palmdale",
        position="Preschool",
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
            "hourly_pay": "24.00",
            "weekly_hours": "40",
            "template_path": "offer.docx",
            "output_dir": "offers",
        },
    )

    assert submitted.status == "pending_approval"
    assert service.store.get_application(application.application_id).stage is HiringStage.EXECUTIVE_APPROVAL
    page.widget.close()
    app.processEvents()


def test_hiring_workspace_visual_sizes_have_no_page_horizontal_scroll(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtWidgets
    from hiring_workspace_v2 import HiringWorkspaceV2Page

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service = HiringWorkflowService(HiringPipelineStore(tmp_path / "visual.sqlite3"))
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
    approval.close()


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
        values={"hourly_pay": "24", "weekly_hours": "40", "template_path": "offer.docx", "output_dir": "offers"},
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
