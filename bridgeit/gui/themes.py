"""
themes.py — Centralised theme definitions for BridgeIt.

Three themes:
  dark     — Ubuntu terminal aubergine, Ubuntu orange accent
  light    — Ubuntu Yaru light, Ubuntu orange accent
  blackout — Pure OLED black, Ubuntu orange accent

Usage:
    from bridgeit.gui.themes import current_theme, next_theme, theme_name

    t = current_theme()
    bg = t["window_bg"]
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Theme dictionaries
# ---------------------------------------------------------------------------

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        # Main window and panel backgrounds
        "window_bg":   "#2d0922",   # Ubuntu terminal aubergine
        "toolbar_bg":  "#1a0613",   # slightly darker aubergine for top bar
        "sidebar_bg":  "#230818",   # subtle tint for the settings panel
        "surface":     "#3d1a30",   # raised card / input surfaces
        "surface_2":   "#4a2540",   # hover state for surfaces

        # Borders and dividers
        "border":      "#6b3060",
        "border_faint":"#3d1a30",

        # Text
        "text":        "#ffffff",
        "text_muted":  "#c4a0b8",   # muted lavender-pink for secondary labels

        # Ubuntu orange accent
        "accent":      "#E95420",
        "accent_hover":"#f5703a",
        "accent_dim":  "rgba(233, 84, 32, 0.18)",

        # Semantic colours
        "success":     "#4e9a06",
        "error":       "#cc0000",

        # Canvas / preview area
        "canvas_bg":   "#1a0613",

        # Splitter handle
        "splitter":    "#3d1a30",

        # Status bar
        "statusbar_bg":"#100408",

        # Tooltip
        "tooltip_bg":  "#3d1a30",
        "tooltip_border": "#6b3060",
    },

    "light": {
        "window_bg":   "#f6f5f4",   # Ubuntu Yaru light grey
        "toolbar_bg":  "#e6e4e2",
        "sidebar_bg":  "#ede9e6",
        "surface":     "#ffffff",
        "surface_2":   "#f0eeec",

        "border":      "#cbc7c4",
        "border_faint":"#e0dedd",

        "text":        "#3d3d3d",
        "text_muted":  "#8b8685",

        "accent":      "#E95420",
        "accent_hover":"#f5703a",
        "accent_dim":  "rgba(233, 84, 32, 0.12)",

        "success":     "#26a269",
        "error":       "#c01c28",

        "canvas_bg":   "#dfdbd8",

        "splitter":    "#cbc7c4",

        "statusbar_bg":"#d5d1ce",

        "tooltip_bg":  "#ffffff",
        "tooltip_border": "#cbc7c4",
    },

    "blackout": {
        "window_bg":   "#000000",   # pure OLED black
        "toolbar_bg":  "#000000",
        "sidebar_bg":  "#080808",
        "surface":     "#111111",
        "surface_2":   "#1c1c1c",

        "border":      "#2a2a2a",
        "border_faint":"#181818",

        "text":        "#e8e8e8",
        "text_muted":  "#555555",

        "accent":      "#E95420",
        "accent_hover":"#f5703a",
        "accent_dim":  "rgba(233, 84, 32, 0.18)",

        "success":     "#33d17a",
        "error":       "#e01b24",

        "canvas_bg":   "#000000",

        "splitter":    "#1c1c1c",

        "statusbar_bg":"#000000",

        "tooltip_bg":  "#1c1c1c",
        "tooltip_border": "#2a2a2a",
    },
}

# Cycle order for the theme toggle button
THEME_ORDER = ["dark", "light", "blackout"]

# Human-readable labels shown in the toolbar tooltip
THEME_LABELS = {
    "dark":     "Dark (Aubergine)",
    "light":    "Light (Yaru)",
    "blackout": "Blackout (OLED)",
}

# ---------------------------------------------------------------------------
# Active theme state
# ---------------------------------------------------------------------------

_current: str = "dark"


def current_theme() -> dict[str, str]:
    """Return the active theme colour dictionary."""
    return THEMES[_current]


def theme_name() -> str:
    """Return the name key of the active theme (e.g. 'dark')."""
    return _current


def theme_label() -> str:
    """Return a human-readable label for the active theme."""
    return THEME_LABELS[_current]


def next_theme() -> str:
    """Advance to the next theme and return its name key."""
    global _current
    idx = THEME_ORDER.index(_current)
    _current = THEME_ORDER[(idx + 1) % len(THEME_ORDER)]
    return _current
