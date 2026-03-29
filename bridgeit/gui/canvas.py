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

from bridgeit.gui.themes import current_theme
from bridgeit.pipeline.bridge import Bridge
from bridgeit.pipeline.trace import Path2D


# Mode is an enum (a named set of constants) for the two canvas modes.
# SELECT = normal click-to-select mode; BRIDGE = drawing a new bridge tab.
class Mode(Enum):
    SELECT = auto()   # user can click paths/bridges to select them
    BRIDGE = auto()   # user is placing bridge endpoints


# ── Visual constants ──────────────────────────────────────────────────────
# These QColor objects define what colour each type of item appears on screen.
# Having them as module-level constants means we change the colour in one place.
_COL_NORMAL      = QColor("#ffffff")   # white  — unselected path
_COL_PATH_SEL    = QColor("#f59e0b")   # amber  — selected path
_COL_HOVER       = QColor("#a78bfa")   # purple — hovered path/bridge
_COL_BRIDGE      = QColor("#22c55e")   # green  — auto bridge marker
_COL_BRIDGE_SEL  = QColor("#ef4444")   # red    — selected bridge
_COL_STAGED_SEL  = QColor("#f59e0b")   # amber  — selected staged bridge
_COL_PENDING     = QColor("#fbbf24")   # yellow — first bridge click dot
_COL_SNAP        = QColor("#ffffff")   # white  — snap indicator
def _canvas_bg() -> QColor:
    """Return the canvas background colour from the active theme (called lazily)."""
    return QColor(current_theme()["canvas_bg"])

# Stroke widths in scene pixels
_W_NORMAL    = 1.5   # normal (unselected) stroke width
_W_SELECTED  = 2.5   # thicker stroke when selected
_MARKER_R    = 6     # bridge endpoint dot radius (scene px)
_SNAP_R      = 5     # snap indicator dot radius (scene px)
_HIT_WIDTH   = 16.0  # invisible hit corridor around thin bridge lines so they're easier to click


# ── Module-level geometry helpers ─────────────────────────────────────────

def _closest_point_on_segment(
    px: float, py: float,   # the query point
    ax: float, ay: float,   # segment start
    bx: float, by: float,   # segment end
) -> Tuple[float, float]:
    """Return the point on segment AB that is closest to point P.

    Used by _snap_to_path() to snap the cursor to the nearest edge of any path.
    """
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy    # squared length of segment
    if denom < 1e-10:
        return ax, ay            # degenerate segment (zero length) — return start point
    # t is how far along AB the projection of P falls (clamped to [0,1])
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return ax + t * dx, ay + t * dy   # interpolate along the segment


def _compute_bridge_rect(
    pt1: Tuple[float, float],   # one end of the bridge centreline
    pt2: Tuple[float, float],   # other end of the bridge centreline
    width_px: float,            # how wide the rectangle should be
) -> Optional[List[Tuple[float, float]]]:
    """Build a rectangular outline for a bridge between two points.

    A bridge is drawn as a rectangle whose long axis runs pt1→pt2.
    The rectangle is width_px pixels wide, centred on the centreline.

    Returns 5 points (4 corners + repeated first point to close the shape),
    or None if the two endpoints are too close together to form a valid rect.
    """
    dx = pt2[0] - pt1[0]
    dy = pt2[1] - pt1[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None   # degenerate bridge — skip it
    ux, uy = dx / length, dy / length          # unit vector along the bridge direction
    perp_x, perp_y = -uy, ux                  # perpendicular unit vector (90° rotate)
    half = width_px / 2                        # half-width offset from centreline
    # Four corners of the rectangle, offset ±half perpendicular to the centreline
    a = (pt1[0] + perp_x * half, pt1[1] + perp_y * half)   # start-left
    b = (pt1[0] - perp_x * half, pt1[1] - perp_y * half)   # start-right
    c = (pt2[0] - perp_x * half, pt2[1] - perp_y * half)   # end-right
    d = (pt2[0] + perp_x * half, pt2[1] + perp_y * half)   # end-left
    return [a, b, c, d, a]   # repeat 'a' at the end to close the loop


def _constrain_to_45(
    origin: Tuple[float, float],
    pt: Tuple[float, float],
) -> Tuple[float, float]:
    """Snap pt to the nearest 45°-multiple direction from origin.

    When the user holds Shift while placing a bridge endpoint, we snap the
    angle to 0°, 45°, 90°, 135° etc. so they can easily make straight or
    diagonal bridges.  The distance from origin is preserved — only the
    angle is changed.
    """
    dx = pt[0] - origin[0]
    dy = pt[1] - origin[1]
    angle = math.atan2(dy, dx)                         # raw angle in radians
    # Round to nearest multiple of 45° (π/4 radians)
    snapped = round(angle / (math.pi / 4)) * (math.pi / 4)
    dist = math.hypot(dx, dy)                          # keep the same distance
    return (origin[0] + dist * math.cos(snapped),
            origin[1] + dist * math.sin(snapped))


# ── Path item ─────────────────────────────────────────────────────────────

class _PathItem(QGraphicsPathItem):
    """A single clickable contour path shown on the canvas.

    Each cut path from the pipeline becomes one _PathItem.
    Clicking it in SELECT mode toggles its selected state (amber highlight).
    The raw (x, y) point list is stored in _path_2d so the snap system
    can find the closest point on any edge during bridge placement.
    """

    def __init__(self, path_2d: Path2D, index: int) -> None:
        self._path_2d = path_2d   # raw points — used by _snap_to_path()

        # Convert our list of (x, y) tuples into a Qt painter path
        qpath = QPainterPath()
        if path_2d:
            qpath.moveTo(path_2d[0][0], path_2d[0][1])
            for x, y in path_2d[1:]:
                qpath.lineTo(x, y)
            qpath.closeSubpath()   # connect the last point back to the first

        super().__init__(qpath)
        self.path_index = index   # original index in the pipeline path list
        self._sel = False         # selection state
        self.setAcceptHoverEvents(True)   # needed for hover highlight to work
        # Nearly-transparent fill so the path has a clickable interior area
        self.setBrush(QBrush(QColor(255, 255, 255, 1)))
        self._refresh_pen()

    def _refresh_pen(self) -> None:
        """Update the stroke colour and width to match current selection state."""
        c = _COL_PATH_SEL if self._sel else _COL_NORMAL
        self.setPen(QPen(c, _W_SELECTED if self._sel else _W_NORMAL))

    def toggle(self) -> bool:
        """Flip the selection state and repaint. Returns the new state."""
        self._sel = not self._sel
        self._refresh_pen()
        return self._sel

    def set_sel(self, v: bool) -> None:
        """Set selection state directly (used by rubber-band and clear_selection)."""
        self._sel = v
        self._refresh_pen()

    @property
    def selected(self) -> bool:
        return self._sel

    def hoverEnterEvent(self, event) -> None:
        """Show purple highlight and pointer cursor when hovering over this path."""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if not self._sel:   # don't override the amber selection colour
            self.setPen(QPen(_COL_HOVER, _W_NORMAL))

    def hoverLeaveEvent(self, event) -> None:
        """Restore normal/selected colour and cursor when cursor leaves."""
        self.unsetCursor()
        self._refresh_pen()


# ── Staged bridge item (pending confirmation) ─────────────────────────────

class _StagedBridgeItem(QGraphicsPathItem):
    """Preview rect for a bridge that has been placed but not yet confirmed.

    After the user clicks two endpoints in bridge mode, a _StagedBridgeItem
    appears immediately as a translucent green rectangle so they can see
    what the bridge will look like.  It stays "staged" until they press Enter
    or click "Confirm Bridges" — only then does it become a real cut path.

    Visually: green outline + subtle green tint fill.
    Selectable: click to highlight amber; Delete removes it from staging.
    """

    def __init__(
        self,
        pt1: Tuple[float, float],   # first bridge endpoint
        pt2: Tuple[float, float],   # second bridge endpoint
        staged_index: int,          # position in the staged list (for deletion)
        width_px: float,            # bridge width in pixels
    ) -> None:
        self.bridge_type  = "staged"    # identifies this as a staged (unconfirmed) bridge
        self.staged_index = staged_index
        self._sel = False

        # Build the rectangular outline path for this bridge
        rect_pts = _compute_bridge_rect(pt1, pt2, width_px)
        qpath = QPainterPath()
        if rect_pts:
            qpath.moveTo(rect_pts[0][0], rect_pts[0][1])
            for x, y in rect_pts[1:]:
                qpath.lineTo(x, y)
            qpath.closeSubpath()

        super().__init__(qpath)
        self.setAcceptHoverEvents(True)
        # Subtle green tint fill (alpha=30 ≈ 12% opacity) so the path underneath shows through
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
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if not self._sel:
            self.setPen(QPen(_COL_HOVER, _W_NORMAL))

    def hoverLeaveEvent(self, event) -> None:
        self.unsetCursor()
        self._refresh_pen()


# ── Confirmed manual bridge item ──────────────────────────────────────────

class _ConfirmedBridgeItem(QGraphicsPathItem):
    """A confirmed manual bridge shown as its actual white cut rectangle.

    Once the user confirms staged bridges, each _StagedBridgeItem is replaced
    by a _ConfirmedBridgeItem.  This is the final visual — a solid white
    rectangle showing exactly the material that will be left un-cut, connecting
    the island to the main shape.

    Clicking a confirmed bridge in SELECT mode highlights it amber and lets
    the user adjust its width via the Bridge Width control in the settings panel.
    """

    def __init__(
        self,
        pt1: Tuple[float, float],   # first bridge endpoint
        pt2: Tuple[float, float],   # second bridge endpoint
        bridge_index: int,          # index into _manual_bridges list
        width_px: float,            # bridge width in pixels
    ) -> None:
        self.bridge_type  = "manual"    # identifies this as a confirmed manual bridge
        self.bridge_index = bridge_index
        self._sel = False

        # Build the rectangular outline for this bridge
        rect_pts = _compute_bridge_rect(pt1, pt2, width_px)
        qpath = QPainterPath()
        if rect_pts:
            qpath.moveTo(rect_pts[0][0], rect_pts[0][1])
            for x, y in rect_pts[1:]:
                qpath.lineTo(x, y)
            qpath.closeSubpath()

        super().__init__(qpath)
        self.setAcceptHoverEvents(True)
        # Nearly-transparent fill so the rectangle has a clickable interior
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
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if not self._sel:
            self.setPen(QPen(_COL_HOVER, _W_NORMAL))

    def hoverLeaveEvent(self, event) -> None:
        self.unsetCursor()
        self._refresh_pen()


# ── Auto bridge marker item ───────────────────────────────────────────────

class _BridgeMarkerItem(QGraphicsPathItem):
    """A selectable auto-generated bridge marker — dashed green line + endpoint dots.

    Auto bridges are generated by the pipeline (bridge.py) and shown as a dashed
    green line between the island contact point and the mainland contact point.
    The actual cut geometry is baked into the SVG paths — this item is purely
    a visual indicator so the user can see where bridges were placed.

    The item's shape (for click detection) is an invisible wide stroke around the
    dashed line plus circles at the endpoints, making it much easier to click
    than the thin visible line would be.
    """

    def __init__(
        self,
        pt1: Tuple[float, float],   # island-side endpoint
        pt2: Tuple[float, float],   # mainland-side endpoint
        bridge_type: str,           # "auto" or "manual"
        bridge_index: int,          # index in the bridge list
    ) -> None:
        self._pt1 = QPointF(pt1[0], pt1[1])
        self._pt2 = QPointF(pt2[0], pt2[1])

        # Build the hit-test shape: a wide invisible stroke along the line
        # plus enlarged circles at each endpoint.  This is what Qt uses for
        # click detection — making it wider than the visible line so it's
        # easy to click on even a thin bridge.
        line = QPainterPath()
        line.moveTo(self._pt1)
        line.lineTo(self._pt2)
        stroker = QPainterPathStroker()
        stroker.setWidth(_HIT_WIDTH)   # invisible corridor around the line
        hit = stroker.createStroke(line)
        r = float(_MARKER_R + 4)       # slightly larger than the visible dot
        hit.addEllipse(self._pt1, r, r)
        hit.addEllipse(self._pt2, r, r)

        # Pass the hit shape to the parent — this becomes the item's bounding shape
        super().__init__(hit)

        self.bridge_type  = bridge_type
        self.bridge_index = bridge_index
        self._sel   = False   # selection state
        self._hover = False   # hover state

        # Make the hit shape invisible — we draw the visible dashed line in paint()
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(QColor(0, 0, 0, 0)))  # fully transparent
        self.setAcceptHoverEvents(True)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        """Custom paint: draw a dashed line and two filled endpoint dots.

        Qt calls this method whenever the item needs to be redrawn.
        The colour changes based on selection/hover state.
        """
        # Pick colour: red if selected, purple if hovered, green otherwise
        col = _COL_BRIDGE_SEL if self._sel else (_COL_HOVER if self._hover else _COL_BRIDGE)
        # Draw the dashed connecting line
        painter.setPen(QPen(col, 2.0, Qt.PenStyle.DashLine))
        painter.drawLine(self._pt1, self._pt2)
        # Draw solid filled circles at each endpoint
        painter.setPen(QPen(Qt.PenStyle.NoPen))   # no outline on the dots
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
    """Zoomable, pannable canvas for selecting and editing cut paths.

    QGraphicsView is a Qt widget that displays a QGraphicsScene.  Think of the
    scene as an infinite 2D canvas holding all the path/bridge items, and the
    view as a window into that canvas that can be zoomed and panned.

    This class handles all mouse/keyboard interaction and maintains the full
    state of which paths are excluded, which bridges are manual/auto/staged.
    """

    # Qt signals — other objects connect to these to be notified of events.
    paths_modified    = pyqtSignal()     # emitted when paths deleted or bridge confirmed
    mode_changed      = pyqtSignal(str)  # "select"|"bridge"|"bridge_pt2"|"bridge_confirm"
    selection_changed = pyqtSignal()     # emitted after any click that may change selection

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        # The scene holds all the graphic items (paths, bridges, dots, etc.)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # Antialiasing smooths jagged diagonal lines
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)   # we handle middle-drag ourselves

        # AnchorUnderMouse: when zooming, keep the point under the cursor fixed
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(_canvas_bg()))

        # Hide scroll bars — we zoom/pan via mouse wheel and middle-drag
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # WheelFocus: the canvas grabs keyboard focus when the scroll wheel is used
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

        self._mode: Mode = Mode.SELECT   # start in select mode

        # ── Path state ────────────────────────────────────────────────────
        self._items: List[_PathItem] = []      # all visible path items on canvas
        self._excluded: Set[int] = set()       # path indices the user has deleted

        # ── Bridge state ──────────────────────────────────────────────────
        # All confirmed bridge items (both auto-generated and manual).
        self._bridge_items: List[_AnyBridgeItem] = []
        # Indices of auto bridges the user deleted (so we can preserve the deletion
        # across canvas reloads triggered by settings changes).
        self._deleted_auto_bridges: Set[int] = set()
        # Manual bridges that have been confirmed: list of (pt1, pt2, width_px) tuples.
        self._manual_bridges: List[Tuple] = []

        # ── Staged (not-yet-confirmed) bridges ────────────────────────────
        # _staged_data stores the raw coordinates; _staged_items are the green rectangles.
        self._staged_data: List[Tuple[Tuple, Tuple]] = []   # [(pt1, pt2), ...]
        self._staged_items: List[_StagedBridgeItem] = []

        # ── Active first-point placement ──────────────────────────────────
        # When the user clicks in bridge mode, _bridge_pt1 holds the first endpoint
        # until they click again to complete the bridge.
        self._bridge_pt1: Optional[Tuple[float, float]] = None
        # Yellow dot shown at the first click point while waiting for the second click
        self._pending_dot: Optional[QGraphicsEllipseItem] = None

        # ── Hover visual aids ─────────────────────────────────────────────
        # Small white circle that snaps to the nearest path edge as the cursor moves
        self._snap_dot: Optional[QGraphicsEllipseItem] = None
        # Dashed yellow line shown when Shift is held (straight-bridge guide)
        self._guide_line: Optional[QGraphicsPathItem] = None

        # Bridge width in pixels, synced from the Bridge Width control before entering bridge mode
        self._bridge_width_px: float = 5.0

        # ── Rubber-band selection ─────────────────────────────────────────
        self._rubber_band: Optional[QRubberBand] = None   # the selection rectangle widget
        self._rubber_origin: Optional[QRect] = None       # where the drag started

        # Flag: has the scene been fitted to the view yet?
        # We fit once on first load, then let the user zoom freely.
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

    def update_theme(self) -> None:
        """Re-apply the active theme colour to the canvas background.

        Called by MainWindow._apply_theme() after the user cycles themes so
        the canvas background matches the new palette.
        """
        self.setBackgroundBrush(QBrush(_canvas_bg()))
        self._scene.update()

    def load(
        self,
        paths: List[Path2D],
        auto_bridges: List[Bridge],
        excluded: Optional[Set[int]] = None,
        manual_bridges: Optional[List[Tuple]] = None,
        deleted_auto_bridges: Optional[Set[int]] = None,
    ) -> None:
        """Rebuild the canvas from a fresh pipeline result.

        Called after each pipeline run and after the user confirms/deletes bridges.
        Clears the old scene completely and redraws everything from scratch.

        Args:
            paths:                All cut paths from the pipeline.
            auto_bridges:         Auto-generated bridges from bridge.py.
            excluded:             Path indices to hide (user-deleted paths).
            manual_bridges:       Confirmed manual bridges as (pt1, pt2, width_px) tuples.
            deleted_auto_bridges: Indices of auto bridges the user deleted.
        """
        # Wipe everything from the scene and clear all our tracking lists
        self._scene.clear()
        self._items.clear()
        self._bridge_items.clear()
        self._staged_items.clear()
        self._staged_data.clear()
        # Reset in-flight bridge placement state
        self._pending_dot = None
        self._snap_dot = None
        self._guide_line = None
        self._bridge_pt1 = None

        # Update state from the caller — only overwrite if new values were provided
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
        """Delete all currently selected items.

        Three categories of items can be deleted:
          1. Staged bridges (unconfirmed) — removed immediately, no pipeline reload.
          2. Paths — added to _excluded; triggers a canvas reload via paths_modified.
          3. Confirmed bridges — removed from _manual_bridges or _deleted_auto_bridges;
             triggers a canvas reload via paths_modified.
        """
        changed = False

        # ── 1. Delete selected staged bridges ─────────────────────────────
        # Work backwards through the list so indices stay valid as we remove items.
        staged_sel = [i for i, s in enumerate(self._staged_items) if s.selected]
        for i in reversed(staged_sel):
            self._scene.removeItem(self._staged_items[i])
            self._staged_items.pop(i)
            self._staged_data.pop(i)
            changed = True
        # Fix up staged_index on remaining items so they stay in sync with the list
        for j, s in enumerate(self._staged_items):
            s.staged_index = j
        if staged_sel:
            # Staged changes don't need a full pipeline reload — just update the toolbar hint
            self._emit_mode_hint()
            return

        # ── 2. Delete selected paths ──────────────────────────────────────
        to_exclude = {item.path_index for item in self._items if item.selected}
        if to_exclude:
            self._excluded |= to_exclude   # add to the exclusion set
            changed = True

        # ── 3. Delete selected confirmed bridges ──────────────────────────
        for bitem in list(self._bridge_items):
            if bitem.selected:
                if bitem.bridge_type == "manual":
                    idx = bitem.bridge_index
                    if 0 <= idx < len(self._manual_bridges):
                        self._manual_bridges.pop(idx)
                        # Fix up bridge_index on all later manual bridges
                        for other in self._bridge_items:
                            if other.bridge_type == "manual" and other.bridge_index > idx:
                                other.bridge_index -= 1
                elif bitem.bridge_type == "auto":
                    # Auto bridges can't be fully removed from the pipeline result,
                    # so we track deleted ones by index and skip them on next reload
                    self._deleted_auto_bridges.add(bitem.bridge_index)
                changed = True

        if changed:
            # Signal the mainwindow to reload the canvas with updated state
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
        """Handle mouse button presses for pan, bridge placement, and selection."""
        # ── Middle-click: start panning ───────────────────────────────────
        if event.button() == Qt.MouseButton.MiddleButton:
            # Qt's ScrollHandDrag mode handles panning natively, but it only
            # activates on left-click.  We fake a left-click event so Qt's
            # built-in panning code kicks in when the middle button is pressed.
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

        # ── Bridge mode: place endpoints ──────────────────────────────────
        if self._mode == Mode.BRIDGE:
            # Convert widget pixel coordinates to scene coordinates
            scene_pos = self.mapToScene(event.position().toPoint())
            pt = (float(scene_pos.x()), float(scene_pos.y()))
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._bridge_click(pt, shift)
            event.accept()
            return

        # ── SELECT mode: click to select / start rubber-band ──────────────
        scene_pos = self.mapToScene(event.position().toPoint())
        hit = self._hit_any(scene_pos)   # did we click an item?

        if hit:
            # Shift or Ctrl = add to existing selection; plain click = replace selection
            multi = bool(event.modifiers() & (
                Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
            ))
            if not multi:
                if not hit.selected:
                    self.clear_selection()   # deselect everything else first
            hit.toggle()   # flip the clicked item's selection state
            self.selection_changed.emit()
        else:
            # Clicked empty space — deselect all (unless modifier held) and start rubber-band
            if not (event.modifiers() & (
                Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
            )):
                self.clear_selection()
                self.selection_changed.emit()
            # Start the rubber-band selection rectangle
            origin = event.position().toPoint()
            self._rubber_origin = origin
            self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
            self._rubber_band.setGeometry(QRect(origin, QSize()))
            self._rubber_band.show()

        event.accept()

    def mouseMoveEvent(self, event) -> None:
        """Update snap dot / guide line in bridge mode; resize rubber-band in select mode."""
        if self._mode == Mode.BRIDGE:
            scene_pos = self.mapToScene(event.position().toPoint())
            pt = (float(scene_pos.x()), float(scene_pos.y()))
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

            if shift and self._bridge_pt1 is not None:
                # Shift held after placing first point: snap to 45° multiples and
                # show a dashed guide line from pt1 to the constrained position
                constrained = _constrain_to_45(self._bridge_pt1, pt)
                snapped = self._snap_to_path(constrained)
                self._show_guide_line(self._bridge_pt1, snapped)
            else:
                self._hide_guide_line()
                snapped = self._snap_to_path(pt)   # snap cursor to nearest path edge

            self._update_snap_dot(snapped)   # move the white snap dot to the snapped position
        else:
            # In SELECT mode there are no bridge aids to show
            self._hide_snap_dot()
            self._hide_guide_line()

        # If a rubber-band drag is in progress, resize it to follow the cursor
        if self._rubber_band is not None and self._rubber_origin is not None:
            self._rubber_band.setGeometry(
                QRect(self._rubber_origin, event.position().toPoint()).normalized()
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """End a middle-drag pan or finalize a rubber-band selection."""
        # End panning — restore the default drag mode (no drag)
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)

        # If we were rubber-banding, select everything inside the final rectangle
        if self._rubber_band is not None:
            vp_rect = self._rubber_band.geometry()   # final rectangle in viewport pixels
            self._rubber_band.hide()
            self._rubber_band = None
            self._rubber_origin = None

            # Convert the viewport rectangle to scene coordinates for hit testing
            scene_rect = self.mapToScene(vp_rect).boundingRect()
            all_items: List[_AnyItem] = self._items + self._bridge_items + self._staged_items
            for item in all_items:
                # mapToScene converts the item's bounding box to scene coordinates
                if scene_rect.intersects(item.mapToScene(item.boundingRect()).boundingRect()):
                    item.set_sel(True)   # select any item that overlaps the rubber-band
            self.selection_changed.emit()

        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard shortcuts for delete, confirm, and escape."""
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            # Delete / Backspace: remove selected items
            self.delete_selected()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Enter: confirm all staged bridges (only works in bridge mode)
            if self._mode == Mode.BRIDGE and self._staged_data:
                self.confirm_staged_bridges()
        elif event.key() == Qt.Key.Key_Escape:
            if self._mode == Mode.BRIDGE:
                if self._bridge_pt1 is not None:
                    # First Escape: cancel the pending first point (yellow dot)
                    self._cancel_pt1()
                    self._emit_mode_hint()
                elif self._staged_data:
                    # Second Escape: discard all staged bridges
                    self._cancel_staged()
                    self._emit_mode_hint()
                else:
                    # Third Escape (nothing pending): exit bridge mode entirely
                    self.set_mode(Mode.SELECT)
            else:
                # In SELECT mode, Escape just clears the selection
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
        """Handle a click in bridge mode.

        First click: snap to the nearest path edge, place a yellow dot at that point.
        Second click: snap (and optionally constrain to 45°), create a staged bridge
                      rectangle between the two points and reset for the next bridge.
        """
        if self._bridge_pt1 is None:
            # ── First click: record point A ────────────────────────────────
            # Snap the raw cursor position to the nearest path segment
            snapped = self._snap_to_path(pt)
            self._bridge_pt1 = snapped

            # Draw a yellow dot at the snapped position to confirm the first click
            r = _MARKER_R
            self._pending_dot = self._scene.addEllipse(
                snapped[0] - r, snapped[1] - r, r * 2, r * 2,
                QPen(Qt.PenStyle.NoPen), QBrush(_COL_PENDING),
            )
            self.mode_changed.emit("bridge_pt2")   # update toolbar hint
        else:
            # ── Second click: record point B and stage the bridge ──────────
            if shift:
                # Shift held: constrain to 45° multiples from the first point
                constrained = _constrain_to_45(self._bridge_pt1, pt)
                snapped = self._snap_to_path(constrained)
            else:
                snapped = self._snap_to_path(pt)

            pt1 = self._bridge_pt1
            self._cancel_pt1()   # remove the yellow dot; clear _bridge_pt1

            # Add this bridge to the staged list and draw its preview rectangle
            idx = len(self._staged_data)
            self._staged_data.append((pt1, snapped))
            staged_item = _StagedBridgeItem(pt1, snapped, idx, self._bridge_width_px)
            self._scene.addItem(staged_item)
            self._staged_items.append(staged_item)

            self._hide_guide_line()
            self._emit_mode_hint()   # update toolbar to show "Confirm Bridges"

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
        """Return the closest point on any visible path segment to pt.

        Iterates over every segment of every path item and finds the globally
        nearest point.  This is what makes bridge endpoints snap to the cut lines
        instead of floating in empty space (which would create broken bridges).

        We compare squared distances to avoid calling sqrt in the inner loop —
        since we're only comparing distances, we don't need the actual value.
        """
        best_dist_sq = float("inf")
        best_pt = pt   # fallback: return the original point if no paths exist
        px, py = pt
        for item in self._items:
            pts = item._path_2d   # raw (x, y) list for this path
            n = len(pts)
            if n < 2:
                continue
            for j in range(n):
                ax, ay = pts[j]
                bx, by = pts[(j + 1) % n]   # % n wraps around to close the loop
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
        """Return [(bridge_index, width_px), ...] for all selected confirmed bridges.

        Called by the mainwindow after every selection change to check whether
        any confirmed manual bridges are selected.  If they are, the controls
        panel shows their width and lets the user adjust it.
        """
        result = []
        for b in self._bridge_items:
            if isinstance(b, _ConfirmedBridgeItem) and b.selected:
                idx = b.bridge_index
                if 0 <= idx < len(self._manual_bridges):
                    entry = self._manual_bridges[idx]
                    # entry[2] is the stored width; fall back to current default if missing
                    width_px = entry[2] if len(entry) > 2 else self._bridge_width_px
                    result.append((idx, width_px))
        return result

    def update_selected_bridges_width(self, width_px: float) -> None:
        """Resize all currently-selected confirmed bridges to a new width in-place.

        Called when the user drags the Bridge Width slider while bridges are selected.
        Collects all selected bridge indices then resizes each one.
        """
        indices = [b.bridge_index for b in self._bridge_items
                   if isinstance(b, _ConfirmedBridgeItem) and b.selected]
        for idx in indices:
            self.update_bridge_width(idx, width_px)

    def update_bridge_width(self, bridge_index: int, width_px: float) -> None:
        """Resize a single confirmed bridge in-place without a full canvas reload.

        Updates the stored width in _manual_bridges, then replaces the old
        _ConfirmedBridgeItem on the scene with a freshly-built one at the new width.
        Preserves the selection state so the bridge stays highlighted after resizing.
        """
        if not (0 <= bridge_index < len(self._manual_bridges)):
            return   # guard against stale index

        entry = self._manual_bridges[bridge_index]
        pt1, pt2 = entry[0], entry[1]

        # Update the stored width
        self._manual_bridges[bridge_index] = (pt1, pt2, width_px)

        # Find the corresponding scene item and swap it for a resized one
        for i, item in enumerate(self._bridge_items):
            if isinstance(item, _ConfirmedBridgeItem) and item.bridge_index == bridge_index:
                was_sel = item.selected     # remember if it was selected
                self._scene.removeItem(item)
                new_item = _ConfirmedBridgeItem(pt1, pt2, bridge_index, width_px)
                new_item.set_sel(was_sel)   # restore selection state
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
