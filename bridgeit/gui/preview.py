"""
preview.py — SVG/image preview widget.

Displays either:
  - The background-removed PNG (after stage 1)
  - The SVG cut path overlay (after full pipeline)

Supports zoom + pan via mouse wheel and middle-click drag.
Accepts drag-and-drop of image files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QMimeData, QPointF, QRectF, QSizeF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bridgeit.config import MUTED_COLOR, PREVIEW_BG_COLOR, TEXT_COLOR


class DropZone(QWidget):
    """Initial drop target shown before any image is loaded."""

    file_dropped = pyqtSignal(str)
    open_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel("⬇")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("font-size: 48px; color: #4a4a6a;")

        title = QLabel("Drop an image here")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 18px; font-weight: 600;")

        sub = QLabel("PNG or JPG — background will be removed automatically")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 12px;")

        hint = QLabel("or click <b>Open Image</b> in the toolbar")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px;")
        hint.setTextFormat(Qt.TextFormat.RichText)

        layout.addStretch()
        layout.addWidget(icon_lbl)
        layout.addSpacing(12)
        layout.addWidget(title)
        layout.addSpacing(4)
        layout.addWidget(sub)
        layout.addSpacing(8)
        layout.addWidget(hint)
        layout.addStretch()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and Path(urls[0].toLocalFile()).suffix.lower() in {".png", ".jpg", ".jpeg"}:
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self.file_dropped.emit(path)


class ImagePreview(QLabel):
    """Zoomable/pannable image preview."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._drag_start: Optional[QPointF] = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self.update()

    def paintEvent(self, event) -> None:
        if not self._pixmap:
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w, h = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()

        # Fit-to-window scale
        scale = min(w / pw, h / ph) * self._zoom
        dw, dh = pw * scale, ph * scale
        x = (w - dw) / 2 + self._offset.x()
        y = (h - dh) / 2 + self._offset.y()

        painter.drawPixmap(int(x), int(y), int(dw), int(dh), self._pixmap)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        self._zoom = max(0.1, min(self._zoom * factor, 20.0))
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag_start = event.position()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None:
            delta = event.position() - self._drag_start
            self._offset += delta
            self._drag_start = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag_start = None


class SvgPreview(QWidget):
    """SVG preview — white background so black cut lines are visible."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # White background simulates paper / laser bed view
        self.setStyleSheet("background: white;")
        self._svg_widget = QSvgWidget(self)
        self._svg_widget.setStyleSheet("background: white;")
        self._svg_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(self._svg_widget)

    def load_svg_string(self, svg: str) -> None:
        self._svg_widget.load(svg.encode("utf-8"))
        self.update()


class PreviewPanel(QStackedWidget):
    """Main preview panel — manages drop zone, image preview, and SVG preview."""

    file_dropped = pyqtSignal(str)

    # Page indices
    PAGE_DROP = 0
    PAGE_IMAGE = 1
    PAGE_SVG = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

        self._drop_zone = DropZone()
        self._image_preview = ImagePreview()
        self._svg_preview = SvgPreview()

        self.addWidget(self._drop_zone)
        self.addWidget(self._image_preview)
        self.addWidget(self._svg_preview)

        self._drop_zone.file_dropped.connect(self.file_dropped)
        self.setCurrentIndex(self.PAGE_DROP)

        self.setStyleSheet(f"background: {PREVIEW_BG_COLOR};")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_drop_zone(self) -> None:
        self.setCurrentIndex(self.PAGE_DROP)

    def show_image(self, pixmap: QPixmap) -> None:
        self._image_preview.set_pixmap(pixmap)
        self.setCurrentIndex(self.PAGE_IMAGE)

    def show_svg(self, svg_string: str) -> None:
        self._svg_preview.load_svg_string(svg_string)
        self.setCurrentIndex(self.PAGE_SVG)

    def show_image_from_pil(self, pil_image) -> None:
        """Convert a PIL Image to QPixmap and display it."""
        from PyQt6.QtGui import QImage

        if pil_image.mode != "RGBA":
            pil_image = pil_image.convert("RGBA")
        w, h = pil_image.size
        # Use raw bytes so Qt owns the data independently (avoids double-free)
        data = bytes(pil_image.tobytes("raw", "RGBA"))
        qt_image = QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        pixmap = QPixmap.fromImage(qt_image)
        self.show_image(pixmap)

    # ------------------------------------------------------------------
    # Drag-and-drop passthrough
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self._drop_zone.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.file_dropped.emit(urls[0].toLocalFile())
