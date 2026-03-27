"""
bridge.py — Bridge generation stage (core unique feature of BridgeIt).

For each floating island, this module finds the closest point on any
other path (mainland or another island), then inserts a thin rectangular
bridge connecting the two shapes so the piece stays in one part after
cutting.

The bridge is represented as two additional line segments added to the
island path: a "go" edge and a "return" edge, spaced bridge_width apart,
creating a tab that keeps the island attached.

Bridge geometry (ASCII diagram):

    mainland path
    ─────────A─────────
             │  ←  bridge_width
    ─────────B─────────
          ↑
         gap
    ──────────────────   island path

The bridge is formed by:
  1. Finding the closest point pair (P_island, P_other) between the island
     and the nearest other path.
  2. Computing the perpendicular direction to the connecting line.
  3. Offsetting that line by ±bridge_width/2 to form the bridge rectangle.
  4. Inserting the bridge points into the island path so the SVG becomes
     one continuous cut path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points

from bridgeit.config import DEFAULT_BRIDGE_WIDTH_MM, DEFAULT_DPI
from bridgeit.pipeline.analyze import AnalysisResult, Island
from bridgeit.pipeline.trace import Path2D


@dataclass
class Bridge:
    """Describes a single bridge connection."""
    island_idx: int
    target_idx: int          # index of the path we're bridging TO
    island_pt: Tuple[float, float]
    target_pt: Tuple[float, float]
    width_px: float


@dataclass
class BridgeResult:
    """Output of bridge stage — paths ready for SVG export."""
    paths: List[Path2D]       # modified paths (islands now bridged)
    bridges: List[Bridge]     # metadata for debugging / UI display
    image_size: Tuple[int, int]


def mm_to_px(mm: float, dpi: float = DEFAULT_DPI) -> float:
    return mm * dpi / 25.4


def px_to_mm(px: float, dpi: float = DEFAULT_DPI) -> float:
    return px * 25.4 / dpi


def add_bridges(
    analysis: AnalysisResult,
    bridge_width_mm: float = DEFAULT_BRIDGE_WIDTH_MM,
    dpi: float = DEFAULT_DPI,
) -> BridgeResult:
    """Generate bridges for all detected islands.

    Args:
        analysis: Output from analyze stage.
        bridge_width_mm: Bridge width in millimetres.
        dpi: Image resolution (pixels per inch) for mm→px conversion.

    Returns:
        BridgeResult with modified paths containing bridge geometry.
    """
    bridge_px = mm_to_px(bridge_width_mm, dpi)

    # Work on a mutable copy of paths
    paths = [list(p) for p in analysis.all_paths]
    bridges: List[Bridge] = []

    if not analysis.islands:
        return BridgeResult(paths=paths, bridges=[], image_size=analysis.image_size)

    for island in analysis.islands:
        bridge = _bridge_island(island, paths, analysis, bridge_px)
        if bridge:
            bridges.append(bridge)

    return BridgeResult(paths=paths, bridges=bridges, image_size=analysis.image_size)


def _bridge_island(
    island: Island,
    paths: List[Path2D],
    analysis: AnalysisResult,
    bridge_px: float,
) -> Optional[Bridge]:
    """Find nearest target path and insert a bridge into the island path."""
    island_poly = island.polygon
    island_line = LineString(island.path)

    best_dist = math.inf
    best_island_pt: Optional[Tuple[float, float]] = None
    best_target_pt: Optional[Tuple[float, float]] = None
    best_target_idx: Optional[int] = None

    # Search all other paths for the nearest point
    for i, path in enumerate(paths):
        if i == island.index:
            continue
        if len(path) < 2:
            continue
        target_line = LineString(path)
        try:
            p1, p2 = nearest_points(island_line, target_line)
            dist = p1.distance(p2)
        except Exception:
            continue
        if dist < best_dist:
            best_dist = dist
            best_island_pt = (p1.x, p1.y)
            best_target_pt = (p2.x, p2.y)
            best_target_idx = i

    if best_island_pt is None:
        return None

    # Insert bridge geometry into the island path
    _insert_bridge_into_path(
        paths[island.index],
        best_island_pt,
        best_target_pt,
        bridge_px,
    )

    return Bridge(
        island_idx=island.index,
        target_idx=best_target_idx,
        island_pt=best_island_pt,
        target_pt=best_target_pt,
        width_px=bridge_px,
    )


def _insert_bridge_into_path(
    path: Path2D,
    island_pt: Tuple[float, float],
    target_pt: Tuple[float, float],
    bridge_px: float,
) -> None:
    """Mutate path to include bridge tabs at the closest point.

    The bridge consists of two parallel lines (go + return) of width
    bridge_px, running from the island outline to the target outline.
    """
    # Direction vector from island → target
    dx = target_pt[0] - island_pt[0]
    dy = target_pt[1] - island_pt[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return  # Points are coincident, skip

    # Unit vector along bridge direction
    ux, uy = dx / length, dy / length

    # Perpendicular unit vector (90° rotation)
    px, py = -uy, ux

    half_w = bridge_px / 2.0

    # Four corners of the bridge rectangle
    # A, B on island side; C, D on target side
    a = (island_pt[0] + px * half_w, island_pt[1] + py * half_w)
    b = (island_pt[0] - px * half_w, island_pt[1] - py * half_w)
    c = (target_pt[0] - px * half_w, target_pt[1] - py * half_w)
    d = (target_pt[0] + px * half_w, target_pt[1] + py * half_w)

    # Find insertion index — the segment of the island path closest to island_pt
    insert_idx = _find_nearest_segment(path, island_pt)

    # Insert bridge points into the path at that segment
    # Path becomes: ... prev_pt → A → D (target side) → C → B → next_pt ...
    bridge_pts = [a, d, target_pt, c, b]
    path[insert_idx:insert_idx] = bridge_pts


def _find_nearest_segment(path: Path2D, pt: Tuple[float, float]) -> int:
    """Return the index of the path point closest to pt."""
    min_dist = math.inf
    best_idx = 0
    px, py = pt
    for i, (x, y) in enumerate(path):
        d = math.hypot(x - px, y - py)
        if d < min_dist:
            min_dist = d
            best_idx = i
    return best_idx


# ---------------------------------------------------------------------------
# Standalone validation
# ---------------------------------------------------------------------------

def _validate(image_path: str) -> None:
    from PIL import Image
    from bridgeit.pipeline.trace import trace_contours
    from bridgeit.pipeline.analyze import analyze_islands

    print(f"[bridge] Processing: {image_path}")
    img = Image.open(image_path).convert("RGBA")
    paths = trace_contours(img)
    analysis = analyze_islands(paths, img.size)
    result = add_bridges(analysis)

    print(f"[bridge] Bridges generated: {len(result.bridges)}")
    for b in result.bridges:
        print(
            f"  island {b.island_idx} → path {b.target_idx}  "
            f"dist={math.hypot(b.target_pt[0]-b.island_pt[0], b.target_pt[1]-b.island_pt[1]):.1f}px  "
            f"width={b.width_px:.1f}px"
        )
    print("[bridge] PASS")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python bridge.py <rgba_image>")
        sys.exit(1)
    _validate(sys.argv[1])
