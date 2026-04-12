"""
export.py — SVG export stage.

Two output modes:
  export_svg()         — fabrication file: black hairline stroke on white bg
  make_preview_svg()   — on-screen preview: white strokes on dark bg with
                         coloured bridge markers showing connection points
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

# svgwrite is a Python library for generating SVG files programmatically.
# It handles XML escaping and formatting so we don't have to write raw strings.
import svgwrite

from bridgeit.config import ACCENT_COLOR, PREVIEW_BG_COLOR
from bridgeit.pipeline.bridge import BridgeResult
from bridgeit.pipeline.trace import Path2D

# ── Fabrication export style ──────────────────────────────────────────────
# These are the visual properties for the final SVG sent to a laser cutter.
# Black stroke is the universal signal for "cut here" in laser RIP software.
CUT_STROKE = "#000000"
CUT_FILL = "none"           # shapes are outlines only, not filled
CUT_STROKE_WIDTH = "0.1px"  # Hairline — as thin as possible for clean cuts

# ── Preview style ─────────────────────────────────────────────────────────
# These are used for the on-screen SVG rendered in the app — white on dark.
PREVIEW_STROKE = "#ffffff"
PREVIEW_STROKE_WIDTH = "1.5px"
BRIDGE_COLOR = "#22c55e"      # green — easy to spot bridge connections
BRIDGE_MARKER_R = 6           # px radius for bridge endpoint dots


def export_svg(
    result: BridgeResult,
    output_path: str | Path,
    stroke_color: str = CUT_STROKE,
    stroke_width: str = CUT_STROKE_WIDTH,
) -> Path:
    """Write fabrication-ready SVG to disk (black stroke, no markers)."""
    # Resolve to an absolute path and create any missing parent directories
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    w, h = result.image_size

    # Create the SVG document with explicit pixel dimensions matching the source image
    dwg = svgwrite.Drawing(filename=str(out), size=(f"{w}px", f"{h}px"), profile="full")

    # viewBox defines the coordinate space — 0,0 to w,h matches our pixel coordinates
    dwg.viewbox(0, 0, w, h)
    dwg.set_desc(title="BridgeIt Export", desc="Fabrication-ready cut path with bridges")

    # Group all cut paths together — this makes it easy to select/move them
    # in vector editing software like Inkscape or Illustrator.
    cut_group = dwg.g(
        id="cut_paths",
        stroke=stroke_color,
        fill=CUT_FILL,
        # Extra SVG attributes that can't be passed as Python kwargs due to hyphens
        **{"stroke-width": stroke_width, "stroke-linecap": "round", "stroke-linejoin": "round"},
    )

    # Add each path to the group — each path becomes one SVG <path> element
    for i, path in enumerate(result.paths):
        if len(path) < 2:
            continue  # skip degenerate paths that can't form a visible stroke

        # _path_to_svg_d converts our list of (x,y) tuples into SVG path data
        # like "M 10.000 20.000 L 30.000 40.000 Z"
        cut_group.add(dwg.path(d=_path_to_svg_d(path), id=f"path_{i}"))

    dwg.add(cut_group)

    # pretty=True adds newlines/indentation for human-readable SVG
    dwg.save(pretty=True)
    return out


def make_preview_svg(result: BridgeResult) -> str:
    """Return an SVG string for on-screen preview.

    Uses white strokes on the app's dark background colour, and overlays
    green markers + lines wherever bridges connect islands to the design.
    """
    w, h = result.image_size

    # Build the SVG in memory — no filename needed yet
    dwg = svgwrite.Drawing(size=(f"{w}px", f"{h}px"), profile="full")
    dwg.viewbox(0, 0, w, h)

    # Fill the entire background with the app's dark theme colour
    dwg.add(dwg.rect(insert=(0, 0), size=(w, h), fill=PREVIEW_BG_COLOR))

    # Draw all cut paths as white outlines on the dark background
    cut_group = dwg.g(
        id="cut_paths",
        stroke=PREVIEW_STROKE,
        fill=CUT_FILL,
        **{"stroke-width": PREVIEW_STROKE_WIDTH,
           "stroke-linecap": "round",
           "stroke-linejoin": "round"},
    )
    for i, path in enumerate(result.paths):
        if len(path) < 2:
            continue
        cut_group.add(dwg.path(d=_path_to_svg_d(path), id=f"path_{i}"))
    dwg.add(cut_group)

    # Overlay green markers wherever bridges connect islands to the design.
    # These are visual only — they don't appear in the fabrication export.
    if result.bridges:
        bridge_group = dwg.g(id="bridges")
        for b in result.bridges:
            ix, iy = b.island_pt    # point on the island outline
            tx, ty = b.target_pt    # point on the target (mainland/neighbour) outline

            # Draw a dashed line from the island contact point to the target point
            bridge_group.add(dwg.line(
                start=(ix, iy), end=(tx, ty),
                stroke=BRIDGE_COLOR,
                **{"stroke-width": "2px", "stroke-dasharray": "6,3"},
            ))

            # Draw filled circles at each endpoint for easy visual identification
            for cx, cy in [(ix, iy), (tx, ty)]:
                bridge_group.add(dwg.circle(
                    center=(cx, cy), r=BRIDGE_MARKER_R,
                    fill=BRIDGE_COLOR, stroke="none",
                ))
        dwg.add(bridge_group)

    # svgwrite requires a filename to save, so we use a TemporaryDirectory
    # (auto-cleaned by the OS on normal exit and on most crash paths).
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = os.path.join(tmp_dir, "preview.svg")
        dwg.filename = tmp_path
        dwg.save(pretty=False)
        with open(tmp_path, "r", encoding="utf-8") as f:
            return f.read()


def export_svg_string(result: BridgeResult) -> str:
    """Legacy helper — returns fabrication SVG as a string."""
    # This is a convenience wrapper used by the pipeline for in-memory SVG handling.
    # It writes to a temp file then reads it back, the same pattern as make_preview_svg.
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = os.path.join(tmp_dir, "export.svg")
        export_svg(result, tmp_path)
        with open(tmp_path, "r", encoding="utf-8") as f:
            return f.read()


def export_image_svg(
    nobg_image: "PIL.Image.Image",
    output_path: str | Path,
    smoothing: float = 2.0,
    min_area: float = 50.0,
) -> Path:
    """Export the background-removed image as a filled vector SVG.

    Each traced region is filled with the average colour sampled from that area
    of the source image.  Parent-child contour relationships (from OpenCV's
    RETR_TREE) are used so that holes — the transparent ring inside a logo
    circle, letter counters, etc. — are punched out correctly via SVG's
    evenodd fill rule rather than painted over.  Smooth cubic Bézier curves
    replace polylines so that circles and arcs look clean at any zoom level.

    Args:
        nobg_image:   RGBA PIL Image with background already removed.
        output_path:  Where to write the SVG file.
        smoothing:    Douglas-Peucker epsilon factor (higher = fewer points).
        min_area:     Minimum contour area in px² — smaller shapes discarded.

    Returns:
        Resolved output Path.
    """
    import numpy as np
    import cv2 as _cv2
    from PIL import Image as _PILImage
    from bridgeit.pipeline.trace import _extract_alpha

    if nobg_image.mode != "RGBA":
        nobg_image = nobg_image.convert("RGBA")

    w, h = nobg_image.size

    # Supersample at 2× before tracing.
    # Pixel-grid contours have staircase vertices; at 2× resolution each step is
    # half the angular arc of the original, so the vertices after Douglas-Peucker
    # simplification fall more evenly around curves.  Catmull-Rom smoothing of
    # those evenly-distributed points produces genuinely smooth circles and arcs
    # rather than smoothly-interpolated staircases.
    _S = 2
    w_s, h_s = w * _S, h * _S
    hi_img   = nobg_image.resize((w_s, h_s), _PILImage.Resampling.LANCZOS)
    rgb_hi   = np.array(hi_img.convert("RGB"), dtype=np.uint8)
    alpha_hi = np.array(hi_img.split()[3], dtype=np.uint8)

    # Binary mask from the upscaled image
    binary = _extract_alpha(hi_img)

    # RETR_TREE: captures the full parent→child hierarchy so we know which
    # contours are holes (odd depth) vs filled shapes (even depth).
    raw_contours, hierarchy = _cv2.findContours(
        binary, _cv2.RETR_TREE, _cv2.CHAIN_APPROX_TC89_L1
    )

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    dwg = svgwrite.Drawing(filename=str(out), size=(f"{w}px", f"{h}px"), profile="full")
    dwg.viewbox(0, 0, w, h)
    dwg.set_desc(title="BridgeIt SVG Image", desc="Filled vector export")
    dwg.add(dwg.rect(insert=(0, 0), size=(w, h), fill="#ffffff"))

    if hierarchy is None or len(raw_contours) == 0:
        dwg.save(pretty=False)
        return out

    hier = hierarchy[0]  # shape (N, 4): [next_sib, prev_sib, first_child, parent]

    # Area threshold scales by _S² (area grows as square of linear scale)
    area_hi = min_area * (_S * _S)

    def _simplify(c):
        if _cv2.contourArea(c) < area_hi:
            return None
        if len(c) >= 3:
            # Use a fixed absolute 1 px epsilon in the hi-res space (= 0.5 px in
            # original coordinates with 2× supersampling).  A proportional epsilon
            # (the cut-path default) gives the same visual smoothness at every
            # zoom level but produces far too few vertices for large circles —
            # with smoothing=2.0 a 500 px-radius circle would get only ~22
            # vertices → ~352 Chaikin segments → 4.5 px per segment → visible
            # polygon facets.  An absolute 1 px eps gives ~70 vertices →
            # 1120 segments → 1.4 px each → sub-pixel-smooth at normal size.
            c = _cv2.approxPolyDP(c, 1.0, True)
        return c if len(c) >= 3 else None

    simplified = [_simplify(c) for c in raw_contours]

    def _children(i):
        """Direct children of contour i (its holes)."""
        kids, j = [], hier[i][2]
        while j != -1:
            kids.append(j)
            j = hier[j][0]
        return kids

    def _depth(i):
        """Nesting depth: 0 = top-level, 1 = hole, 2 = island-in-hole, …"""
        d, p = 0, hier[i][3]
        while p != -1:
            d += 1
            p = hier[p][3]
        return d

    # Scale contour coordinates back to original (1×) space
    def _to_pts(c):
        return [(float(pt[0][0]) / _S, float(pt[0][1]) / _S) for pt in c]

    # Even-depth contours (0, 2, 4, …) are filled shapes.
    # Odd-depth contours are holes — included as sub-paths of their even-depth
    # parent so SVG's evenodd rule punches them out automatically.
    even_idxs = sorted(
        [i for i in range(len(simplified))
         if simplified[i] is not None and _depth(i) % 2 == 0],
        key=lambda i: _cv2.contourArea(raw_contours[i]),
        reverse=True,
    )

    for i in even_idxs:
        c_outer = simplified[i]
        pts_outer = _to_pts(c_outer)

        # Collect direct hole children for this shape
        hole_pts_list = [
            _to_pts(simplified[j])
            for j in _children(i)
            if simplified[j] is not None and len(simplified[j]) >= 3
        ]

        # Sample colour from the high-res image using upscaled coordinates so
        # we get maximum colour accuracy from the supersampled pixels.
        smask = np.zeros((h_s, w_s), dtype=np.uint8)
        _cv2.fillPoly(
            smask,
            [np.array([[int(x * _S), int(y * _S)] for x, y in pts_outer], dtype=np.int32)],
            255,
        )
        for hp in hole_pts_list:
            _cv2.fillPoly(
                smask,
                [np.array([[int(x * _S), int(y * _S)] for x, y in hp], dtype=np.int32)],
                0,
            )

        fg = (smask > 0) & (alpha_hi > 64)
        if fg.sum() < 10:
            continue

        avg  = rgb_hi[fg].mean(axis=0)
        fill = "#{:02x}{:02x}{:02x}".format(int(avg[0]), int(avg[1]), int(avg[2]))

        # Compound path: outer boundary + hole sub-paths.
        # fill-rule="evenodd" punches holes cleanly without needing reversed winding.
        # A 0.5 px stroke matching the fill closes any sub-pixel seams between
        # adjacent same-colour shapes without visibly thickening the edges.
        d_parts = [_smooth_d(pts_outer)] + [_smooth_d(hp) for hp in hole_pts_list]
        dwg.add(dwg.path(
            d=" ".join(d_parts),
            fill=fill,
            stroke=fill,
            **{"fill-rule": "evenodd", "stroke-width": "0.5", "stroke-linejoin": "round"},
        ))

    dwg.save(pretty=False)
    return out


def _path_to_svg_d(path: Path2D) -> str:
    """Convert a list of (x, y) tuples into an SVG path data string.

    M = moveto (pen-up move to start), L = lineto (draw a line), Z = closepath.
    Used for cut-path export where straight segments are correct.
    """
    if not path:
        return ""

    # Start the path at the first point
    parts = [f"M {path[0][0]:.3f} {path[0][1]:.3f}"]

    # Draw a straight line to each subsequent point
    for x, y in path[1:]:
        parts.append(f"L {x:.3f} {y:.3f}")

    # Z closes the path back to the first point — essential for a filled/cut shape
    parts.append("Z")
    return " ".join(parts)


def _smooth_d(path: Path2D, iterations: int = 4) -> str:
    """Convert a closed path to a smooth SVG path using Chaikin corner-cutting.

    Vertices are first redistributed at uniform arc-length spacing before the
    corner-cutting iterations.  Without this step, clusters of closely-spaced
    staircase vertices (dense on horizontal/vertical circle sections, sparse at
    45° diagonals) cause uneven corner-cutting that manifests as visible ripples
    on otherwise smooth curves.  Uniform spacing ensures the Chaikin limit curve
    has consistent curvature throughout.
    """
    import math

    pts = list(path)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    if n < 2:
        return ""
    if n == 2:
        return (f"M {pts[0][0]:.2f} {pts[0][1]:.2f} "
                f"L {pts[1][0]:.2f} {pts[1][1]:.2f} Z")

    # ── Step 1: uniform arc-length resampling ────────────────────────────
    # Resample the polygon at n equally-spaced positions along its perimeter
    # so that Chaikin applies evenly everywhere.
    closed = pts + [pts[0]]
    cumlen = [0.0]
    for i in range(len(closed) - 1):
        dx = closed[i + 1][0] - closed[i][0]
        dy = closed[i + 1][1] - closed[i][1]
        cumlen.append(cumlen[-1] + math.hypot(dx, dy))
    total = cumlen[-1]

    if total > 1e-6:
        step = total / n
        target = 0.0
        resampled: list = []
        seg = 0
        for _ in range(n):
            while seg + 1 < len(cumlen) - 1 and cumlen[seg + 1] < target:
                seg += 1
            seg_len = cumlen[seg + 1] - cumlen[seg]
            t = (target - cumlen[seg]) / seg_len if seg_len > 1e-9 else 0.0
            resampled.append((
                closed[seg][0] + t * (closed[seg + 1][0] - closed[seg][0]),
                closed[seg][1] + t * (closed[seg + 1][1] - closed[seg][1]),
            ))
            target += step
        pts = resampled

    # ── Step 2: Chaikin corner-cutting ───────────────────────────────────
    # With uniform spacing the subdivision is even and the resulting
    # quadratic B-spline has no curvature ripples.
    for _ in range(iterations):
        new_pts: list = []
        m = len(pts)
        for i in range(m):
            p1 = pts[i]
            p2 = pts[(i + 1) % m]
            new_pts.append((0.75 * p1[0] + 0.25 * p2[0], 0.75 * p1[1] + 0.25 * p2[1]))
            new_pts.append((0.25 * p1[0] + 0.75 * p2[0], 0.25 * p1[1] + 0.75 * p2[1]))
        pts = new_pts

    parts = [f"M {pts[0][0]:.2f} {pts[0][1]:.2f}"]
    for x, y in pts[1:]:
        parts.append(f"L {x:.2f} {y:.2f}")
    parts.append("Z")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Standalone validation
# ---------------------------------------------------------------------------

def _validate(image_path: str, out_path: Optional[str] = None) -> None:
    # This function is only called when running this module directly.
    # It runs the entire pipeline and checks that a valid SVG was produced.
    from PIL import Image
    from bridgeit.pipeline.trace import trace_contours
    from bridgeit.pipeline.analyze import analyze_islands
    from bridgeit.pipeline.bridge import add_bridges

    print(f"[export] Processing: {image_path}")
    img = Image.open(image_path).convert("RGBA")
    paths = trace_contours(img)
    analysis = analyze_islands(paths, img.size)
    result = add_bridges(analysis)

    # Default output path: same folder as input, with .svg extension
    if out_path is None:
        out_path = str(Path(image_path).with_suffix(".svg"))

    written = export_svg(result, out_path)
    size = written.stat().st_size
    print(f"[export] SVG written: {written}  ({size} bytes)")

    # Count <path> elements to verify the SVG contains actual cut geometry
    path_count = written.read_text().count("<path ")
    print(f"[export] <path> elements: {path_count}")
    print("[export] PASS" if path_count > 0 else "[export] FAIL — no paths in SVG")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python export.py <rgba_image> [output.svg]")
        sys.exit(1)
    _validate(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
