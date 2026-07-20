import os

import pytest
from pypdf import PdfWriter


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_visual_mapper_renders_page_and_draws_moves_resizes_pdf_coordinate_box(tmp_path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("PySide6.QtPdf")
    from onboarding_pdf_mapper_v2 import OnboardingPdfMapperCanvas

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    source = tmp_path / "mapper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with source.open("wb") as file:
        writer.write(file)
    canvas = OnboardingPdfMapperCanvas()
    canvas.resize(800, 900)

    canvas.load_pdf(source)
    for _ in range(20):
        app.processEvents()
        if canvas.page_is_rendered:
            break
    item = canvas.add_pdf_box((72, 650, 200, 20))
    canvas.resize_box(item, width=180, height=24)
    canvas.move_box(item, x=80, y=640)

    assert canvas.page_is_rendered is True
    assert canvas.pdf_rect(item) == pytest.approx((80, 640, 180, 24), abs=0.5)
