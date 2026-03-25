"""Global configuration and defaults for BridgeIt."""

APP_NAME = "BridgeIt"
APP_VERSION = "1.0.0"

# Pipeline defaults
DEFAULT_BRIDGE_WIDTH_MM = 0.5
DEFAULT_CONTOUR_SMOOTHING = 2.0       # epsilon factor for approxPolyDP
DEFAULT_MIN_CONTOUR_AREA = 100        # pixels² — ignore noise
DEFAULT_DPI = 96                      # assumed screen DPI for mm↔px conversion

# UI
PREVIEW_BG_COLOR = "#1e1e2e"
ACCENT_COLOR = "#7c3aed"
SURFACE_COLOR = "#2a2a3e"
TEXT_COLOR = "#e2e8f0"
MUTED_COLOR = "#64748b"
SUCCESS_COLOR = "#22c55e"
ERROR_COLOR = "#ef4444"

WINDOW_MIN_WIDTH = 1100
WINDOW_MIN_HEIGHT = 700
