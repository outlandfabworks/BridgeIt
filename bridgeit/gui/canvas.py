"""
canvas.py — Interactive path editor canvas.

SELECT mode (default):
  • Click a path or bridge to select it (amber/red highlight)
  • Click+drag on empty space to rubber-band select multiple items
  • Delete / Backspace removes all selected items
  • Escape deselects everything

BRIDGE mode:
  • White snap dot follows cursor, snapping to the nearest path segment
  • Hold Shift to constrain the bridge angle to 0°/45°/90°/135° (dashed guide shown)
  • First click places point A (snapped, yellow dot)
  • Additional clicks each place a new bridge — all staged bridges shown as white
    preview rectangles immediately
  • Click on a staged bridge to select it; Delete removes it from staging
  • Press Enter or click "Confirm Bridges" to commit all staged bridges as real cut paths
  • Escape cancels the pending first point, or clears all staged bridges; a second
    Escape with nothing pending exits bridge mode entirely

Middle-click drag to pan.  Scroll wheel to zoom.
Mouse hover over the canvas gives it keyboard focus automatically.
"""

from __future__ import annotations

import math
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
_COL_NORMAL      = QColor("#ffffff")
_COL_PATH_SEL    = QColor("#f59e0b")   # amber  — selected path
_COL_HOVER       = QColor("#a78bfa")   # purple — hovered path/bridge
_COL_BRIDGE      = QColor("#22c55e")   # green  — auto bridge marker
_COL_BRIDGE_SEL  = QColor("#ef4444")   # red    — selected bridge
_COL_STAGED_SEL  = QColor("#f59e0b")   # amber  — selected staged bridge
_COL_PENDING     = QColor("#fbbf24")   # yellow — first bridge click dot
_COL_SNAP        = QColor("#ffffff")   # white  — snap indicator
_COL_BG          = QColor(PREVIEW_BG_COLOR)

_W_NORMAL    = 1.5
_W_SELECTED  = 2.5
_MARKER_R    = 6      # bridge endpoint dot radius (scene px)
_SNAP_R      = 5      # snap indicator dot radius (scene px)
_HIT_WIDTH   = 16.0   # invisible hit corridor width around bridge lines


# ── Module-level geometry helpers ─────────────────────────────────────────

def _closest_point_on_segment(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> Tuple[float, float]:
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom < 1e-10:
        return ax, ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return ax + t * dx, ay + t * dy


def _compute_bridge_rect(
    pt1: Tuple[float, float],
    pt2: Tuple[float, float],
    width_px: float,
) -> Optional[List[Tuple[float, float]]]:
    dx = pt2[0] - pt1[0]
    dy = pt2[1] - pt1[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    ux, uy = dx / length, dy / length
    perp_x, perp_y = -uy, ux
    half = width_px / 2
    a = (pt1[0] + perp_x * half, pt1[1] + perp_y * half)
    b = (pt1[0] - perp_x * half, pt1[1] - perp_y * half)
    c = (pt2[0] - perp_x * half, pt2[1] - perp_y * half)
    d = (pt2[0] + perp_x * half, pt2[1] + perp_y * half)
    return [a, b, c, d, a]


def _constrain_to_45(
    origin: Tuple[float, float],
    pt: Tuple[float, float],
) -> Tuple[float, float]:
    """Snap pt to the nearest 45°-multiple direction from origin."""
    dx = pt[0] - origin[0]
    dy = pt[1] - origin[1]
    angle = math.atan2(dy, dx)
    snapped = round(angle / (math.pi / 4)) * (math.pi / 4)
    dist = math.hypot(dx, dy)
    return (origin[0] + dist * math.cos(snapped),
            origin[1] + dist * math.sin(snapped))


# ── Path item ─────────────────────────────────────────────────────────────

class _PathItem(QGraphicsPathItem):
    """A single clickable contour path."""

    def __init__(self, path_2d: Path2D, index: int) -> None:
        self._path_2d = path_2d   # kept for snap computation
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
        self.setBrush(QBrush(QColor(255, 255, 255, 1)))
        self._refresh_pen()

    def _refresh_pen(self) -> None:
        c = _COL_PATH_SEL if self._sel else _COL_NORMAL
        self.setPen(QPen(c, _W_SELECTED if self._sel else _W_NORMAL))

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


# ── Staged bridge item (pending confirmation) ─────────────────────────────

class _StagedBridgeItem(QGraphicsPathItem):
    """Preview rect for a bridge that has been placed but not yet confirmed.

    Visually: white outline + subtle green tint.
    Selectable: click to highlight amber, Delete removes from staging.
    """

    def __init__(
        self,
        pt1: Tuple[float, float],
        pt2: Tuple[float, float],
        staged_index: int,
        width_px: float,
    ) -> None:
        self.bridge_type  = "staged"
        self.staged_index = staged_index
        self._sel = False

        rect_pts = _compute_bridge_rect(pt1, pt2, width_px)
        qpath = QPainterPath()
        if rect_pts:
            qpath.moveTo(rect_pts[0][0], rect_pts[0][1])
            for x, y in rect_pts[1:]:
                qpath.lineTo(x, y)
            qpath.closeSubpath()

        super().__init__(qpath)
        self.setAcceptHoverEvents(True)
        self.setBrush(QBrush(QColor(34, 197, 94, 30)))
        self._refresh_pen()

    def _refresh_pen(self) -> None:
        c = _COL_STAGED_SEL if self._sel else _COL_BRIDGE
        self.setPen(QPen(c, _W_SELECTED if self._sel else _W_NORMAL))

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


# ── Confirmed manual bridge item ──────────────────────────────────────────

class _ConfirmedBridgeItem(QGraphicsPathItem):
    """A confirmed manual bridge shown as its actual white cut rectangle."""

    def __init__(
        self,
        pt1: Tuple[float, float],
        pt2: Tuple[float, float],
        bridge_index: int,
        width_px: float,
    ) -> None:
        self.bridge_type  = "manual"
        self.bridge_index = bridge_index
        self._sel = False

        rect_pts = _compute_bridge_rect(pt1, pt2, width_px)
        qpath = QPainterPath()
        if rect_pts:
            qpath.moveTo(rect_pts[0][0], rect_pts[0][1])
            for x, y in rect_pts[1:]:
                qpath.lineTo(x, y)
            qpath.closeSubpath()

        super().__init__(qpath)
        self.setAcceptHoverEvents(True)
        self.setBrush(QBrush(QColor(255, 255, 255, 1)))
        self._refresh_pen()

    def _refresh_pen(self) -> None:
        c = _COL_PATH_SEL if self._sel else _COL_NORMAL
        self.setPen(QPen(c, _W_SELECTED if self._sel else _W_NORMAL))

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


# ── Auto bridge marker item ───────────────────────────────────────────────

class _BridgeMarkerItem(QGraphicsPathItem):
    """A selectable auto-generated bridge marker — dashed line + endpoint dots."""

    def __init__(
        self,
        pt1: Tuple[float, float],
        pt2: Tuple[float, float],
        bridge_type: str,
        bridge_index: int,
    ) -> None:
        self._pt1 = QPointF(pt1[0], pt1[1])
        self._pt2 = QPointF(pt2[0], pt2[1])

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
        self._hover = False

        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(QColor(0, 0, 0, 0)))
        self.setAcceptHoverEvents(True)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        col = _COL_BRIDGE_SEL if self._sel else (_COL_HOVER if self._hover else _COL_BRIDGE)
        painter.setPen(QPen(col, 2.0, Qt.PenStyle.DashLine))
        painter.drawLine(self._pt1, self._pt2)
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.setBrush(QBrush(col))
        r = float(_MARKER_R)
        painter.drawEllipse(self._pt1, r, r)
        painter.drawEllipse(self._pt2, r, r)

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
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, event) -> None:
        self.unsetCursor()
        self._hover = False
        self.update()


# ── Interactive canvas ────────────────────────────────────────────────────

_AnyBridgeItem = Union[_BridgeMarkerItem, _ConfirmedBridgeItem, _StagedBridgeItem]
_AnyItem = Union[_PathItem, _AnyBridgeItem]


class InteractiveCanvas(QGraphicsView):
    """Zoomable, pannable canvas for selecting and editing cut paths."""

    paths_modified   = pyqtSignal()    # paths deleted, bridge added/deleted
    mode_changed     = pyqtSignal(str) # "select"|"bridge"|"bridge_pt2"|"bridge_confirm"
    selection_changed = pyqtSignal()   # any click that may have changed selection

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
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

        self._mode: Mode = Mode.SELECT

        # Path state
        self._items: List[_PathItem] = []
        self._excluded: Set[int] = set()

        # Confirmed bridge state
        self._bridge_items: List[_AnyBridgeItem] = []
        self._deleted_auto_bridges: Set[int] = set()
        self._manual_bridges: List[Tuple] = []  # (pt1, pt2, width_px)

        # Staged (not-yet-confirmed) bridges
        self._staged_data: List[Tuple[Tuple, Tuple]] = []   # (pt1, pt2)
        self._staged_items: List[_StagedBridgeItem] = []

        # Active first-point placement
        self._bridge_pt1: Optional[Tuple[float, float]] = None
        self._pending_dot: Optional[QGraphicsEllipseItem] = None

        # Hover aids
        self._snap_dot: Optional[QGraphicsEllipseItem] = None
        self._guide_line: Optional[QGraphicsPathItem] = None   # Shift-constrain guide

        # Bridge width used when placing (set by mainwindow before entering bridge mode)
        self._bridge_width_px: float = 5.0

        # Rubber-band selection
        self._rubber_band: Optional[QRubberBand] = None
        self._rubber_origin: Optional[QRect] = None

        self._fitted = False

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def bridge_width_px(self) -> float:
        return self._bridge_width_px

    @bridge_width_px.setter
    def bridge_width_px(self, v: float) -> None:
        self._bridge_width_px = max(0.5, v)

    @property
    def staged_count(self) -> int:
        return len(self._staged_data)

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
        self._staged_items.clear()
        self._staged_data.clear()
        self._pending_dot = None
        self._snap_dot = None
        self._guide_line = None
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

        for i, bridge_data in enumerate(self._manual_bridges):
            pt1, pt2 = bridge_data[0], bridge_data[1]
            width_px = bridge_data[2] if len(bridge_data) > 2 else self._bridge_width_px
            item = _ConfirmedBridgeItem(pt1, pt2, i, width_px)
            self._scene.addItem(item)
            self._bridge_items.append(item)

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

        # Remove staged bridges that are selected (no paths_modified needed — not committed)
        staged_sel = [i for i, s in enumerate(self._staged_items) if s.selected]
        for i in reversed(staged_sel):
            self._scene.removeItem(self._staged_items[i])
            self._staged_items.pop(i)
            self._staged_data.pop(i)
            changed = True
        # Re-index
        for j, s in enumerate(self._staged_items):
            s.staged_index = j
        if staged_sel:
            self._emit_mode_hint()
            return  # staged removals don't need a pipeline reload

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
        for s in self._staged_items:
            s.set_sel(False)

    def confirm_staged_bridges(self) -> None:
        """Commit all staged bridges as confirmed manual bridges."""
        if not self._staged_data:
            return
        for staged_item in self._staged_items:
            self._scene.removeItem(staged_item)
        self._staged_items.clear()
        for pt1, pt2 in self._staged_data:
            self._manual_bridges.append((pt1, pt2, self._bridge_width_px))
        self._staged_data.clear()
        self.paths_modified.emit()
        self._emit_mode_hint()

    # ── Event handlers ────────────────────────────────────────────────────

    def enterEvent(self, event) -> None:
        self.setFocus()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hide_snap_dot()
        self._hide_guide_line()
        super().leaveEvent(event)

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
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._bridge_click(pt, shift)
            event.accept()
            return

        # SELECT mode
        scene_pos = self.mapToScene(event.position().toPoint())
        hit = self._hit_any(scene_pos)

        if hit:
            multi = bool(event.modifiers() & (
                Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
            ))
            if not multi:
                if not hit.selected:
                    self.clear_selection()
            hit.toggle()
            self.selection_changed.emit()
        else:
            if not (event.modifiers() & (
                Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
            )):
                self.clear_selection()
                self.selection_changed.emit()
            origin = event.position().toPoint()
            self._rubber_origin = origin
            self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
            self._rubber_band.setGeometry(QRect(origin, QSize()))
            self._rubber_band.show()

        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._mode == Mode.BRIDGE:
            scene_pos = self.mapToScene(event.position().toPoint())
            pt = (float(scene_pos.x()), float(scene_pos.y()))
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

            if shift and self._bridge_pt1 is not None:
                constrained = _constrain_to_45(self._bridge_pt1, pt)
                snapped = self._snap_to_path(constrained)
                self._show_guide_line(self._bridge_pt1, snapped)
            else:
                self._hide_guide_line()
                snapped = self._snap_to_path(pt)

            self._update_snap_dot(snapped)
        else:
            self._hide_snap_dot()
            self._hide_guide_line()

        if self._rubber_band is not None and self._rubber_origin is not None:
            self._rubber_band.setGeometry(
                QRect(self._rubber_origin, event.position().toPoint()).normalized()
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)

        if self._rubber_band is not None:
            vp_rect = self._rubber_band.geometry()
            self._rubber_band.hide()
            self._rubber_band = None
            self._rubber_origin = None

            scene_rect = self.mapToScene(vp_rect).boundingRect()
            all_items: List[_AnyItem] = self._items + self._bridge_items + self._staged_items
            for item in all_items:
                if scene_rect.intersects(item.mapToScene(item.boundingRect()).boundingRect()):
                    item.set_sel(True)
            self.selection_changed.emit()

        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._mode == Mode.BRIDGE and self._staged_data:
                self.confirm_staged_bridges()
        elif event.key() == Qt.Key.Key_Escape:
            if self._mode == Mode.BRIDGE:
                if self._bridge_pt1 is not None:
                    self._cancel_pt1()
                    self._emit_mode_hint()
                elif self._staged_data:
                    self._cancel_staged()
                    self._emit_mode_hint()
                else:
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

    def _bridge_click(self, pt: Tuple[float, float], shift: bool = False) -> None:
        if self._bridge_pt1 is None:
            # First point — snap and place yellow dot
            snapped = self._snap_to_path(
                _constrain_to_45(pt, pt) if False else pt   # no constraint for pt1
            )
            self._bridge_pt1 = snapped
            r = _MARKER_R
            self._pending_dot = self._scene.addEllipse(
                snapped[0] - r, snapped[1] - r, r * 2, r * 2,
                QPen(Qt.PenStyle.NoPen), QBrush(_COL_PENDING),
            )
            self.mode_changed.emit("bridge_pt2")
        else:
            # Second point — snap, apply optional Shift constraint, stage the bridge
            if shift:
                constrained = _constrain_to_45(self._bridge_pt1, pt)
                snapped = self._snap_to_path(constrained)
            else:
                snapped = self._snap_to_path(pt)

            pt1 = self._bridge_pt1
            self._cancel_pt1()

            idx = len(self._staged_data)
            self._staged_data.append((pt1, snapped))
            staged_item = _StagedBridgeItem(pt1, snapped, idx, self._bridge_width_px)
            self._scene.addItem(staged_item)
            self._staged_items.append(staged_item)

            self._hide_guide_line()
            self._emit_mode_hint()

    def _cancel_pt1(self) -> None:
        self._bridge_pt1 = None
        if self._pending_dot is not None:
            self._scene.removeItem(self._pending_dot)
            self._pending_dot = None

    def _cancel_staged(self) -> None:
        for item in self._staged_items:
            self._scene.removeItem(item)
        self._staged_items.clear()
        self._staged_data.clear()

    def _cancel_pending(self) -> None:
        self._cancel_pt1()
        self._cancel_staged()
        self._hide_snap_dot()
        self._hide_guide_line()

    def _emit_mode_hint(self) -> None:
        """Emit the right mode_changed string based on current staging state."""
        if self._staged_data:
            self.mode_changed.emit("bridge_confirm")
        else:
            self.mode_changed.emit("bridge")

    # ── Snapping & visual aids ────────────────────────────────────────────

    def _snap_to_path(self, pt: Tuple[float, float]) -> Tuple[float, float]:
        best_dist_sq = float("inf")
        best_pt = pt
        px, py = pt
        for item in self._items:
            pts = item._path_2d
            n = len(pts)
            if n < 2:
                continue
            for j in range(n):
                ax, ay = pts[j]
                bx, by = pts[(j + 1) % n]
                cx, cy = _closest_point_on_segment(px, py, ax, ay, bx, by)
                d_sq = (cx - px) ** 2 + (cy - py) ** 2
                if d_sq < best_dist_sq:
                    best_dist_sq = d_sq
                    best_pt = (cx, cy)
        return best_pt

    def _update_snap_dot(self, pt: Tuple[float, float]) -> None:
        r = float(_SNAP_R)
        if self._snap_dot is None:
            self._snap_dot = self._scene.addEllipse(
                pt[0] - r, pt[1] - r, r * 2, r * 2,
                QPen(_COL_SNAP, 1.5),
                QBrush(QColor(255, 255, 255, 60)),
            )
        else:
            self._snap_dot.setRect(pt[0] - r, pt[1] - r, r * 2, r * 2)

    def _hide_snap_dot(self) -> None:
        if self._snap_dot is not None:
            self._scene.removeItem(self._snap_dot)
            self._snap_dot = None

    def _show_guide_line(
        self,
        pt1: Tuple[float, float],
        pt2: Tuple[float, float],
    ) -> None:
        """Draw a dashed guide line from pt1 to pt2 (Shift-constrain visual)."""
        qpath = QPainterPath()
        qpath.moveTo(pt1[0], pt1[1])
        qpath.lineTo(pt2[0], pt2[1])
        if self._guide_line is None:
            self._guide_line = QGraphicsPathItem(qpath)
            self._guide_line.setPen(
                QPen(QColor(251, 191, 36, 160), 1.0, Qt.PenStyle.DashLine)
            )
            self._scene.addItem(self._guide_line)
        else:
            self._guide_line.setPath(qpath)

    def _hide_guide_line(self) -> None:
        if self._guide_line is not None:
            self._scene.removeItem(self._guide_line)
            self._guide_line = None

    def get_selected_confirmed_bridges(self) -> List[Tuple[int, float]]:
        """Return [(bridge_index, width_px), ...] for all selected confirmed bridges."""
        result = []
        for b in self._bridge_items:
            if isinstance(b, _ConfirmedBridgeItem) and b.selected:
                idx = b.bridge_index
                if 0 <= idx < len(self._manual_bridges):
                    entry = self._manual_bridges[idx]
                    width_px = entry[2] if len(entry) > 2 else self._bridge_width_px
                    result.append((idx, width_px))
        return result

    def update_selected_bridges_width(self, width_px: float) -> None:
        """Resize all currently-selected confirmed bridges in-place."""
        indices = [b.bridge_index for b in self._bridge_items
                   if isinstance(b, _ConfirmedBridgeItem) and b.selected]
        for idx in indices:
            self.update_bridge_width(idx, width_px)

    def update_bridge_width(self, bridge_index: int, width_px: float) -> None:
        """Resize a confirmed bridge in-place without a full canvas reload."""
        if not (0 <= bridge_index < len(self._manual_bridges)):
            return
        entry = self._manual_bridges[bridge_index]
        pt1, pt2 = entry[0], entry[1]
        self._manual_bridges[bridge_index] = (pt1, pt2, width_px)
        for i, item in enumerate(self._bridge_items):
            if isinstance(item, _ConfirmedBridgeItem) and item.bridge_index == bridge_index:
                was_sel = item.selected
                self._scene.removeItem(item)
                new_item = _ConfirmedBridgeItem(pt1, pt2, bridge_index, width_px)
                new_item.set_sel(was_sel)
                self._scene.addItem(new_item)
                self._bridge_items[i] = new_item
                break

    # ── Hit detection ─────────────────────────────────────────────────────

    def _hit_any(self, scene_pos: QPointF) -> Optional[_AnyItem]:
        for item in self._scene.items(scene_pos):
            if isinstance(item, (_PathItem, _BridgeMarkerItem,
                                  _ConfirmedBridgeItem, _StagedBridgeItem)):
                return item
        return None
