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
    dpi: float = DEFAULT_DPI  # resolution used for mm↔px — needed for physical SVG units
    already_smoothed: bool = False  # skip smoothing in export when paths were pre-smoothed


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
        return BridgeResult(paths=paths, bridges=[], image_size=analysis.image_size, dpi=dpi)

    # Process each island in turn, finding its nearest neighbour and inserting a bridge
    for island in analysis.islands:
        bridge = _bridge_island(island, paths, analysis, bridge_px)
        if bridge:
            bridges.append(bridge)

    return BridgeResult(paths=paths, bridges=bridges, image_size=analysis.image_size, dpi=dpi)


def _bridge_island(
    island: Island,
    paths: List[Path2D],
    analysis: AnalysisResult,
    bridge_px: float,
) -> Optional[Bridge]:
    """Find the correct target path and insert a bridge into the island path.

    Strategy — prefer containment over proximity:

      1. Find every path whose polygon contains the island's centroid.
         These are "parent" shapes that the island sits inside (e.g. the outer
         stroke of the letter whose counter this island is).  Bridge to the
         SMALLEST such parent — that is the most immediate enclosing boundary.

      2. If no enclosing parent exists (free-floating island), fall back to the
         centroid-guided nearest-path search: find the point on each candidate
         nearest to the island centroid, then find the island-side point nearest
         to that anchor, and pick the shortest such connection.

    Using containment first fixes the most common bad-placement case: letter
    counters (holes in A, D, O, R …) would otherwise bridge to whichever other
    letter outline happened to be geometrically closest, producing bridges that
    cut straight across words.  With containment, each counter bridges to the
    stroke of its own letter.
    """
    island_line = LineString(island.path)
    centroid = island.polygon.centroid

    best_island_pt: Optional[Tuple[float, float]] = None
    best_target_pt: Optional[Tuple[float, float]] = None
    best_target_idx: Optional[int] = None

    # ── Pass 1: find the innermost path that contains this island ─────────
    containing: List[Tuple[float, int]] = []   # (area, path_index)
    for i, path in enumerate(paths):
        if i == island.index or len(path) < 3:
            continue
        try:
            poly = Polygon(path)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.contains(centroid):
                containing.append((poly.area, i))
        except Exception as _e:
            import warnings
            warnings.warn(f"Bridge geometry error (containment check path {i}): {_e}", RuntimeWarning, stacklevel=2)
            continue

    if containing:
        # Smallest enclosing area = most immediate parent (e.g. the letter stroke)
        containing.sort(key=lambda x: x[0])
        target_idx = containing[0][1]
        target_line = LineString(paths[target_idx])
        try:
            _, p_target = nearest_points(centroid, target_line)
            p_island, _ = nearest_points(island_line, p_target)
            best_island_pt = (p_island.x, p_island.y)
            best_target_pt = (p_target.x, p_target.y)
            best_target_idx = target_idx
        except Exception as _e:
            import warnings
            warnings.warn(f"Bridge geometry error (nearest-points pass 1): {_e}", RuntimeWarning, stacklevel=2)
            # fall through to pass 2

    # ── Pass 2: no containing path — centroid-guided nearest-path search ──
    if best_island_pt is None:
        best_dist = math.inf
        for i, path in enumerate(paths):
            if i == island.index or len(path) < 2:
                continue
            target_line = LineString(path)
            try:
                _, p_target = nearest_points(centroid, target_line)
                p_island, _ = nearest_points(island_line, p_target)
                dist = p_island.distance(p_target)
            except Exception as _e:
                import warnings
                warnings.warn(f"Bridge geometry error (nearest-points pass 2, path {i}): {_e}", RuntimeWarning, stacklevel=2)
                continue
            if dist < best_dist:
                best_dist = dist
                best_island_pt = (p_island.x, p_island.y)
                best_target_pt = (p_target.x, p_target.y)
                best_target_idx = i

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


def apply_manual_bridges(
    paths: List[Path2D],
    manual_bridges: list,
) -> List[Path2D]:
    """Splice user-drawn manual bridges into their source paths.

    The old approach appended each manual bridge as a separate closed rectangle
    path.  That doesn't create physical tabs — the cutter would cut the original
    path completely AND then cut the rectangle, leaving the island free to fall.

    This function uses the same technique as the auto-bridge algorithm:
    it finds which path pt1 lies on and MUTATES that path to include a detour
    out to pt2 and back, opening a gap of bridge_width at the attachment point.
    The result is one continuous cut that physically holds the two pieces.

    Args:
        paths:          The active path list to modify (already filtered for exclusions).
        manual_bridges: Each entry is [pt1, pt2, width_px] from the canvas.

    Returns:
        A new list of paths with the bridge geometry spliced in.
    """
    # Work on deep copies so we don't mutate the originals that the canvas holds
    result = [list(p) for p in paths]

    for bridge_data in manual_bridges:
        if len(bridge_data) < 2:
            continue
        pt1: Tuple[float, float] = bridge_data[0]
        pt2: Tuple[float, float] = bridge_data[1]
        width_px: float = bridge_data[2] if len(bridge_data) > 2 else mm_to_px(DEFAULT_BRIDGE_WIDTH_MM)

        # Find which path pt1 is on — we splice the bridge into that path.
        # This mirrors the auto-bridge convention: the "island" side is modified,
        # the "target" side is left intact (its cut line is where the tab tip rests).
        path_idx = _find_nearest_path(result, pt1)
        if path_idx is None:
            continue

        _insert_bridge_into_path(result[path_idx], pt1, pt2, width_px)

    return result


def _find_nearest_path(paths: List[Path2D], pt: Tuple[float, float]) -> Optional[int]:
    """Return the index of the path whose nearest vertex is closest to pt."""
    best_dist = math.inf
    best_idx: Optional[int] = None
    px, py = pt

    for i, path in enumerate(paths):
        for x, y in path:
            d = math.hypot(x - px, y - py)
            if d < best_dist:
                best_dist = d
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
