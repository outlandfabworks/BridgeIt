"""
icons.py — SVG icon definitions for the BridgeIt toolbar.

Each icon is defined as an SVG string with "COLOR" as a placeholder,
which is replaced at render time with the actual theme colour.

Usage:
    from bridgeit.gui.icons import make_icon
    btn.setIcon(make_icon("open", color="#ffffff", size=20))
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QRectF, QSize
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtCore import Qt

# ---------------------------------------------------------------------------
# SVG definitions — 20×20 viewBox, "COLOR" replaced at render time
# ---------------------------------------------------------------------------

_ICONS: dict[str, str] = {

    # Open folder — classic tab-folder silhouette
    "open": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <path d="M2 7 L2 16 L18 16 L18 7 L9.5 7 L7.5 5 L2 5 Z"
            stroke="COLOR" stroke-width="1.6" stroke-linejoin="round"/>
    </svg>""",

    # Export — upward arrow rising from a tray
    "export": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <rect x="3" y="13" width="14" height="4" rx="1.5"
            stroke="COLOR" stroke-width="1.5"/>
      <line x1="10" y1="10" x2="10" y2="2"
            stroke="COLOR" stroke-width="1.6" stroke-linecap="round"/>
      <polyline points="7,5.5 10,2 13,5.5"
                stroke="COLOR" stroke-width="1.6"
                stroke-linejoin="round" stroke-linecap="round"/>
    </svg>""",

    # Original — eye / viewfinder
    "original": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <path d="M2 10 Q10 3 18 10 Q10 17 2 10 Z"
            stroke="COLOR" stroke-width="1.5" stroke-linejoin="round"/>
      <circle cx="10" cy="10" r="2.8"
              stroke="COLOR" stroke-width="1.5"/>
      <circle cx="10" cy="10" r="1"
              fill="COLOR"/>
    </svg>""",

    # Paths — bezier curve with anchor nodes at each end
    "paths": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <path d="M3 15 C4 5 16 5 17 15"
            stroke="COLOR" stroke-width="1.6" stroke-linecap="round"/>
      <circle cx="3"  cy="15" r="2.2" fill="COLOR"/>
      <circle cx="17" cy="15" r="2.2" fill="COLOR"/>
      <circle cx="4"  cy="6"  r="1.4" stroke="COLOR" stroke-width="1.2" fill="none"/>
      <circle cx="16" cy="6"  r="1.4" stroke="COLOR" stroke-width="1.2" fill="none"/>
    </svg>""",

    # Delete — trash can with lid and three internal lines
    "delete": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <path d="M5 7.5 L5.5 16.5 Q5.5 17 6 17 L14 17 Q14.5 17 14.5 16.5 L15 7.5"
            stroke="COLOR" stroke-width="1.5" stroke-linejoin="round"/>
      <line x1="3"  y1="7.5" x2="17" y2="7.5"
            stroke="COLOR" stroke-width="1.6" stroke-linecap="round"/>
      <path d="M8 7.5 L8 5.5 Q8 5 8.5 5 L11.5 5 Q12 5 12 5.5 L12 7.5"
            stroke="COLOR" stroke-width="1.4" stroke-linejoin="round"/>
      <line x1="8"  y1="10" x2="8"  y2="14.5"
            stroke="COLOR" stroke-width="1.2" stroke-linecap="round"/>
      <line x1="10" y1="10" x2="10" y2="14.5"
            stroke="COLOR" stroke-width="1.2" stroke-linecap="round"/>
      <line x1="12" y1="10" x2="12" y2="14.5"
            stroke="COLOR" stroke-width="1.2" stroke-linecap="round"/>
    </svg>""",

    # Bridge — two horizontal rails with a filled rectangular tab between them
    "bridge": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <line x1="2"  y1="6"  x2="18" y2="6"
            stroke="COLOR" stroke-width="1.8" stroke-linecap="round"/>
      <line x1="2"  y1="14" x2="18" y2="14"
            stroke="COLOR" stroke-width="1.8" stroke-linecap="round"/>
      <rect x="8.5" y="6" width="3" height="8"
            fill="COLOR"/>
    </svg>""",

    # Theme toggle — circle split vertically: left half filled, right half outlined
    "theme": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">
      <path d="M10 3 A7 7 0 0 0 10 17 Z"
            fill="COLOR"/>
      <circle cx="10" cy="10" r="7"
              fill="none" stroke="COLOR" stroke-width="1.5"/>
    </svg>""",

    # Shortcuts — keyboard outline with three key rows
    "shortcuts": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <rect x="2" y="5" width="16" height="11" rx="2"
            stroke="COLOR" stroke-width="1.5"/>
      <line x1="5"   y1="9"  x2="6.5" y2="9"
            stroke="COLOR" stroke-width="1.5" stroke-linecap="round"/>
      <line x1="9.5" y1="9"  x2="10.5" y2="9"
            stroke="COLOR" stroke-width="1.5" stroke-linecap="round"/>
      <line x1="13.5" y1="9" x2="15"  y2="9"
            stroke="COLOR" stroke-width="1.5" stroke-linecap="round"/>
      <line x1="6.5" y1="12.5" x2="13.5" y2="12.5"
            stroke="COLOR" stroke-width="1.6" stroke-linecap="round"/>
    </svg>""",

    # Erase background — eraser shape: rectangle body + baseline
    "erase": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <rect x="3" y="7" width="12" height="7" rx="1.5"
            stroke="COLOR" stroke-width="1.5"/>
      <line x1="9" y1="7" x2="9" y2="14"
            stroke="COLOR" stroke-width="1.5"/>
      <line x1="2" y1="16.5" x2="18" y2="16.5"
            stroke="COLOR" stroke-width="1.5" stroke-linecap="round"/>
    </svg>""",

    # Auto Bridge — magic wand with sparkle star at tip
    "auto_bridge": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <line x1="3" y1="17" x2="11.5" y2="8.5"
            stroke="COLOR" stroke-width="1.5" stroke-linecap="round"/>
      <path d="M13 3 L14 6 L17 7 L14 8 L13 11 L12 8 L9 7 L12 6 Z"
            stroke="COLOR" stroke-width="1.2" stroke-linejoin="round"/>
    </svg>""",

    # Trace Selection — dashed pentagon with dots at vertices (polygon lasso tool)
    "crop": """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
  <polygon points="10,2 17,7 14,17 6,17 3,7"
           stroke="COLOR" stroke-width="1.5" stroke-dasharray="3 1.5"
           stroke-linejoin="round" fill="none"/>
  <circle cx="10" cy="2"  r="1.8" fill="COLOR"/>
  <circle cx="17" cy="7"  r="1.8" fill="COLOR"/>
  <circle cx="14" cy="17" r="1.8" fill="COLOR"/>
  <circle cx="6"  cy="17" r="1.8" fill="COLOR"/>
  <circle cx="3"  cy="7"  r="1.8" fill="COLOR"/>
</svg>""",

    # Export SVG Image — filled rectangle with a down-arrow (image/vector export)
    "export_image": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <rect x="3" y="3" width="10" height="10" rx="1.5"
            fill="COLOR" opacity="0.35"/>
      <rect x="3" y="3" width="10" height="10" rx="1.5"
            stroke="COLOR" stroke-width="1.5"/>
      <line x1="10" y1="11" x2="10" y2="18"
            stroke="COLOR" stroke-width="1.5" stroke-linecap="round"/>
      <polyline points="7,15 10,18 13,15"
                stroke="COLOR" stroke-width="1.5"
                stroke-linecap="round" stroke-linejoin="round"/>
    </svg>""",

    # Export DXF — up-arrow with "DXF" text label
    "export_dxf": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <rect x="3" y="13" width="14" height="4" rx="1.5"
            stroke="COLOR" stroke-width="1.5"/>
      <line x1="10" y1="10" x2="10" y2="2"
            stroke="COLOR" stroke-width="1.6" stroke-linecap="round"/>
      <polyline points="7,5.5 10,2 13,5.5"
                stroke="COLOR" stroke-width="1.6"
                stroke-linejoin="round" stroke-linecap="round"/>
      <text x="10" y="16.8" font-size="3.8" font-family="monospace"
            text-anchor="middle" fill="COLOR" stroke="none">DXF</text>
    </svg>""",

    # About — circle with "i" inside
    "about": """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none">
      <circle cx="10" cy="10" r="7.5" stroke="COLOR" stroke-width="1.5"/>
      <line x1="10" y1="9" x2="10" y2="14"
            stroke="COLOR" stroke-width="1.8" stroke-linecap="round"/>
      <circle cx="10" cy="6.5" r="1" fill="COLOR"/>
    </svg>""",
}

# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def make_icon(name: str, color: str = "#ffffff", size: int = 20) -> QIcon:
    """Render a named SVG icon as a QIcon using the given fill/stroke colour.

    The SVG template uses "COLOR" as a placeholder which is substituted before
    rendering.  The result is anti-aliased and transparent-background.

    Args:
        name:   Key into _ICONS dict (e.g. "open", "export").
        color:  CSS hex colour string (e.g. "#e2e8f0").
        size:   Pixel size for the square pixmap (default 20).

    Returns:
        A QIcon with a single pixmap at the requested size.
        Returns an empty QIcon if the name is not recognised.
    """
    template = _ICONS.get(name)
    if not template:
        return QIcon()

    svg_bytes = QByteArray(template.replace("COLOR", color).encode("utf-8"))

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    renderer = QSvgRenderer(svg_bytes)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()

    return QIcon(pixmap)


def icon_names() -> list[str]:
    """Return the list of available icon names."""
    return list(_ICONS.keys())
