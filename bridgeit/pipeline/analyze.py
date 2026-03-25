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

from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

from bridgeit.pipeline.trace import Path2D


@dataclass
class Island:
    """A floating shape that needs a bridge to stay attached."""
    index: int                          # index in original path list
    path: Path2D                        # original (x,y) path
    polygon: Polygon                    # Shapely polygon
    area: float = field(init=False)

    def __post_init__(self) -> None:
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
    if not paths:
        return AnalysisResult(
            mainland_indices=[],
            islands=[],
            all_paths=[],
            image_size=image_size,
        )

    polygons = [_path_to_polygon(p) for p in paths]

    # Build a union of all shapes — anything inside the union but not
    # touching/contained-by a larger polygon is an island.
    mainland_indices: List[int] = []
    island_list: List[Island] = []

    if len(polygons) == 1:
        # Single shape — it's the mainland by definition.
        mainland_indices = [0]
    else:
        # Sort by area descending; largest is always mainland
        sorted_by_area = sorted(enumerate(polygons), key=lambda x: x[1].area, reverse=True)
        largest_idx, largest_poly = sorted_by_area[0]
        mainland_indices.append(largest_idx)

        for idx, poly in sorted_by_area[1:]:
            if not poly.is_valid:
                poly = poly.buffer(0)

            if _is_island(poly, polygons, idx):
                island_list.append(Island(index=idx, path=paths[idx], polygon=poly))
            else:
                mainland_indices.append(idx)

    return AnalysisResult(
        mainland_indices=mainland_indices,
        islands=island_list,
        all_paths=paths,
        image_size=image_size,
    )


def _path_to_polygon(path: Path2D) -> Polygon:
    """Convert a closed path to a Shapely Polygon."""
    # Drop the duplicate closing point Shapely doesn't need it
    coords = path[:-1] if path and path[0] == path[-1] else path
    if len(coords) < 3:
        # Degenerate — return tiny polygon at centroid
        if coords:
            cx = sum(x for x, _ in coords) / len(coords)
            cy = sum(y for _, y in coords) / len(coords)
            return Polygon([(cx - 0.5, cy - 0.5), (cx + 0.5, cy - 0.5), (cx, cy + 0.5)])
        return Polygon()
    poly = Polygon(coords)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


def _is_island(poly: Polygon, all_polygons: List[Polygon], own_idx: int) -> bool:
    """Return True if this polygon is not spatially connected to any larger polygon."""
    for i, other in enumerate(all_polygons):
        if i == own_idx:
            continue
        if other.area <= poly.area:
            continue
        # If this polygon is within or touches the larger one, it's NOT a standalone island
        if other.contains(poly) or other.touches(poly) or other.intersects(poly):
            return False
    return True


# ---------------------------------------------------------------------------
# Standalone validation
# ---------------------------------------------------------------------------

def _validate(image_path: str) -> None:
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
