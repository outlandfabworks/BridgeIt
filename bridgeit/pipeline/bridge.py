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

# Shapely provides geometric types and algorithms.
# LineString = a sequence of connected line segments (an open path).
# Point = a single (x, y) coordinate.
# nearest_points = a Shapely function that finds the closest pair of points
#   between two geometries.
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points

from bridgeit.config import DEFAULT_BRIDGE_WIDTH_MM, DEFAULT_DPI
from bridgeit.pipeline.analyze import AnalysisResult, Island
from bridgeit.pipeline.trace import Path2D


@dataclass
class Bridge:
    """Describes a single bridge connection."""
    island_idx: int                          # which island this bridge belongs to
    target_idx: int                          # index of the path we're bridging TO
    island_pt: Tuple[float, float]           # point on the island where bridge starts
    target_pt: Tuple[float, float]           # point on the target where bridge ends
    width_px: float                          # width of the bridge in pixels


@dataclass
class BridgeResult:
    """Output of bridge stage — paths ready for SVG export."""
    paths: List[Path2D]       # modified paths (islands now bridged into one outline)
    bridges: List[Bridge]     # metadata for debugging / UI display (dashed markers)
    image_size: Tuple[int, int]


def mm_to_px(mm: float, dpi: float = DEFAULT_DPI) -> float:
    # Convert millimetres to pixels using the DPI (dots per inch) setting.
    # 1 inch = 25.4 mm, so mm / 25.4 gives inches, then × dpi gives pixels.
    return mm * dpi / 25.4


def px_to_mm(px: float, dpi: float = DEFAULT_DPI) -> float:
    # Inverse of mm_to_px — used when reading back a pixel width from the canvas
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
    # Convert the user-facing mm value to pixels for all geometry calculations
    bridge_px = mm_to_px(bridge_width_mm, dpi)

    # Create a mutable copy of all paths — we will modify island paths in-place
    # to insert bridge geometry without altering the originals
    paths = [list(p) for p in analysis.all_paths]
    bridges: List[Bridge] = []

    # If no islands were found, there's nothing to bridge — return paths as-is
    if not analysis.islands:
        return BridgeResult(paths=paths, bridges=[], image_size=analysis.image_size)

    # Process each island in turn, finding its nearest neighbour and inserting a bridge
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
    # Convert the island polygon's outline to a Shapely LineString so we can
    # use nearest_points() to find the closest point pair efficiently.
    island_line = LineString(island.path)

    # Track the best (closest) connection point found so far
    best_dist = math.inf
    best_island_pt: Optional[Tuple[float, float]] = None
    best_target_pt: Optional[Tuple[float, float]] = None
    best_target_idx: Optional[int] = None

    # Compare the island against every other path to find the nearest target.
    # We skip the island itself (same index) and degenerate single-point paths.
    for i, path in enumerate(paths):
        if i == island.index:
            continue
        if len(path) < 2:
            continue

        # Convert the candidate target path to a Shapely LineString
        target_line = LineString(path)
        try:
            # nearest_points returns a pair: the point on island_line that is
            # closest to target_line, and vice versa.
            p1, p2 = nearest_points(island_line, target_line)
            dist = p1.distance(p2)
        except Exception:
            # Malformed geometries can cause Shapely errors; skip them safely
            continue

        # Keep track of whichever target gives the shortest bridge length
        if dist < best_dist:
            best_dist = dist
            best_island_pt = (p1.x, p1.y)
            best_target_pt = (p2.x, p2.y)
            best_target_idx = i

    # If no valid target was found (e.g. only one path exists), skip this island
    if best_island_pt is None:
        return None

    # Mutate the island path to splice in the bridge rectangle points
    _insert_bridge_into_path(
        paths[island.index],
        best_island_pt,
        best_target_pt,
        bridge_px,
    )

    # Return bridge metadata so the UI can draw the dashed-line marker
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
    # Direction vector from island → target (the "spine" of the bridge)
    dx = target_pt[0] - island_pt[0]
    dy = target_pt[1] - island_pt[1]
    length = math.hypot(dx, dy)

    # If the two points are at the same location, no bridge can be drawn
    if length < 1e-6:
        return

    # Normalise the direction vector to length 1 (a unit vector)
    ux, uy = dx / length, dy / length

    # The perpendicular unit vector is used to offset the bridge sideways,
    # giving it its width. Rotating (ux, uy) by 90° gives (-uy, ux).
    px, py = -uy, ux

    # half_w is the distance from the bridge centreline to each edge
    half_w = bridge_px / 2.0

    # Compute the four corners of the bridge rectangle:
    # a, b are on the island side; c, d are on the target side
    a = (island_pt[0] + px * half_w, island_pt[1] + py * half_w)
    b = (island_pt[0] - px * half_w, island_pt[1] - py * half_w)
    c = (target_pt[0] - px * half_w, target_pt[1] - py * half_w)
    d = (target_pt[0] + px * half_w, target_pt[1] + py * half_w)

    # Find the index in the path where the bridge should be spliced in.
    # We insert near the point on the island path closest to island_pt.
    insert_idx = _find_nearest_segment(path, island_pt)

    # Splice the bridge points into the path at that index.
    # The order a → d → target_pt → c → b traces the bridge rectangle
    # and back, keeping the cut path as one continuous closed loop.
    bridge_pts = [a, d, target_pt, c, b]
    path[insert_idx:insert_idx] = bridge_pts


def _find_nearest_segment(path: Path2D, pt: Tuple[float, float]) -> int:
    """Return the index of the path point closest to pt."""
    min_dist = math.inf
    best_idx = 0
    px, py = pt

    # Linear search through all path vertices — straightforward and fast
    # enough for the path sizes we deal with (typically hundreds of points)
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
    # This function is only called when running this module directly.
    # It runs trace→analyze→bridge and reports what was generated.
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
