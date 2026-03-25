"""
export.py — SVG export stage.

Converts BridgeResult paths into a clean, standards-compliant SVG file
using svgwrite. The output is a single unified cut path — one stroke,
no fills — ready to send directly to a laser cutter, CNC, or 3D printer
slicer.

SVG coordinate system matches the source image pixels 1:1, with a
viewBox set to the image dimensions. The caller can scale as needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import svgwrite

from bridgeit.pipeline.bridge import BridgeResult
from bridgeit.pipeline.trace import Path2D


# Cut path style — single black stroke, no fill (standard for laser cutters)
CUT_STROKE = "#000000"
CUT_FILL = "none"
CUT_STROKE_WIDTH = "0.1px"   # Hairline for most laser RIP software


def export_svg(
    result: BridgeResult,
    output_path: str | Path,
    stroke_color: str = CUT_STROKE,
    stroke_width: str = CUT_STROKE_WIDTH,
) -> Path:
    """Write all paths to an SVG file.

    Args:
        result: BridgeResult from bridge stage.
        output_path: Destination .svg file path.
        stroke_color: SVG stroke colour (default black).
        stroke_width: SVG stroke-width value.

    Returns:
        Resolved Path of the written file.
    """
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    w, h = result.image_size
    dwg = svgwrite.Drawing(
        filename=str(out),
        size=(f"{w}px", f"{h}px"),
        profile="full",
    )
    dwg.viewbox(0, 0, w, h)

    # Metadata
    dwg.set_desc(title="BridgeIt Export", desc="Fabrication-ready cut path with bridges")

    # Group all cut paths
    cut_group = dwg.g(
        id="cut_paths",
        stroke=stroke_color,
        fill=CUT_FILL,
        **{"stroke-width": stroke_width, "stroke-linecap": "round", "stroke-linejoin": "round"},
    )

    for i, path in enumerate(result.paths):
        if len(path) < 2:
            continue
        d = _path_to_svg_d(path)
        cut_group.add(dwg.path(d=d, id=f"path_{i}"))

    dwg.add(cut_group)
    dwg.save(pretty=True)
    return out


def export_svg_string(
    result: BridgeResult,
    stroke_color: str = CUT_STROKE,
    stroke_width: str = CUT_STROKE_WIDTH,
) -> str:
    """Return SVG content as a string (for preview rendering)."""
    import io
    import tempfile, os

    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        export_svg(result, tmp_path, stroke_color, stroke_width)
        with open(tmp_path, "r", encoding="utf-8") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _path_to_svg_d(path: Path2D) -> str:
    """Convert a list of (x, y) points to an SVG path `d` attribute string."""
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
    from pathlib import Path

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

    svg_text = written.read_text()
    path_count = svg_text.count("<path ")
    print(f"[export] <path> elements: {path_count}")
    print("[export] PASS" if path_count > 0 else "[export] FAIL — no paths in SVG")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python export.py <rgba_image> [output.svg]")
        sys.exit(1)
    _validate(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
