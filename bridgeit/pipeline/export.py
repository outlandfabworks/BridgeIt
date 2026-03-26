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

import svgwrite

from bridgeit.config import ACCENT_COLOR, PREVIEW_BG_COLOR
from bridgeit.pipeline.bridge import BridgeResult
from bridgeit.pipeline.trace import Path2D

# Fabrication export style
CUT_STROKE = "#000000"
CUT_FILL = "none"
CUT_STROKE_WIDTH = "0.1px"   # Hairline for laser RIP software

# Preview style
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
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    w, h = result.image_size
    dwg = svgwrite.Drawing(filename=str(out), size=(f"{w}px", f"{h}px"), profile="full")
    dwg.viewbox(0, 0, w, h)
    dwg.set_desc(title="BridgeIt Export", desc="Fabrication-ready cut path with bridges")

    cut_group = dwg.g(
        id="cut_paths",
        stroke=stroke_color,
        fill=CUT_FILL,
        **{"stroke-width": stroke_width, "stroke-linecap": "round", "stroke-linejoin": "round"},
    )
    for i, path in enumerate(result.paths):
        if len(path) < 2:
            continue
        cut_group.add(dwg.path(d=_path_to_svg_d(path), id=f"path_{i}"))

    dwg.add(cut_group)
    dwg.save(pretty=True)
    return out


def make_preview_svg(result: BridgeResult) -> str:
    """Return an SVG string for on-screen preview.

    Uses white strokes on the app's dark background colour, and overlays
    green markers + lines wherever bridges connect islands to the design.
    """
    w, h = result.image_size

    dwg = svgwrite.Drawing(size=(f"{w}px", f"{h}px"), profile="full")
    dwg.viewbox(0, 0, w, h)

    # Dark background matching the app theme
    dwg.add(dwg.rect(insert=(0, 0), size=(w, h), fill=PREVIEW_BG_COLOR))

    # Cut paths — white strokes
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

    # Bridge markers — green lines + endpoint dots
    if result.bridges:
        bridge_group = dwg.g(id="bridges")
        for b in result.bridges:
            ix, iy = b.island_pt
            tx, ty = b.target_pt

            # Connecting line
            bridge_group.add(dwg.line(
                start=(ix, iy), end=(tx, ty),
                stroke=BRIDGE_COLOR,
                **{"stroke-width": "2px", "stroke-dasharray": "6,3"},
            ))
            # Endpoint dots
            for cx, cy in [(ix, iy), (tx, ty)]:
                bridge_group.add(dwg.circle(
                    center=(cx, cy), r=BRIDGE_MARKER_R,
                    fill=BRIDGE_COLOR, stroke="none",
                ))
        dwg.add(bridge_group)

    # Serialise without writing to disk
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        dwg.filename = tmp_path
        dwg.save(pretty=False)
        with open(tmp_path, "r", encoding="utf-8") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def export_svg_string(result: BridgeResult) -> str:
    """Legacy helper — returns fabrication SVG as a string."""
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        export_svg(result, tmp_path)
        with open(tmp_path, "r", encoding="utf-8") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _path_to_svg_d(path: Path2D) -> str:
    if not path:
        return ""
    parts = [f"M {path[0][0]:.3f} {path[0][1]:.3f}"]
    for x, y in path[1:]:
        parts.append(f"L {x:.3f} {y:.3f}")
    parts.append("Z")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Standalone validation
# ---------------------------------------------------------------------------

def _validate(image_path: str, out_path: Optional[str] = None) -> None:
    from PIL import Image
    from bridgeit.pipeline.trace import trace_contours
    from bridgeit.pipeline.analyze import analyze_islands
    from bridgeit.pipeline.bridge import add_bridges

    print(f"[export] Processing: {image_path}")
    img = Image.open(image_path).convert("RGBA")
    paths = trace_contours(img)
    analysis = analyze_islands(paths, img.size)
    result = add_bridges(analysis)

    if out_path is None:
        out_path = str(Path(image_path).with_suffix(".svg"))

    written = export_svg(result, out_path)
    size = written.stat().st_size
    print(f"[export] SVG written: {written}  ({size} bytes)")
    path_count = written.read_text().count("<path ")
    print(f"[export] <path> elements: {path_count}")
    print("[export] PASS" if path_count > 0 else "[export] FAIL — no paths in SVG")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python export.py <rgba_image> [output.svg]")
        sys.exit(1)
    _validate(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
