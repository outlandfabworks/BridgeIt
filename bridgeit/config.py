"""Global configuration and defaults for BridgeIt."""

# The app name and version shown in the window title and toolbar
APP_NAME = "BridgeIt"
APP_VERSION = "0.6.0"

# ── Pipeline defaults ──────────────────────────────────────────────────────
# These are the starting values used when no user has changed a setting yet.

# How wide each connecting bridge should be, measured in millimetres.
# Smaller bridges are less visible but may be physically weaker when cut.
DEFAULT_BRIDGE_WIDTH_MM = 0.5

# Controls how much the detected contour outline is smoothed.
# This is the "epsilon" factor for OpenCV's approxPolyDP algorithm,
# which removes tiny zigzags from the traced edges.
DEFAULT_CONTOUR_SMOOTHING = 2.0       # epsilon factor for approxPolyDP

# Any detected shape smaller than this area (in square pixels) is ignored
# as noise — dust, JPEG artefacts, etc.
DEFAULT_MIN_CONTOUR_AREA = 100        # pixels² — ignore noise

# The assumed screen resolution used when converting millimetres to pixels.
# 96 DPI is the standard for most desktop monitors.
DEFAULT_DPI = 96                      # assumed screen DPI for mm↔px conversion

# ── UI colour palette ──────────────────────────────────────────────────────
# Hex colour strings used throughout the stylesheet and canvas rendering.
# All colours are standard HTML hex codes (#RRGGBB).

# The dark background colour for the preview area
PREVIEW_BG_COLOR = "#1e1e2e"

# Purple accent used for highlights, sliders, and active buttons
ACCENT_COLOR = "#7c3aed"

# Slightly lighter dark surface for panels and cards
SURFACE_COLOR = "#2a2a3e"

# Main text colour — off-white for readability on dark backgrounds
TEXT_COLOR = "#e2e8f0"

# Subdued colour for secondary labels and status text
MUTED_COLOR = "#64748b"

# Green for success messages and bridge markers
SUCCESS_COLOR = "#22c55e"

# Red for error messages and selected bridge markers
ERROR_COLOR = "#ef4444"

# ── Window size constraints ────────────────────────────────────────────────
# Prevent the window from being resized so small that the UI breaks.
WINDOW_MIN_WIDTH = 1100
WINDOW_MIN_HEIGHT = 700
