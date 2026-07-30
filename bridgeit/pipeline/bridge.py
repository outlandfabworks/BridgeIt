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


# Minimum span (px) from first bridge to island's far end to trigger a second bridge.
# At 96 DPI, 100 px ≈ 26 mm — about the width of a thumb, a reasonable threshold for
# "this island is long enough that one bridge leaves the far end loose."
_MIN_SECOND_BRIDGE_PX = 100.0


def _farthest_path_point(
    path: Path2D, from_pt: Tuple[float, float]
) -> Optional[Tuple[float, float]]:
    """Return the vertex on path that is farthest from from_pt."""
    if not path:
        return None
    fx, fy = from_pt
    best_d = -1.0
    best_pt: Optional[Tuple[float, float]] = None
    for pt in path:
        d = math.hypot(pt[0] - fx, pt[1] - fy)
        if d > best_d:
            best_d = d
            best_pt = pt
    return best_pt


def _find_best_target(
    anchor: Point,
    island: Island,
    paths: List[Path2D],
) -> Optional[Tuple[Tuple[float, float], Tuple[float, float], int]]:
    """Find the best bridge target for the given anchor point on the island.

    Strategy — prefer containment over proximity (same as before, factored out
    so the second-bridge search can reuse it with a different anchor):

      1. Find every path whose polygon contains the island centroid.  Bridge to
         the smallest such parent — most immediate enclosing boundary.
      2. Fall back to nearest-path search if no containing path exists.

    Args:
        anchor: The Shapely Point used to guide nearest-point queries on targets.
                First bridge uses the island centroid; second bridge uses the
                far-end vertex.
        island: The Island being bridged.
        paths:  All current paths (mutable copies from add_bridges).

    Returns:
        (island_pt, target_pt, target_idx) or None if no target found.
    """
    import warnings

    island_line = LineString(island.path)
    centroid = island.polygon.centroid
    island_area = island.polygon.area

    best_island_pt: Optional[Tuple[float, float]] = None
    best_target_pt: Optional[Tuple[float, float]] = None
    best_target_idx: Optional[int] = None

    # ── Pass 1: find the innermost path that contains this island ─────────
    containing: List[Tuple[float, int]] = []
    for i, path in enumerate(paths):
        if i == island.index or len(path) < 3:
            continue
        try:
            poly = Polygon(path)
            if not poly.is_valid:
                poly = poly.buffer(0)
            # Only a larger path can be a genuine container.
            if poly.area <= island_area:
                continue
            if poly.contains(centroid):
                containing.append((poly.area, i))
        except Exception as _e:
            warnings.warn(
                f"Bridge geometry error (containment check path {i}): {_e}",
                RuntimeWarning, stacklevel=3,
            )
            continue

    if containing:
        containing.sort(key=lambda x: x[0])
        target_idx = containing[0][1]
        target_line = LineString(paths[target_idx])
        try:
            _, p_target = nearest_points(anchor, target_line)
            p_island, _ = nearest_points(island_line, p_target)
            best_island_pt = (p_island.x, p_island.y)
            best_target_pt = (p_target.x, p_target.y)
            best_target_idx = target_idx
        except Exception as _e:
            warnings.warn(
                f"Bridge geometry error (nearest-points pass 1): {_e}",
                RuntimeWarning, stacklevel=3,
            )

    # ── Pass 2: no containing path — anchor-guided nearest-path search ────
    if best_island_pt is None:
        best_dist = math.inf
        for i, path in enumerate(paths):
            if i == island.index or len(path) < 2:
                continue
            target_line = LineString(path)
            try:
                _, p_target = nearest_points(anchor, target_line)
                p_island, _ = nearest_points(island_line, p_target)
                dist = p_island.distance(p_target)
            except Exception as _e:
                warnings.warn(
                    f"Bridge geometry error (nearest-points pass 2, path {i}): {_e}",
                    RuntimeWarning, stacklevel=3,
                )
                continue
            if dist < best_dist:
                best_dist = dist
                best_island_pt = (p_island.x, p_island.y)
                best_target_pt = (p_target.x, p_target.y)
                best_target_idx = i

    if best_island_pt is None:
        return None
    return (best_island_pt, best_target_pt, best_target_idx)


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
    paths = [list(p) for p in analysis.all_paths]
    bridges: List[Bridge] = []

    if not analysis.islands:
        return BridgeResult(paths=paths, bridges=[], image_size=analysis.image_size, dpi=dpi)

    for island in analysis.islands:
        bridges.extend(_bridge_island(island, paths, analysis, bridge_px))

    return BridgeResult(paths=paths, bridges=bridges, image_size=analysis.image_size, dpi=dpi)


def _bridge_island(
    island: Island,
    paths: List[Path2D],
    analysis: AnalysisResult,
    bridge_px: float,
) -> List[Bridge]:
    """Find target path(s) and insert bridge(s) into the island path.

    Adds a second bridge at the far end of elongated islands so that long thin
    shapes are held at both ends rather than dangling from a single tab.
    """
    centroid = island.polygon.centroid

    r1 = _find_best_target(centroid, island, paths)
    if r1 is None:
        return []

    island_pt1, target_pt1, target_idx1 = r1
    _insert_bridge_into_path(paths[island.index], island_pt1, target_pt1, bridge_px)

    result: List[Bridge] = [Bridge(
        island_idx=island.index,
        target_idx=target_idx1,
        island_pt=island_pt1,
        target_pt=target_pt1,
        width_px=bridge_px,
    )]

    # If the island is elongated, add a second bridge at the opposite end so the
    # far tip doesn't remain free to vibrate or fall out during cutting.
    far_pt = _farthest_path_point(island.path, island_pt1)
    if far_pt is not None:
        far_dist = math.hypot(far_pt[0] - island_pt1[0], far_pt[1] - island_pt1[1])
        if far_dist > _MIN_SECOND_BRIDGE_PX:
            r2 = _find_best_target(Point(far_pt), island, paths)
            if r2 is not None:
                island_pt2, target_pt2, target_idx2 = r2
                # Skip if both endpoints are nearly identical to the first bridge
                # (happens when the only target path has a single nearest point
                # regardless of anchor — e.g. free-floating islands far from everything).
                sep = math.hypot(island_pt2[0] - island_pt1[0], island_pt2[1] - island_pt1[1])
                if sep > bridge_px * 2:
                    _insert_bridge_into_path(paths[island.index], island_pt2, target_pt2, bridge_px)
                    result.append(Bridge(
                        island_idx=island.index,
                        target_idx=target_idx2,
                        island_pt=island_pt2,
                        target_pt=target_pt2,
                        width_px=bridge_px,
                    ))

    return result


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

    # Find the segment (path[i], path[i+1]) nearest to island_pt and insert
    # the bridge between those two vertices.  Inserting at i+1 means the path
    # flows: path[i] → a → [bridge] → b → path[i+1] with no diagonal jump.
    seg_idx = _find_nearest_segment(path, island_pt)
    insert_idx = (seg_idx + 1) % len(path)

    bridge_pts = [a, d, c, b]
    path[insert_idx:insert_idx] = bridge_pts


def _find_nearest_segment(path: Path2D, pt: Tuple[float, float]) -> int:
    """Return index i of the segment (path[i], path[i+1]) nearest to pt.

    Projects pt onto each segment and measures the true perpendicular distance,
    so the returned index is the segment the bridge should split — not just the
    nearest vertex.  Inserting at i+1 places bridge geometry between the two
    segment endpoints, keeping the path flowing smoothly through the attachment.
    """
    px, py = pt
    n = len(path)
    best_dist = math.inf
    best_idx = 0

    for i in range(n):
        ax, ay = path[i]
        bx, by = path[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1e-12:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
        near_x = ax + t * dx
        near_y = ay + t * dy
        d = math.hypot(px - near_x, py - near_y)
        if d < best_dist:
            best_dist = d
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
    """Return the index of the path nearest to pt using segment projection."""
    best_dist = math.inf
    best_idx: Optional[int] = None
    px, py = pt

    for i, path in enumerate(paths):
        n = len(path)
        for j in range(n):
            ax, ay = path[j]
            bx, by = path[(j + 1) % n]
            dx, dy = bx - ax, by - ay
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq < 1e-12:
                t = 0.0
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
            near_x = ax + t * dx
            near_y = ay + t * dy
            d = math.hypot(px - near_x, py - near_y)
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
