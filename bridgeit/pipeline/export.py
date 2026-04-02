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


def _path_to_svg_d(path: Path2D) -> str:
    # Convert a list of (x, y) tuples into an SVG path data string.
    # M = moveto (pen-up move to start), L = lineto (draw a line), Z = closepath.
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
