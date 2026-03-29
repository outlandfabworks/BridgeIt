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

# Type alias: a 2D path is simply a list of (x, y) float coordinate pairs.
# This makes function signatures much easier to read.
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
    # Ensure the image is in RGBA mode so we can access the alpha channel.
    # The alpha channel encodes which pixels are foreground vs. background.
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Step 1: Extract the alpha channel and turn it into a clean binary mask
    alpha = _extract_alpha(img)

    # Step 2: Find the outlines of all shapes in the binary mask
    contours = _find_contours(alpha, min_area)

    # Step 3: Convert those outlines into simplified (x, y) point lists
    paths = _contours_to_paths(contours, smoothing)
    return paths


def _extract_alpha(img: Image.Image) -> np.ndarray:
    """Return a binary mask from the alpha channel.

    Applies a Gaussian blur before re-thresholding so that the pixel-grid
    staircase on diagonal edges is smoothed out, producing cleaner contours.
    """
    # PIL's split() returns separate R, G, B, A channels.
    # Index [3] is the alpha channel — 0 = transparent, 255 = opaque.
    alpha = np.array(img.split()[3])          # 0-255

    # Threshold: any pixel with alpha > 10 becomes white (255),
    # fully transparent pixels become black (0).
    # This gives us a hard foreground/background mask.
    _, binary = cv2.threshold(alpha, 10, 255, cv2.THRESH_BINARY)

    # Gaussian blur softens the staircase edges that come from pixel-perfect
    # alpha channels, then we threshold again to get a clean binary result.
    blurred = cv2.GaussianBlur(binary, (7, 7), 2.0)
    _, binary = cv2.threshold(blurred, 127, 255, cv2.THRESH_BINARY)

    # Morphological operations clean up the mask:
    # MORPH_CLOSE fills tiny holes inside the foreground shape.
    # MORPH_OPEN  removes tiny isolated specks outside the shape.
    # An ellipse kernel gives smoother results than a square one.
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
    # cv2.findContours returns a list of contours.
    # RETR_TREE retrieves every contour including nested holes (not just outer edges).
    # CHAIN_APPROX_TC89_L1 compresses straight segments to save memory/points.
    contours, _ = cv2.findContours(
        binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_TC89_L1
    )

    # Discard any contour smaller than min_area — these are noise, dust, or
    # JPEG compression artefacts, not real design elements.
    filtered = [c for c in contours if cv2.contourArea(c) >= min_area]

    # Sort largest → smallest so the primary (outer) shape comes first in the list.
    # This matters for the island-detection stage that follows.
    filtered.sort(key=cv2.contourArea, reverse=True)

    # Hard cap: an AI-processed complex image can still produce thousands of
    # tiny contours after area filtering.  Keep only the largest 500 to
    # prevent the tracing stage from hanging the process.
    return filtered[:500]


def _contours_to_paths(contours: List[np.ndarray], smoothing: float) -> List[Path2D]:
    """Convert OpenCV contours to simplified (x, y) path lists."""
    paths: List[Path2D] = []
    for contour in contours:
        # A contour needs at least 3 points to form any kind of shape
        if len(contour) < 3:
            continue

        if smoothing > 0:
            # arcLength measures the perimeter of the contour.
            # We use it to scale epsilon so the smoothing is proportional
            # to the size of the shape, not an absolute pixel value.
            peri = cv2.arcLength(contour, closed=True)

            # epsilon is the maximum allowed deviation when simplifying.
            # The 0.001 factor keeps it conservative — only removes truly
            # redundant points, not important curve-defining ones.
            epsilon = max(1.0, smoothing * peri * 0.001)

            # approxPolyDP (Douglas-Peucker algorithm) removes points that
            # deviate less than epsilon from the simplified line.
            contour = cv2.approxPolyDP(contour, epsilon, closed=True)

        # After simplification some tiny contours may degenerate below 3 pts
        if len(contour) < 3:
            continue

        # OpenCV contours have shape (N, 1, 2); we flatten to a plain list of (x,y)
        pts: Path2D = [(float(pt[0][0]), float(pt[0][1])) for pt in contour]

        # Close the loop: append the first point at the end if it isn't already there.
        # A closed path is needed so SVG's Z command and Shapely polygons work correctly.
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        paths.append(pts)

    return paths


def get_image_size(img: Image.Image) -> Tuple[int, int]:
    """Return (width, height) of image in pixels."""
    # PIL's .size property already returns (width, height) — this thin wrapper
    # gives the rest of the pipeline a named function to call.
    return img.size


# ---------------------------------------------------------------------------
# Standalone validation
# ---------------------------------------------------------------------------

def _validate(image_path: str) -> None:
    # This function is only called when running this module directly.
    # It traces contours from an image and saves a debug visualisation.
    print(f"[trace] Processing: {image_path}")
    img = Image.open(image_path).convert("RGBA")
    paths = trace_contours(img)
    print(f"[trace] Found {len(paths)} contour(s)")
    for i, p in enumerate(paths):
        print(f"  contour {i}: {len(p)} points")

    # Draw each contour in green on a black canvas for visual debugging
    from pathlib import Path
    import numpy as np

    # Create a blank black image the same size as the input
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
