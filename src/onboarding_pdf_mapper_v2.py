from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtPdf, QtWidgets


class OnboardingPdfMapperCanvas(QtWidgets.QGraphicsView):
    """Rendered PDF page with movable, selectable, drawable mapping rectangles."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.document = QtPdf.QPdfDocument(self)
        self.document.statusChanged.connect(self._document_status_changed)
        self.page_number = 0
        self.page_size = QtCore.QSizeF()
        self.page_is_rendered = False
        self._draw_origin: QtCore.QPointF | None = None
        self._drawing_item: QtWidgets.QGraphicsRectItem | None = None
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        self.setAccessibleName("Visual PDF field mapping canvas")

    def load_pdf(self, path: Path, *, page_number: int = 1) -> None:
        source = Path(path).resolve(strict=True)
        if source.suffix.casefold() != ".pdf" or not source.is_file():
            raise ValueError("PDF mapper source must be a PDF file.")
        if int(page_number) < 1:
            raise ValueError("PDF mapper page number must be at least one.")
        self.page_number = int(page_number) - 1
        self.page_is_rendered = False
        self.document.close()
        self.document.load(str(source))
        if self.document.status() == QtPdf.QPdfDocument.Status.Ready:
            self._render_page()

    def add_pdf_box(
        self, rect: tuple[float, float, float, float]
    ) -> QtWidgets.QGraphicsRectItem:
        if not self.page_is_rendered:
            raise ValueError("Load a PDF page before adding a mapping box.")
        x, y, width, height = (float(value) for value in rect)
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("PDF mapping box coordinates are invalid.")
        scene_y = self.page_size.height() - y - height
        item = self.scene().addRect(
            x,
            scene_y,
            width,
            height,
            QtGui.QPen(QtGui.QColor("#2563EB"), 2),
            QtGui.QBrush(QtGui.QColor(37, 99, 235, 40)),
        )
        item.setFlags(
            QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )
        item.setSelected(True)
        return item

    def pdf_rect(self, item: QtWidgets.QGraphicsRectItem) -> tuple[float, float, float, float]:
        bounds = item.sceneTransform().mapRect(item.rect())
        return (
            float(bounds.x()),
            float(self.page_size.height() - bounds.y() - bounds.height()),
            float(bounds.width()),
            float(bounds.height()),
        )

    def resize_box(self, item: QtWidgets.QGraphicsRectItem, *, width: float, height: float) -> None:
        x, y, _old_width, _old_height = self.pdf_rect(item)
        self._set_pdf_rect(item, x=x, y=y, width=float(width), height=float(height))

    def move_box(self, item: QtWidgets.QGraphicsRectItem, *, x: float, y: float) -> None:
        _old_x, _old_y, width, height = self.pdf_rect(item)
        self._set_pdf_rect(item, x=float(x), y=float(y), width=width, height=height)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.page_is_rendered and event.button() == QtCore.Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.scene().itemAt(point, self.transform()) is None:
                self._draw_origin = point
                self._drawing_item = self.scene().addRect(
                    QtCore.QRectF(point, point),
                    QtGui.QPen(QtGui.QColor("#2563EB"), 2),
                    QtGui.QBrush(QtGui.QColor(37, 99, 235, 40)),
                )
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._draw_origin is not None and self._drawing_item is not None:
            point = self.mapToScene(event.position().toPoint())
            self._drawing_item.setRect(QtCore.QRectF(self._draw_origin, point).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._draw_origin is not None and self._drawing_item is not None:
            item = self._drawing_item
            self._draw_origin = None
            self._drawing_item = None
            if item.rect().width() < 2 or item.rect().height() < 2:
                self.scene().removeItem(item)
            else:
                item.setFlags(
                    QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                    | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                )
                item.setSelected(True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _document_status_changed(self, status: QtPdf.QPdfDocument.Status) -> None:
        if status == QtPdf.QPdfDocument.Status.Ready:
            self._render_page()

    def _render_page(self) -> None:
        if self.page_number >= self.document.pageCount():
            raise ValueError("PDF mapper page is outside the source document.")
        self.page_size = self.document.pagePointSize(self.page_number)
        image_size = QtCore.QSize(
            max(1, round(self.page_size.width())),
            max(1, round(self.page_size.height())),
        )
        image = self.document.render(self.page_number, image_size)
        self.scene().clear()
        self.scene().addPixmap(QtGui.QPixmap.fromImage(image)).setZValue(-1)
        self.scene().setSceneRect(QtCore.QRectF(QtCore.QPointF(), self.page_size))
        self.page_is_rendered = True
        self.fitInView(self.scene().sceneRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def _set_pdf_rect(
        self,
        item: QtWidgets.QGraphicsRectItem,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("PDF mapping box coordinates are invalid.")
        item.setPos(0, 0)
        item.setRect(x, self.page_size.height() - y - height, width, height)
