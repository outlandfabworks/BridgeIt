"""
canvas.py — Interactive path editor canvas.

Replaces the static SVG preview with a QGraphicsView that renders each
contour path as a clickable item. Supports two modes:

  SELECT mode (default):
    • Click a path to select/deselect (amber highlight)
    • Delete or Backspace key removes selected paths
    • Escape deselects all

  BRIDGE mode:
    • First click places point A (yellow dot)
    • Second click places point B and draws a manual bridge (green dashed line)
    • Escape cancels a pending first click

Middle-click drag to pan, scroll wheel to zoom.
"""

from __future__ import annotations

import math
from enum import Enum, auto
from typing import List, Optional, Set, Tuple

from PyQt6.QtCore import Qt, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QKeyEvent, QPainter, QPainterPath, QPen,
)
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem,
    QGraphicsScene, QGraphicsView,
)

from bridgeit.config import PREVIEW_BG_COLOR
from bridgeit.pipeline.bridge import Bridge
from bridgeit.pipeline.trace import Path2D


class Mode(Enum):
    SELECT = auto()
    BRIDGE = auto()


# Visual constants
_COL_NORMAL   = QColor("#ffffff")
_COL_SELECTED = QColor("#f59e0b")   # amber
_COL_HOVER    = QColor("#a78bfa")   # purple
_COL_BRIDGE   = QColor("#22c55e")   # green
_COL_PENDING  = QColor("#fbbf24")   # yellow (first bridge click)
_COL_BG       = QColor(PREVIEW_BG_COLOR)

_W_NORMAL   = 1.5
_W_SELECTED = 2.5
_MARKER_R   = 6   # bridge dot radius px


class _PathItem(QGraphicsPathItem):
    """A single clickable contour path."""

    def __init__(self, path_2d: Path2D, index: int) -> None:
        qpath = QPainterPath()
        if path_2d:
            qpath.moveTo(path_2d[0][0], path_2d[0][1])
            for x, y in path_2d[1:]:
                qpath.lineTo(x, y)
            qpath.closeSubpath()
        super().__init__(qpath)
        self.path_index = index
        self._sel = False
        self.setAcceptHoverEvents(True)
        # Very faint fill so clicks anywhere inside the shape register
        self.setBrush(QBrush(QColor(255, 255, 255, 10)))
        self._refresh_pen()

    # ------------------------------------------------------------------

    def _refresh_pen(self) -> None:
        w = _W_SELECTED if self._sel else _W_NORMAL
        c = _COL_SELECTED if self._sel else _COL_NORMAL
        self.setPen(QPen(c, w))

    def toggle(self) -> bool:
        self._sel = not self._sel
        self._refresh_pen()
        return self._sel

    def set_sel(self, v: bool) -> None:
        self._sel = v
        self._refresh_pen()

    @property
    def selected(self) -> bool:
        return self._sel

    # ------------------------------------------------------------------
    # Hover

    def hoverEnterEvent(self, event) -> None:
        if not self._sel:
            self.setPen(QPen(_COL_HOVER, _W_NORMAL))

    def hoverLeaveEvent(self, event) -> None:
        self._refresh_pen()


class InteractiveCanvas(QGraphicsView):
    """Zoomable, pannable, interactive path canvas."""

    paths_modified = pyqtSignal()   # deleted paths or added manual bridge
    mode_changed   = pyqtSignal(str)  # "select" | "bridge" | "bridge_pt2"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(_COL_BG))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._mode: Mode = Mode.SELECT
        self._items: List[_PathItem] = []
        self._excluded: Set[int] = set()
        self._manual_bridges: List[Tuple[Tuple[float,float], Tuple[float,float]]] = []
        self._bridge_pt1: Optional[Tuple[float,float]] = None
        self._pending_dot: Optional[QGraphicsEllipseItem] = None
        self._fitted = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        paths: List[Path2D],
        auto_bridges: List[Bridge],
        excluded: Optional[Set[int]] = None,
        manual_bridges: Optional[List[Tuple]] = None,
    ) -> None:
        """Populate the canvas with paths and bridge markers."""
        self._scene.clear()
        self._items.clear()
        self._pending_dot = None
        self._bridge_pt1 = None

        if excluded is not None:
            self._excluded = set(excluded)
        if manual_bridges is not None:
            self._manual_bridges = list(manual_bridges)

        for i, path in enumerate(paths):
            if i in self._excluded or len(path) < 2:
                continue
            item = _PathItem(path, i)
            self._scene.addItem(item)
            self._items.append(item)

        for b in auto_bridges:
            self._draw_bridge_line(b.island_pt, b.target_pt)

        for pt1, pt2 in self._manual_bridges:
            self._draw_bridge_line(pt1, pt2)

        bbox = self._scene.itemsBoundingRect()
        if bbox.isValid():
            self.fitInView(bbox, Qt.AspectRatioMode.KeepAspectRatio)
        self._fitted = True

    def set_mode(self, mode: Mode) -> None:
        self._mode = mode
        if mode == Mode.SELECT:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self._cancel_pending()
            self.mode_changed.emit("select")
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.mode_changed.emit("bridge")

    @property
    def mode(self) -> Mode:
        return self._mode

    def get_excluded(self) -> Set[int]:
        return set(self._excluded)

    def get_manual_bridges(self) -> List[Tuple]:
        return list(self._manual_bridges)

    def delete_selected(self) -> None:
        to_exclude = {item.path_index for item in self._items if item.selected}
        if to_exclude:
            self._excluded |= to_exclude
            self.paths_modified.emit()

    def clear_selection(self) -> None:
        for item in self._items:
            item.set_sel(False)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            # Fake a left-press so Qt starts the drag
            from PyQt6.QtCore import QEvent
            from PyQt6.QtGui import QMouseEvent
            fake = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                event.position(), event.globalPosition(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                event.modifiers(),
            )
            super().mousePressEvent(fake)
            return

        scene_pos = self.mapToScene(event.position().toPoint())
        pt = (float(scene_pos.x()), float(scene_pos.y()))

        if self._mode == Mode.BRIDGE:
            self._bridge_click(pt)
        else:
            hit = self._hit_item(scene_pos)
            if hit:
                hit.toggle()
            else:
                self.clear_selection()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif event.key() == Qt.Key.Key_Escape:
            if self._mode == Mode.BRIDGE and self._bridge_pt1 is not None:
                self._cancel_pending()
                self.mode_changed.emit("bridge")   # still in bridge mode
            elif self._mode == Mode.BRIDGE:
                self.set_mode(Mode.SELECT)
            else:
                self.clear_selection()
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Only auto-fit on first show; after that respect user zoom
        if not self._fitted:
            bbox = self._scene.itemsBoundingRect()
            if bbox.isValid():
                self.fitInView(bbox, Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------------------------------------------
    # Bridge helpers
    # ------------------------------------------------------------------

    def _bridge_click(self, pt: Tuple[float, float]) -> None:
        if self._bridge_pt1 is None:
            self._bridge_pt1 = pt
            r = _MARKER_R
            self._pending_dot = self._scene.addEllipse(
                pt[0]-r, pt[1]-r, r*2, r*2,
                QPen(Qt.PenStyle.NoPen), QBrush(_COL_PENDING),
            )
            self.mode_changed.emit("bridge_pt2")
        else:
            pt2 = pt
            pt1 = self._bridge_pt1
            self._cancel_pending()
            self._manual_bridges.append((pt1, pt2))
            self._draw_bridge_line(pt1, pt2)
            self.paths_modified.emit()
            self.mode_changed.emit("bridge")   # ready for another bridge

    def _cancel_pending(self) -> None:
        self._bridge_pt1 = None
        if self._pending_dot is not None:
            self._scene.removeItem(self._pending_dot)
            self._pending_dot = None

    def _draw_bridge_line(
        self,
        pt1: Tuple[float, float],
        pt2: Tuple[float, float],
    ) -> None:
        pen = QPen(_COL_BRIDGE, 2.0, Qt.PenStyle.DashLine)
        self._scene.addLine(pt1[0], pt1[1], pt2[0], pt2[1], pen)
        r = _MARKER_R
        for cx, cy in [pt1, pt2]:
            self._scene.addEllipse(
                cx-r, cy-r, r*2, r*2,
                QPen(Qt.PenStyle.NoPen), QBrush(_COL_BRIDGE),
            )

    def _hit_item(self, scene_pos: QPointF) -> Optional[_PathItem]:
        for item in self._scene.items(scene_pos):
            if isinstance(item, _PathItem):
                return item
        return None
