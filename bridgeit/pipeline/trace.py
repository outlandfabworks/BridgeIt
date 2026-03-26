"""
trace.py — Contour tracing stage.

Takes an RGBA PIL Image (background already removed) and returns a list
of vector paths suitable for SVG export. Each path is a list of (x, y)
float tuples representing the outline of one contour.

Produces OUTLINES ONLY — not filled regions — so the result is directly
usable as a laser-cutter cut path.
"""

from __future__ import annotations

from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image

from bridgeit.config import DEFAULT_CONTOUR_SMOOTHING, DEFAULT_MIN_CONTOUR_AREA

# Type alias
Path2D = List[Tuple[float, float]]


def trace_contours(
    img: Image.Image,
    smoothing: float = DEFAULT_CONTOUR_SMOOTHING,
    min_area: float = DEFAULT_MIN_CONTOUR_AREA,
) -> List[Path2D]:
    """Extract clean vector outlines from an RGBA image.

    Args:
        img: RGBA PIL Image (background should be transparent).
        smoothing: Epsilon factor for Douglas-Peucker simplification.
                   Higher = more simplified. 0 = no simplification.
        min_area: Minimum contour area in pixels². Smaller contours are
                  treated as noise and discarded.

    Returns:
        List of closed paths. Each path is a list of (x, y) float tuples.
        The first point == last point (closed loop).
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    alpha = _extract_alpha(img)
    contours = _find_contours(alpha, min_area)
    paths = _contours_to_paths(contours, smoothing)
    return paths


def _extract_alpha(img: Image.Image) -> np.ndarray:
    """Return a binary mask from the alpha channel."""
    alpha = np.array(img.split()[3])          # 0-255
    _, binary = cv2.threshold(alpha, 10, 255, cv2.THRESH_BINARY)
    # Slight morphological cleanup to remove single-pixel speckles
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
    return binary


def _find_contours(binary: np.ndarray, min_area: float) -> List[np.ndarray]:
    """Find ALL contours — outer shapes AND inner holes (letter counters etc).

    Uses RETR_TREE so that holes inside letters (O, D, P, etc.) and logo
    interior cutouts are included as separate cut paths, which is correct
    for laser cutting.
    """
    contours, _ = cv2.findContours(
        binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_TC89_L1
    )
    filtered = [c for c in contours if cv2.contourArea(c) >= min_area]
    # Sort largest → smallest so primary shape comes first
    filtered.sort(key=cv2.contourArea, reverse=True)
    return filtered


def _contours_to_paths(contours: List[np.ndarray], smoothing: float) -> List[Path2D]:
    """Convert OpenCV contours to simplified (x, y) path lists."""
    paths: List[Path2D] = []
    for contour in contours:
        if len(contour) < 3:
            continue

        if smoothing > 0:
            peri = cv2.arcLength(contour, closed=True)
            epsilon = smoothing * peri / len(contour)
            contour = cv2.approxPolyDP(contour, epsilon, closed=True)

        if len(contour) < 3:
            continue

        pts: Path2D = [(float(pt[0][0]), float(pt[0][1])) for pt in contour]
        # Close the loop
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        paths.append(pts)

    return paths


def get_image_size(img: Image.Image) -> Tuple[int, int]:
    """Return (width, height) of image in pixels."""
    return img.size


# ---------------------------------------------------------------------------
# Standalone validation
# ---------------------------------------------------------------------------

def _validate(image_path: str) -> None:
    print(f"[trace] Processing: {image_path}")
    img = Image.open(image_path).convert("RGBA")
    paths = trace_contours(img)
    print(f"[trace] Found {len(paths)} contour(s)")
    for i, p in enumerate(paths):
        print(f"  contour {i}: {len(p)} points")

    # Save a debug PNG with contours drawn
    from pathlib import Path
    import numpy as np

    canvas = np.zeros((*img.size[::-1], 3), dtype=np.uint8)
    for path in paths:
        pts = np.array([[int(x), int(y)] for x, y in path], dtype=np.int32)
        cv2.polylines(canvas, [pts], isClosed=True, color=(0, 200, 100), thickness=2)

    out_path = Path(image_path).with_stem(Path(image_path).stem + "_contours").with_suffix(".png")
    cv2.imwrite(str(out_path), canvas)
    print(f"[trace] Debug image saved: {out_path}")
    print("[trace] PASS" if paths else "[trace] FAIL — no contours found")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python trace.py <rgba_image>")
        sys.exit(1)
    _validate(sys.argv[1])
