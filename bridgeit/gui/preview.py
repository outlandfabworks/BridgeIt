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
from typing import List, Optional

# Qt core types: signals/slots, geometry, and alignment flags
from PyQt6.QtCore import QMimeData, QPointF, QSizeF, Qt, pyqtSignal

# Qt graphics/painting types
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)

# Qt widget types
# QFrame = a widget that can draw a border/line
# QLabel = a widget that displays text or images
# QSizePolicy = describes how a widget should resize
# QStackedWidget = a container that shows only one "page" at a time
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bridgeit.gui.themes import current_theme
from bridgeit.gui.canvas import InteractiveCanvas


# DropZone is the initial "empty state" screen shown before any image is loaded.
# It tells the user to drag a file in, and accepts drop events.
class DropZone(QWidget):
    """Initial drop target shown before any image is loaded."""

    # pyqtSignal is Qt's mechanism for sending notifications between objects.
    # file_dropped carries the dropped file path as a string.
    file_dropped = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Enable this widget to receive drag-and-drop events from the OS
        self.setAcceptDrops(True)
        self._build_ui()

    def _build_ui(self) -> None:
        # QVBoxLayout stacks child widgets vertically, one above the other
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        t = current_theme()

        # Large down-arrow icon — purely decorative hint for the user
        self._icon_lbl = QLabel("⬇")
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setStyleSheet(f"font-size: 48px; color: {t['border']};")

        self._title_lbl = QLabel("Drop an image here")
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setStyleSheet(f"color: {t['text']}; font-size: 18px; font-weight: 600;")

        self._sub_lbl = QLabel("PNG or JPG — background will be removed automatically")
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setStyleSheet(f"color: {t['text_muted']}; font-size: 12px;")

        # RichText allows <b>bold</b> HTML within the label text
        self._hint_lbl = QLabel(f"or click <b>⊡ Open</b> in the toolbar")
        self._hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_lbl.setStyleSheet(f"color: {t['text_muted']}; font-size: 11px;")
        self._hint_lbl.setTextFormat(Qt.TextFormat.RichText)

        # addStretch() inserts elastic space that pushes content to the centre
        layout.addStretch()
        layout.addWidget(self._icon_lbl)
        layout.addSpacing(12)
        layout.addWidget(self._title_lbl)
        layout.addSpacing(4)
        layout.addWidget(self._sub_lbl)
        layout.addSpacing(8)
        layout.addWidget(self._hint_lbl)
        layout.addStretch()

    def update_theme(self) -> None:
        """Re-apply the active theme colours to all drop zone labels."""
        t = current_theme()
        self._icon_lbl.setStyleSheet(f"font-size: 48px; color: {t['border']};")
        self._title_lbl.setStyleSheet(f"color: {t['text']}; font-size: 18px; font-weight: 600;")
        self._sub_lbl.setStyleSheet(f"color: {t['text_muted']}; font-size: 12px;")
        self._hint_lbl.setStyleSheet(f"color: {t['text_muted']}; font-size: 11px;")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        # Called when the user drags something over this widget.
        # We check if the dragged item is a supported image file before accepting it.
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            # Only accept PNG and JPEG files — reject everything else
            if urls and Path(urls[0].toLocalFile()).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                event.acceptProposedAction()   # signal "yes, I'll accept this drop"
                return
        event.ignore()   # reject anything else

    def dropEvent(self, event: QDropEvent) -> None:
        # Called when the user releases the dragged file over this widget.
        # We emit the file path so the main window can start processing it.
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()   # convert URL to a local filesystem path
            self.file_dropped.emit(path)


# ImagePreview shows a single background-removed image with zoom and pan support.
# It inherits from QLabel, which can display a QPixmap (a pre-loaded image bitmap).
class ImagePreview(QLabel):
    """Zoomable/pannable image preview."""

    # Emitted when the user clicks in erase mode — carries the sampled (R, G, B)
    color_sampled = pyqtSignal(int, int, int)

    # Emitted when the user closes the lasso polygon.
    # Carries a list of (x, y) int tuples in original image pixel coordinates.
    lasso_selected = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None   # the image to display
        self._zoom = 1.0                          # current zoom level (1.0 = fit to window)
        self._offset = QPointF(0, 0)             # pan offset in pixels
        self._drag_start: Optional[QPointF] = None  # mouse position at start of pan drag
        self._erase_mode = False                  # when True, left-click samples a colour
        # Lasso / polygon trace mode
        self._lasso_mode = False
        self._lasso_pts: List[QPointF] = []          # widget-coord vertices placed so far
        self._lasso_hover: Optional[QPointF] = None  # live cursor position for preview line
        self._confirmed_lasso_img: Optional[List[tuple]] = None  # confirmed polygon in image coords
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        # Expanding policy lets the widget grow to fill all available space
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def set_erase_mode(self, on: bool) -> None:
        self._erase_mode = on
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)

    def set_lasso_mode(self, on: bool) -> None:
        self._lasso_mode = on
        self._lasso_pts = []
        self._lasso_hover = None
        self.setMouseTracking(on)
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_confirmed_lasso(self, img_pts: Optional[List[tuple]]) -> None:
        """Set or clear the confirmed polygon (image-coord points) shown as overlay."""
        self._confirmed_lasso_img = img_pts
        self.update()

    def _widget_to_image(self, pos: QPointF) -> Optional[tuple]:
        """Convert a widget-coordinate point to image-pixel coordinates."""
        if not self._pixmap:
            return None
        w, h = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(w / pw, h / ph) * self._zoom
        ox = (w - pw * scale) / 2 + self._offset.x()
        oy = (h - ph * scale) / 2 + self._offset.y()
        px = int((pos.x() - ox) / scale)
        py = int((pos.y() - oy) / scale)
        return (max(0, min(px, pw - 1)), max(0, min(py, ph - 1)))

    def _img_pts_to_widget(self, img_pts: List[tuple]) -> List[QPointF]:
        """Convert image-coordinate points to widget-coordinate QPointFs."""
        if not self._pixmap or not img_pts:
            return []
        w, h = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(w / pw, h / ph) * self._zoom
        ox = (w - pw * scale) / 2 + self._offset.x()
        oy = (h - ph * scale) / 2 + self._offset.y()
        return [QPointF(ox + x * scale, oy + y * scale) for x, y in img_pts]

    def set_pixmap(self, pixmap: QPixmap) -> None:
        # Load a new image and reset zoom/pan to the default "fit in window" state
        self._pixmap = pixmap
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self.update()   # request a repaint from Qt

    def paintEvent(self, event) -> None:
        # paintEvent is called by Qt whenever the widget needs to be redrawn.
        # We override it to draw the image with our custom zoom and pan applied.
        if not self._pixmap:
            # No image loaded yet — fall back to QLabel's default (blank) painting
            super().paintEvent(event)
            return

        # QPainter is Qt's drawing API — think of it as a canvas with a paintbrush
        painter = QPainter(self)

        # SmoothPixmapTransform uses bilinear filtering when scaling — this makes
        # the image look smooth instead of blocky when zoomed in or out.
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w, h = self.width(), self.height()       # size of this widget in screen pixels
        pw, ph = self._pixmap.width(), self._pixmap.height()  # size of the image

        # Compute the scale needed to fit the image in the window, then apply zoom.
        # min() ensures we scale to fit the smaller dimension (letterboxing).
        scale = min(w / pw, h / ph) * self._zoom
        dw, dh = pw * scale, ph * scale

        # Centre the image in the widget, then offset by the pan amount
        x = (w - dw) / 2 + self._offset.x()
        y = (h - dh) / 2 + self._offset.y()

        painter.drawPixmap(int(x), int(y), int(dw), int(dh), self._pixmap)

        # ── Lasso overlay ─────────────────────────────────────────────────
        # Determine which polygon to draw: live points OR confirmed polygon
        draw_pts: List[QPointF] = []
        is_closed = False

        if self._lasso_pts:
            draw_pts = self._lasso_pts
        elif self._confirmed_lasso_img:
            draw_pts = self._img_pts_to_widget(self._confirmed_lasso_img)
            is_closed = True

        if draw_pts:
            # Build a QPainterPath for the polygon
            poly_path = QPainterPath()
            poly_path.moveTo(draw_pts[0])
            for pt in draw_pts[1:]:
                poly_path.lineTo(pt)
            if is_closed:
                poly_path.closeSubpath()

            if is_closed and len(draw_pts) >= 3:
                # Dark vignette outside the selection
                full_path = QPainterPath()
                full_path.addRect(0, 0, float(self.width()), float(self.height()))
                outside = full_path.subtracted(poly_path)
                painter.fillPath(outside, QColor(0, 0, 0, 140))

            # Orange dashed border
            pen = QPen(QColor(251, 146, 60), 2,
                       Qt.PenStyle.SolidLine if is_closed else Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(poly_path)

            # Dots at each vertex
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(251, 146, 60)))
            for pt in draw_pts:
                painter.drawEllipse(pt, 4.0, 4.0)

            # Highlight first point larger (close-target indicator) when ≥3 pts placed
            if self._lasso_pts and len(self._lasso_pts) >= 3:
                painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
                painter.setPen(QPen(QColor(251, 146, 60), 1.5))
                painter.drawEllipse(draw_pts[0], 7.0, 7.0)

            # Live preview line from last point to cursor
            if self._lasso_mode and self._lasso_hover and not is_closed and self._lasso_pts:
                pen2 = QPen(QColor(251, 146, 60, 160), 1.5, Qt.PenStyle.DashLine)
                painter.setPen(pen2)
                painter.drawLine(draw_pts[-1], self._lasso_hover)

    def wheelEvent(self, event: QWheelEvent) -> None:
        # angleDelta().y() is positive when scrolling up (zoom in) and
        # negative when scrolling down (zoom out).
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15

        # Clamp zoom level between 10% and 2000% to prevent extreme values
        self._zoom = max(0.1, min(self._zoom * factor, 20.0))
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._lasso_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position()
                # Close if clicking near the first point (≥3 pts already placed)
                if len(self._lasso_pts) >= 3:
                    fp = self._lasso_pts[0]
                    dx, dy = pos.x() - fp.x(), pos.y() - fp.y()
                    if dx*dx + dy*dy < 225:   # 15px radius
                        self._close_lasso()
                        return
                self._lasso_pts.append(pos)
                self.update()
            elif event.button() == Qt.MouseButton.RightButton:
                if len(self._lasso_pts) >= 3:
                    self._close_lasso()
            return

        if self._erase_mode and event.button() == Qt.MouseButton.LeftButton and self._pixmap:
            pt = self._widget_to_image(event.position())
            if pt:
                qimg = self._pixmap.toImage()
                c = QColor(qimg.pixel(pt[0], pt[1]))
                self.color_sampled.emit(c.red(), c.green(), c.blue())
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag_start = event.position()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._lasso_mode:
            self._lasso_hover = event.position()
            self.update()
            return
        if self._drag_start is not None:
            delta = event.position() - self._drag_start
            self._offset += delta
            self._drag_start = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag_start = None

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._lasso_mode:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if len(self._lasso_pts) >= 3:
                    self._close_lasso()
            elif event.key() == Qt.Key.Key_Escape:
                self._lasso_pts = []
                self._lasso_hover = None
                self.update()
            elif event.key() == Qt.Key.Key_Backspace:
                if self._lasso_pts:
                    self._lasso_pts.pop()
                    self.update()
            return
        super().keyPressEvent(event)

    def _close_lasso(self) -> None:
        """Convert widget-coord polygon to image coords and emit lasso_selected."""
        img_pts = []
        for wpt in self._lasso_pts:
            ipt = self._widget_to_image(wpt)
            if ipt:
                img_pts.append(ipt)
        self._lasso_pts = []
        self._lasso_hover = None
        if len(img_pts) >= 3:
            self._confirmed_lasso_img = img_pts
            self.lasso_selected.emit(img_pts)
        self.update()


# PreviewPanel is a QStackedWidget — a container that holds multiple "pages"
# (drop zone, image view, interactive canvas) and shows only one at a time.
# Think of it like a deck of cards where only the top card is visible.
class PreviewPanel(QStackedWidget):
    """Main preview panel — manages drop zone, image preview, and interactive canvas."""

    # Emitted when a file is dropped anywhere on this panel
    file_dropped = pyqtSignal(str)

    # Page indices — used with setCurrentIndex() to switch the visible page
    PAGE_DROP   = 0   # empty state / file drop target
    PAGE_IMAGE  = 1   # background-removed image view
    PAGE_CANVAS = 2   # interactive cut-path editor

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Accept drops anywhere on the panel, not just the DropZone widget
        self.setAcceptDrops(True)

        # Create each of the three pages
        self._drop_zone   = DropZone()
        self._img_preview = ImagePreview()
        self._canvas      = InteractiveCanvas()

        # Add pages in order — their indices must match PAGE_DROP/IMAGE/CANVAS
        self.addWidget(self._drop_zone)
        self.addWidget(self._img_preview)
        self.addWidget(self._canvas)

        # Forward file_dropped signals from the DropZone up to our own signal
        # so the main window only needs to connect to PreviewPanel.file_dropped
        self._drop_zone.file_dropped.connect(self.file_dropped)

        # Start on the empty drop zone page
        self.setCurrentIndex(self.PAGE_DROP)
        self.setStyleSheet(f"background: {current_theme()['canvas_bg']};")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def canvas(self) -> InteractiveCanvas:
        # Expose the canvas so the main window can load paths and connect signals
        return self._canvas

    @property
    def img_preview(self) -> ImagePreview:
        return self._img_preview

    def show_drop_zone(self) -> None:
        # Switch to the empty-state page (e.g. after processing fails)
        self.setCurrentIndex(self.PAGE_DROP)

    def show_image(self, pixmap: QPixmap) -> None:
        # Load a new image into the image preview and switch to that page
        self._img_preview.set_pixmap(pixmap)
        self.setCurrentIndex(self.PAGE_IMAGE)

    def show_canvas(self) -> None:
        # Switch to the interactive path-editor canvas page
        self.setCurrentIndex(self.PAGE_CANVAS)

    def is_canvas_visible(self) -> bool:
        # True when the interactive canvas is the currently visible page
        return self.currentIndex() == self.PAGE_CANVAS

    def show_image_from_pil(self, pil_image) -> None:
        """Convert a PIL Image to QPixmap and display it."""
        # Qt works with QPixmap/QImage; PIL uses its own Image type.
        # We convert by going through QImage which accepts raw byte arrays.
        from PyQt6.QtGui import QImage

        # QImage requires RGBA mode — convert if needed
        if pil_image.mode != "RGBA":
            pil_image = pil_image.convert("RGBA")

        w, h = pil_image.size

        # tobytes("raw", "RGBA") gives a flat bytes array in row-major RGBA order
        data = bytes(pil_image.tobytes("raw", "RGBA"))

        # Build a QImage from the raw bytes; the stride (row width in bytes) is w*4
        # .copy() is needed because QImage doesn't own the `data` buffer — without it,
        # the data could be garbage-collected while Qt is still using it.
        qt_image = QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()

        # QPixmap is an optimised, screen-ready version of QImage
        pixmap = QPixmap.fromImage(qt_image)
        self.show_image(pixmap)

    # ------------------------------------------------------------------
    # Drag-and-drop passthrough
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        # Delegate to the DropZone's logic so we don't duplicate the validation
        self._drop_zone.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        # Convert the drop URL to a path and emit our file_dropped signal
        urls = event.mimeData().urls()
        if urls:
            self.file_dropped.emit(urls[0].toLocalFile())
