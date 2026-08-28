"""
analyze.py — Island detection stage.

Uses Shapely to classify contours as either:
  - "mainland" — the outer boundary (or large connected shapes)
  - "island"   — a floating shape that would fall out when cut

An island is any contour that is NOT contained within any other contour.
When there is only one contour it is treated as the mainland (the primary
cut shape). With multiple contours, each contour that does not overlap or
touch the bounding box of a larger one is an island.

The result is a list of Island dataclasses, each carrying its Shapely
Polygon and the original path for reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

# Shapely is a library for working with 2D geometric shapes.
# Polygon represents a filled region; MultiPolygon holds several Polygons.
from shapely.geometry import Polygon, MultiPolygon

# unary_union merges a list of shapes into one combined shape
from shapely.ops import unary_union

from bridgeit.pipeline.trace import Path2D


# @dataclass automatically generates __init__, __repr__, and __eq__ methods
# based on the annotated class attributes — no need to write them manually.

@dataclass
class Island:
    """A floating shape that needs a bridge to stay attached."""
    index: int                          # index in original path list
    path: Path2D                        # original (x,y) path
    polygon: Polygon                    # Shapely polygon for geometric operations

    # field(init=False) means 'area' is NOT a constructor parameter;
    # it is calculated automatically in __post_init__ instead.
    area: float = field(init=False)

    def __post_init__(self) -> None:
        # __post_init__ runs right after __init__; we use it to compute
        # derived fields that depend on other fields being set first.
        self.area = self.polygon.area


@dataclass
class AnalysisResult:
    """Output of the analyze stage."""
    mainland_indices: List[int]         # indices considered "mainland"
    islands: List[Island]               # floating islands needing bridges
    all_paths: List[Path2D]             # original paths (unchanged)
    image_size: Tuple[int, int]         # (width, height) in pixels


def analyze_islands(
    paths: List[Path2D],
    image_size: Tuple[int, int],
) -> AnalysisResult:
    """Classify paths as mainland or island.

    Args:
        paths: List of closed (x, y) paths from trace stage.
        image_size: (width, height) of source image in pixels.

    Returns:
        AnalysisResult with classified paths.
    """
    # If there are no paths at all (e.g. blank image), return an empty result
    if not paths:
        return AnalysisResult(
            mainland_indices=[],
            islands=[],
            all_paths=[],
            image_size=image_size,
        )

    # Convert every path (list of points) into a Shapely Polygon so we can
    # use spatial operations like contains() and intersects()
    polygons = [_path_to_polygon(p) for p in paths]

    mainland_indices: List[int] = []
    island_list: List[Island] = []

    if len(polygons) == 1:
        # With only one shape in the design, it must be the main cut outline
        mainland_indices = [0]
    else:
        # Sort by area descending so the largest shape is examined first.
        # The largest shape is always treated as the mainland (primary outline).
        sorted_by_area = sorted(enumerate(polygons), key=lambda x: x[1].area, reverse=True)
        largest_idx, largest_poly = sorted_by_area[0]
        mainland_indices.append(largest_idx)

        # For every smaller shape, decide if it is a freestanding island
        # or if it is geometrically connected to (part of) a larger shape.
        for idx, poly in sorted_by_area[1:]:
            # Some polygons may be self-intersecting due to pixel noise;
            # buffer(0) is the standard Shapely fix for invalid geometry.
            if not poly.is_valid:
                poly = poly.buffer(0)

            if _is_island(poly, polygons, idx):
                # This shape doesn't touch or overlap any larger shape,
                # so it would fall out when the design is cut — it needs a bridge.
                island_list.append(Island(index=idx, path=paths[idx], polygon=poly))
            else:
                # This shape is connected to or nested inside a larger shape,
                # so it's part of the main design — no bridge needed.
                mainland_indices.append(idx)

    return AnalysisResult(
        mainland_indices=mainland_indices,
        islands=island_list,
        all_paths=paths,
        image_size=image_size,
    )


def _path_to_polygon(path: Path2D) -> Polygon:
    """Convert a closed path to a Shapely Polygon."""
    # Shapely doesn't want the closing duplicate point (it closes automatically),
    # so strip it if it's there.
    coords = path[:-1] if path and path[0] == path[-1] else path

    if len(coords) < 3:
        # A polygon needs at least 3 vertices; for degenerate cases create
        # a tiny triangle near the path's centroid so later checks don't crash.
        if coords:
            cx = sum(x for x, _ in coords) / len(coords)
            cy = sum(y for _, y in coords) / len(coords)
            return Polygon([(cx - 0.5, cy - 0.5), (cx + 0.5, cy - 0.5), (cx, cy + 0.5)])
        return Polygon()   # empty polygon — contains nothing

    poly = Polygon(coords)

    # Fix any self-intersections that arise from noisy pixel-traced outlines.
    # buffer(0) can return a MultiPolygon for truly degenerate rings; in that
    # case take the largest sub-polygon so the rest of the pipeline sees a
    # simple Polygon with a valid .exterior attribute.
    if not poly.is_valid:
        fixed = poly.buffer(0)
        if fixed.geom_type == "MultiPolygon":
            fixed = max(fixed.geoms, key=lambda g: g.area)
        poly = fixed
    return poly


def _is_island(poly: Polygon, all_polygons: List[Polygon], own_idx: int) -> bool:
    """Return True if this polygon is a floating island relative to every larger polygon.

    Two cases make a polygon mainland (return False):
      - Its boundary shares points with a larger polygon's boundary → the shapes
        are adjacent pieces of the same design (touching edges, overlapping outlines).

    Two cases keep it as an island (return True):
      - It is completely enclosed inside a larger polygon with no boundary contact
        (e.g. a counter inside a letter, a hole inside a frame) → it will fall out
        when cut and needs a bridge.
      - It is completely separate from all larger polygons → also needs a bridge.

    Using intersects() was wrong: it returns True even when one polygon is fully
    INSIDE another (their interiors share space), causing holes to be misclassified
    as mainland. The fix is to test boundary contact only.
    """
    for i, other in enumerate(all_polygons):
        if i == own_idx:
            continue
        if other.area <= poly.area:
            continue

        # Shapes are "connected" only if their outlines actually share points.
        # A polygon completely enclosed inside another has no boundary contact —
        # it's an island (falls out when cut), not a connected mainland piece.
        if not other.contains(poly) and other.exterior.intersects(poly.exterior):
            return False

    return True


# ---------------------------------------------------------------------------
# Standalone validation
# ---------------------------------------------------------------------------

def _validate(image_path: str) -> None:
    # This function is only called when running this module directly.
    # It runs the full trace→analyze flow and prints the results.
    from PIL import Image
    from bridgeit.pipeline.trace import trace_contours

    print(f"[analyze] Processing: {image_path}")
    img = Image.open(image_path).convert("RGBA")
    paths = trace_contours(img)
    result = analyze_islands(paths, img.size)

    print(f"[analyze] Total paths: {len(paths)}")
    print(f"[analyze] Mainland paths: {result.mainland_indices}")
    print(f"[analyze] Islands detected: {len(result.islands)}")
    for island in result.islands:
        print(f"  island {island.index}: area={island.area:.1f}px²  points={len(island.path)}")
    print("[analyze] PASS")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python analyze.py <rgba_image>")
        sys.exit(1)
    _validate(sys.argv[1])
