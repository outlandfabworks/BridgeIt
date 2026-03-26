"""
canvas.py — Interactive path editor canvas.

SELECT mode (default):
  • Click a path or bridge to select it (amber/red highlight)
  • Click+drag on empty space to rubber-band select multiple items
  • Delete / Backspace removes all selected items
  • Escape deselects everything

BRIDGE mode:
  • First click places point A (yellow dot)
  • Second click places point B and draws a manual bridge
  • Escape cancels a pending first click

Middle-click drag to pan.  Scroll wheel to zoom.
Mouse hover over the canvas gives it keyboard focus automatically.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import List, Optional, Set, Tuple, Union

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QKeyEvent, QPainter, QPainterPath,
    QPainterPathStroker, QPen,
)
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem,
    QGraphicsScene, QGraphicsView, QRubberBand,
)

from bridgeit.config import PREVIEW_BG_COLOR
from bridgeit.pipeline.bridge import Bridge
from bridgeit.pipeline.trace import Path2D


class Mode(Enum):
    SELECT = auto()
    BRIDGE = auto()


# ── Visual constants ──────────────────────────────────────────────────────
_COL_NORMAL     = QColor("#ffffff")
_COL_PATH_SEL   = QColor("#f59e0b")   # amber  — selected path
_COL_HOVER      = QColor("#a78bfa")   # purple — hovered path/bridge
_COL_BRIDGE     = QColor("#22c55e")   # green  — bridge marker
_COL_BRIDGE_SEL = QColor("#ef4444")   # red    — selected bridge
_COL_PENDING    = QColor("#fbbf24")   # yellow — first bridge click
_COL_BG         = QColor(PREVIEW_BG_COLOR)

_W_NORMAL   = 1.5
_W_SELECTED = 2.5
_MARKER_R   = 6     # bridge endpoint dot radius (scene px)
_HIT_WIDTH  = 16.0  # invisible hit corridor width around bridge lines


# ── Path item ─────────────────────────────────────────────────────────────

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
        # Alpha=1 fill: invisible but still triggers containment hit tests
        self.setBrush(QBrush(QColor(255, 255, 255, 1)))
        self._refresh_pen()

    def _refresh_pen(self) -> None:
        c = _COL_PATH_SEL if self._sel else _COL_NORMAL
        w = _W_SELECTED   if self._sel else _W_NORMAL
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

    def hoverEnterEvent(self, event) -> None:
        if not self._sel:
            self.setPen(QPen(_COL_HOVER, _W_NORMAL))

    def hoverLeaveEvent(self, event) -> None:
        self._refresh_pen()


# ── Bridge marker item ────────────────────────────────────────────────────

class _BridgeMarkerItem(QGraphicsPathItem):
    """A selectable bridge marker — dashed line + two endpoint dots.

    Extends QGraphicsPathItem so that Qt's well-tested shape/hit system is
    used: the path is a wide transparent corridor for easy clicking, and
    paint() draws the visible dashed line + dots on top.

    bridge_type:  "auto"   — pipeline-generated
                  "manual" — user-placed
    bridge_index: index in the respective list
    """

    def __init__(
        self,
        pt1: Tuple[float, float],
        pt2: Tuple[float, float],
        bridge_type: str,
        bridge_index: int,
    ) -> None:
        self._pt1 = QPointF(pt1[0], pt1[1])
        self._pt2 = QPointF(pt2[0], pt2[1])

        # Build hit-zone path: wide stroke corridor + endpoint circles
        line = QPainterPath()
        line.moveTo(self._pt1)
        line.lineTo(self._pt2)
        stroker = QPainterPathStroker()
        stroker.setWidth(_HIT_WIDTH)
        hit = stroker.createStroke(line)
        r = float(_MARKER_R + 4)
        hit.addEllipse(self._pt1, r, r)
        hit.addEllipse(self._pt2, r, r)

        super().__init__(hit)

        self.bridge_type  = bridge_type
        self.bridge_index = bridge_index
        self._sel = False

        # Fully transparent — only used for hit detection
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(QColor(0, 0, 0, 0)))
        self.setAcceptHoverEvents(True)

    # ── Paint the visible bridge ─────────────────────────────────────────

    def paint(self, painter: QPainter, option, widget=None) -> None:
        col = _COL_BRIDGE_SEL if self._sel else _COL_BRIDGE

        # Dashed connecting line
        painter.setPen(QPen(col, 2.0, Qt.PenStyle.DashLine))
        painter.drawLine(self._pt1, self._pt2)

        # Endpoint dots
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.setBrush(QBrush(col))
        r = float(_MARKER_R)
        painter.drawEllipse(self._pt1, r, r)
        painter.drawEllipse(self._pt2, r, r)

    # ── Selection ────────────────────────────────────────────────────────

    def toggle(self) -> bool:
        self._sel = not self._sel
        self.update()
        return self._sel

    def set_sel(self, v: bool) -> None:
        self._sel = v
        self.update()

    @property
    def selected(self) -> bool:
        return self._sel

    def hoverEnterEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if not self._sel:
            # Temporarily repaint in hover colour
            self._hover = True
            self.update()

    def hoverLeaveEvent(self, event) -> None:
        self.unsetCursor()
        self._hover = False
        self.update()

    def _color(self) -> QColor:
        if self._sel:
            return _COL_BRIDGE_SEL
        if getattr(self, "_hover", False):
            return _COL_HOVER
        return _COL_BRIDGE


# ── Interactive canvas ────────────────────────────────────────────────────

class InteractiveCanvas(QGraphicsView):
    """Zoomable, pannable canvas for selecting and editing cut paths."""

    paths_modified = pyqtSignal()    # paths deleted, bridge added/deleted
    mode_changed   = pyqtSignal(str) # "select" | "bridge" | "bridge_pt2"

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
        # WheelFocus: grabbing focus on mouse-wheel is standard; enterEvent
        # also sets focus so Delete key works without an explicit click.
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

        self._mode: Mode = Mode.SELECT

        # Path state
        self._items: List[_PathItem] = []
        self._excluded: Set[int] = set()

        # Bridge state
        self._bridge_items: List[_BridgeMarkerItem] = []
        self._deleted_auto_bridges: Set[int] = set()
        self._manual_bridges: List[Tuple[Tuple[float,float], Tuple[float,float]]] = []

        # Bridge-draw in progress
        self._bridge_pt1: Optional[Tuple[float, float]] = None
        self._pending_dot: Optional[QGraphicsEllipseItem] = None

        # Rubber-band selection
        self._rubber_band: Optional[QRubberBand] = None
        self._rubber_origin: Optional[QRect] = None   # viewport coords

        self._fitted = False

    # ── Public API ────────────────────────────────────────────────────────

    def load(
        self,
        paths: List[Path2D],
        auto_bridges: List[Bridge],
        excluded: Optional[Set[int]] = None,
        manual_bridges: Optional[List[Tuple]] = None,
        deleted_auto_bridges: Optional[Set[int]] = None,
    ) -> None:
        self._scene.clear()
        self._items.clear()
        self._bridge_items.clear()
        self._pending_dot = None
        self._bridge_pt1 = None

        if excluded is not None:
            self._excluded = set(excluded)
        if manual_bridges is not None:
            self._manual_bridges = list(manual_bridges)
        if deleted_auto_bridges is not None:
            self._deleted_auto_bridges = set(deleted_auto_bridges)

        for i, path in enumerate(paths):
            if i in self._excluded or len(path) < 2:
                continue
            item = _PathItem(path, i)
            self._scene.addItem(item)
            self._items.append(item)

        for i, b in enumerate(auto_bridges):
            if i in self._deleted_auto_bridges:
                continue
            marker = _BridgeMarkerItem(b.island_pt, b.target_pt, "auto", i)
            self._scene.addItem(marker)
            self._bridge_items.append(marker)

        for i, (pt1, pt2) in enumerate(self._manual_bridges):
            marker = _BridgeMarkerItem(pt1, pt2, "manual", i)
            self._scene.addItem(marker)
            self._bridge_items.append(marker)

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

    def get_deleted_auto_bridges(self) -> Set[int]:
        return set(self._deleted_auto_bridges)

    def delete_selected(self) -> None:
        changed = False

        to_exclude = {item.path_index for item in self._items if item.selected}
        if to_exclude:
            self._excluded |= to_exclude
            changed = True

        for bitem in list(self._bridge_items):
            if bitem.selected:
                if bitem.bridge_type == "manual":
                    idx = bitem.bridge_index
                    if 0 <= idx < len(self._manual_bridges):
                        self._manual_bridges.pop(idx)
                        for other in self._bridge_items:
                            if other.bridge_type == "manual" and other.bridge_index > idx:
                                other.bridge_index -= 1
                elif bitem.bridge_type == "auto":
                    self._deleted_auto_bridges.add(bitem.bridge_index)
                changed = True

        if changed:
            self.paths_modified.emit()

    def clear_selection(self) -> None:
        for item in self._items:
            item.set_sel(False)
        for bitem in self._bridge_items:
            bitem.set_sel(False)

    # ── Event handlers ────────────────────────────────────────────────────

    def enterEvent(self, event) -> None:
        """Grab keyboard focus when mouse enters — Delete key works instantly."""
        self.setFocus()
        super().enterEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            from PyQt6.QtCore import QEvent
            from PyQt6.QtGui import QMouseEvent as _QME
            fake = _QME(
                QEvent.Type.MouseButtonPress,
                event.position(), event.globalPosition(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                event.modifiers(),
            )
            super().mousePressEvent(fake)
            return

        if self._mode == Mode.BRIDGE:
            scene_pos = self.mapToScene(event.position().toPoint())
            pt = (float(scene_pos.x()), float(scene_pos.y()))
            self._bridge_click(pt)
            event.accept()
            return

        # SELECT mode
        scene_pos = self.mapToScene(event.position().toPoint())
        hit = self._hit_any(scene_pos)

        if hit:
            # Shift+click adds to selection; plain click toggles and deselects others
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                if not hit.selected:
                    self.clear_selection()
            hit.toggle()
        else:
            # Start rubber-band selection
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.clear_selection()
            origin = event.position().toPoint()
            self._rubber_origin = origin
            self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
            self._rubber_band.setGeometry(QRect(origin, QSize()))
            self._rubber_band.show()

        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._rubber_band is not None and self._rubber_origin is not None:
            self._rubber_band.setGeometry(
                QRect(self._rubber_origin, event.position().toPoint()).normalized()
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)

        if self._rubber_band is not None:
            # Finalise rubber-band: select everything intersecting the rect
            vp_rect = self._rubber_band.geometry()
            self._rubber_band.hide()
            self._rubber_band = None
            self._rubber_origin = None

            scene_rect = self.mapToScene(vp_rect).boundingRect()
            all_items: List[Union[_PathItem, _BridgeMarkerItem]] = (
                self._items + self._bridge_items
            )
            for item in all_items:
                item_scene_rect = item.mapToScene(
                    item.boundingRect()
                ).boundingRect()
                if scene_rect.intersects(item_scene_rect):
                    item.set_sel(True)

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
                self.mode_changed.emit("bridge")
            elif self._mode == Mode.BRIDGE:
                self.set_mode(Mode.SELECT)
            else:
                self.clear_selection()
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._fitted:
            bbox = self._scene.itemsBoundingRect()
            if bbox.isValid():
                self.fitInView(bbox, Qt.AspectRatioMode.KeepAspectRatio)

    # ── Bridge drawing ────────────────────────────────────────────────────

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
            pt1, pt2 = self._bridge_pt1, pt
            self._cancel_pending()
            idx = len(self._manual_bridges)
            self._manual_bridges.append((pt1, pt2))
            marker = _BridgeMarkerItem(pt1, pt2, "manual", idx)
            self._scene.addItem(marker)
            self._bridge_items.append(marker)
            self.paths_modified.emit()
            self.mode_changed.emit("bridge")

    def _cancel_pending(self) -> None:
        self._bridge_pt1 = None
        if self._pending_dot is not None:
            self._scene.removeItem(self._pending_dot)
            self._pending_dot = None

    # ── Hit detection ─────────────────────────────────────────────────────

    def _hit_any(
        self, scene_pos: QPointF
    ) -> Optional[Union[_PathItem, _BridgeMarkerItem]]:
        for item in self._scene.items(scene_pos):
            if isinstance(item, (_PathItem, _BridgeMarkerItem)):
                return item
        return None
