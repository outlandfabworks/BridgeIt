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

# Qt core types: signals/slots, geometry, and alignment flags
from PyQt6.QtCore import QMimeData, QPointF, QRect, QRectF, QSizeF, Qt, pyqtSignal

# Qt graphics/painting types
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QMouseEvent,
    QPainter,
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
            if urls and Path(urls[0].toLocalFile()).suffix.lower() in {".png", ".jpg", ".jpeg"}:
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

    # Emitted when the user finishes drawing a keep-region rectangle.
    # Carries (x1, y1, x2, y2) in original image pixel coordinates.
    crop_selected = pyqtSignal(int, int, int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None   # the image to display
        self._zoom = 1.0                          # current zoom level (1.0 = fit to window)
        self._offset = QPointF(0, 0)             # pan offset in pixels
        self._drag_start: Optional[QPointF] = None  # mouse position at start of pan drag
        self._erase_mode = False                  # when True, left-click samples a colour
        # Crop / keep-region mode state
        self._crop_mode = False
        self._crop_drag_start: Optional[QPointF] = None   # widget coords of drag start
        self._crop_drag_end: Optional[QPointF] = None     # widget coords of drag end
        self._active_crop: Optional[tuple] = None         # confirmed (x1,y1,x2,y2) in image px
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        # Expanding policy lets the widget grow to fill all available space
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_erase_mode(self, on: bool) -> None:
        self._erase_mode = on
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)

    def set_crop_mode(self, on: bool) -> None:
        self._crop_mode = on
        self._crop_drag_start = None
        self._crop_drag_end = None
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_active_crop(self, rect: Optional[tuple]) -> None:
        """Set or clear the confirmed crop overlay (x1, y1, x2, y2) in image pixels."""
        self._active_crop = rect
        self.update()

    def _widget_to_image(self, pos: QPointF) -> Optional[tuple]:
        """Convert a widget-coordinate point to image-pixel coordinates."""
        if not self._pixmap:
            return None
        w, h = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(w / pw, h / ph) * self._zoom
        img_x = (w - pw * scale) / 2 + self._offset.x()
        img_y = (h - ph * scale) / 2 + self._offset.y()
        px = int((pos.x() - img_x) / scale)
        py = int((pos.y() - img_y) / scale)
        return (max(0, min(px, pw - 1)), max(0, min(py, ph - 1)))

    def _image_to_widget_rect(self, x1: int, y1: int, x2: int, y2: int) -> Optional[QRectF]:
        """Convert image-pixel crop rect to widget-coordinate QRectF."""
        if not self._pixmap:
            return None
        w, h = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(w / pw, h / ph) * self._zoom
        ox = (w - pw * scale) / 2 + self._offset.x()
        oy = (h - ph * scale) / 2 + self._offset.y()
        return QRectF(
            ox + x1 * scale, oy + y1 * scale,
            (x2 - x1) * scale, (y2 - y1) * scale,
        )

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

        # ── Crop overlay ──────────────────────────────────────────────────
        # Draw a dark vignette outside the selection + orange dashed border.
        draw_rect: Optional[QRectF] = None
        if self._crop_drag_start and self._crop_drag_end:
            draw_rect = QRectF(self._crop_drag_start, self._crop_drag_end).normalized()
        elif self._active_crop:
            draw_rect = self._image_to_widget_rect(*self._active_crop)

        if draw_rect:
            shadow = QColor(0, 0, 0, 140)
            # Four dark rectangles surrounding the selection
            painter.fillRect(QRectF(0, 0, w, draw_rect.top()), shadow)
            painter.fillRect(QRectF(0, draw_rect.bottom(), w, h - draw_rect.bottom()), shadow)
            painter.fillRect(QRectF(0, draw_rect.top(), draw_rect.left(), draw_rect.height()), shadow)
            painter.fillRect(QRectF(draw_rect.right(), draw_rect.top(), w - draw_rect.right(), draw_rect.height()), shadow)
            # Orange dashed border
            pen = QPen(QColor(251, 146, 60), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(draw_rect)

    def wheelEvent(self, event: QWheelEvent) -> None:
        # angleDelta().y() is positive when scrolling up (zoom in) and
        # negative when scrolling down (zoom out).
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15

        # Clamp zoom level between 10% and 2000% to prevent extreme values
        self._zoom = max(0.1, min(self._zoom * factor, 20.0))
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # In crop mode, left-click starts drawing the keep-region rectangle
        if self._crop_mode and event.button() == Qt.MouseButton.LeftButton:
            self._crop_drag_start = event.position()
            self._crop_drag_end = event.position()
            self.update()
            return
        # In erase mode, left-click samples the colour at the clicked image pixel
        if self._erase_mode and event.button() == Qt.MouseButton.LeftButton and self._pixmap:
            pt = self._widget_to_image(event.position())
            if pt:
                qimg = self._pixmap.toImage()
                c = QColor(qimg.pixel(pt[0], pt[1]))
                self.color_sampled.emit(c.red(), c.green(), c.blue())
            return  # don't start a pan drag in erase mode
        # Middle-click starts a pan drag — record where the mouse is
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag_start = event.position()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # In crop mode, update the live drag rectangle
        if self._crop_mode and self._crop_drag_start is not None:
            self._crop_drag_end = event.position()
            self.update()
            return
        # While middle-click is held, compute how far the mouse has moved
        # and shift the image's offset by that amount (panning)
        if self._drag_start is not None:
            delta = event.position() - self._drag_start
            self._offset += delta
            self._drag_start = event.position()  # update start for next frame
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        # In crop mode, finalise the rectangle and emit the selection
        if self._crop_mode and event.button() == Qt.MouseButton.LeftButton and self._crop_drag_start:
            pt1 = self._widget_to_image(self._crop_drag_start)
            pt2 = self._widget_to_image(event.position())
            self._crop_drag_start = None
            self._crop_drag_end = None
            if pt1 and pt2:
                x1, x2 = sorted([pt1[0], pt2[0]])
                y1, y2 = sorted([pt1[1], pt2[1]])
                if x2 > x1 + 5 and y2 > y1 + 5:   # ignore tiny accidental drags
                    self._active_crop = (x1, y1, x2, y2)
                    self.crop_selected.emit(x1, y1, x2, y2)
            self.update()
            return
        # Middle-click released — stop panning
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag_start = None


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
