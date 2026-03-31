"""
mainwindow.py — BridgeIt main application window.

Layout:
  ┌─────────────────────────────────────────────────────┐
  │  Toolbar: [Open] [Export SVG] ──── status label     │
  ├───────────────┬─────────────────────────────────────┤
  │ ControlsPanel │  PreviewPanel                        │
  │  (settings)   │  (drop zone / image / SVG preview)   │
  └───────────────┴─────────────────────────────────────┘

Processing runs in a QThread worker so the UI never blocks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PIL import Image
from PyQt6.QtCore import QObject, QThread, QTimer, Qt, QSize, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QWidget,
)

from bridgeit.config import (
    APP_NAME,
    APP_VERSION,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from bridgeit.gui.themes import current_theme, next_theme, theme_label
from bridgeit.gui.controls import ControlsPanel
from bridgeit.gui.preview import PreviewPanel
from bridgeit.pipeline.pipeline import PipelineResult, PipelineRunner, PipelineSettings, Stage
from bridgeit.gui.canvas import InteractiveCanvas, Mode as CanvasMode
from bridgeit.pipeline.export import make_preview_svg


# Maps each pipeline Stage to a human-readable position number (EXPORT omitted
# from status bar since it's near-instant and the "Done" message follows immediately).
_STAGE_NUM: dict = {
    Stage.REMOVE_BG: 1,
    Stage.TRACE:     2,
    Stage.ANALYZE:   3,
    Stage.BRIDGE:    4,
    Stage.EXPORT:    4,   # export is bundled with bridge step visually
}


def _bridge_rect(
    pt1: tuple,
    pt2: tuple,
    width_px: float,
):
    """Return a closed rectangle path representing a manual bridge.

    Used by _on_export_clicked() to bake manual bridge rectangles into the
    exported SVG as actual cut paths.  This is a duplicate of the same logic
    in canvas.py — kept here so the export code doesn't depend on canvas internals.
    """
    import math
    dx = pt2[0] - pt1[0]
    dy = pt2[1] - pt1[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None   # degenerate bridge — skip it
    ux, uy = dx / length, dy / length   # unit vector along bridge direction
    px, py = -uy, ux                    # perpendicular unit vector
    half = width_px / 2
    a = (pt1[0] + px*half, pt1[1] + py*half)
    b = (pt1[0] - px*half, pt1[1] - py*half)
    c = (pt2[0] - px*half, pt2[1] - py*half)
    d = (pt2[0] + px*half, pt2[1] + py*half)
    return [a, b, c, d, a]   # 5 points — last repeats first to close the shape


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _PipelineWorker(QObject):
    """Runs the pipeline stages in a background QThread.

    Why a background thread?  The pipeline can take several seconds (especially
    the AI background removal step).  If we ran it on the main thread, the UI
    would freeze completely until it finished — no repaints, no clicks, nothing.
    Running it in a QThread keeps the UI responsive while processing happens.

    Qt's thread-safe signal mechanism automatically delivers the finished/error
    signals back to the main thread when the worker is done.
    """

    progress = pyqtSignal(str)    # emitted at each pipeline stage with a status message
    finished = pyqtSignal(object) # emitted with a PipelineResult when the run completes
    error    = pyqtSignal(str)    # emitted with an error message if something goes wrong

    def __init__(
        self,
        runner: PipelineRunner,
        source,
        nobg_image=None,
        preview_only: bool = False,
    ) -> None:
        super().__init__()
        self._runner = runner
        self._source = source
        self._nobg_image = nobg_image
        # preview_only=True skips background removal and reuses the cached image
        self._preview_only = preview_only

    @pyqtSlot()
    def run(self) -> None:
        """Entry point called by the QThread when it starts.

        @pyqtSlot() marks this as a Qt slot so it's called correctly across threads.
        """
        try:
            if self._preview_only and self._nobg_image is not None:
                # Fast re-run: skip background removal, reuse cached nobg_image
                result = self._runner.run_to_preview(self._nobg_image)
            else:
                # Full run: execute in a child process so cv2 never shares Qt's
                # heap.  cv2 corrupts glibc malloc when called from a QThread on
                # some Qt/OpenCV builds; an isolated process avoids this entirely.
                import multiprocessing as _mp
                from bridgeit.pipeline._subprocess_worker import run_pipeline as _target

                ctx = _mp.get_context("spawn")
                q = ctx.Queue()
                p = ctx.Process(
                    target=_target,
                    args=(q, self._source, self._runner.settings),
                )
                p.start()

                # Poll the queue so we can detect if the child dies unexpectedly
                result_tuple = None
                while result_tuple is None:
                    try:
                        result_tuple = q.get(timeout=5)
                    except Exception:  # queue.Empty on timeout
                        if not p.is_alive():
                            raise RuntimeError("Pipeline process terminated unexpectedly")

                p.join(timeout=5)
                tag, value = result_tuple
                if tag == "err":
                    raise RuntimeError(value)
                result = value

            self.finished.emit(result)   # delivers result back to the main thread
        except Exception as exc:
            self.error.emit(str(exc))    # delivers error message back to the main thread


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()

        # ── State variables ───────────────────────────────────────────────
        # Cached background-removed image (reused on settings-change re-runs)
        self._nobg_image: Optional[Image.Image] = None
        # The full result from the most recent pipeline run
        self._last_result: Optional[PipelineResult] = None
        # Background thread and worker object (None when not running)
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[_PipelineWorker] = None
        # Settings waiting to be applied after the current pipeline run finishes
        self._pending_settings: Optional[PipelineSettings] = None
        self._preview_svg: Optional[str] = None

        # ── Canvas edit state (synced to/from the canvas widget) ──────────
        self._excluded_paths: set = set()        # path indices hidden by the user
        self._manual_bridges: list = []          # confirmed manual bridge data
        self._deleted_auto_bridges: set = set()  # auto bridge indices deleted by user

        # ── Bridge toolbar state ──────────────────────────────────────────
        # True when there are staged bridges and the toolbar button acts as "Confirm"
        self._bridge_confirming: bool = False

        # ── Background erase state ────────────────────────────────────────
        # Original PIL Image (pre-processing) — used for colour sampling in erase mode
        self._source_image: Optional[Image.Image] = None
        # Colours the user has sampled for erasure: [(r, g, b), ...]
        self._erase_colors: list = []
        # Index of the bridge currently being resized (-1 = none selected)
        self._editing_bridge_idx: int = -1

        # ── Debounce timer ────────────────────────────────────────────────
        # When the user drags a settings slider, valueChanged fires many times per second.
        # Instead of running the full pipeline on every single event, we wait 250ms after
        # the last change before actually running — this is called "debouncing".
        self._settings_timer = QTimer()
        self._settings_timer.setSingleShot(True)   # fires once, not on a repeating loop
        self._settings_timer.setInterval(250)       # 250ms delay
        self._settings_timer.timeout.connect(self._on_settings_debounced)

        # Tracks every icon button so _apply_theme() can re-render their icons
        # when the user cycles themes.  Each entry is (button, icon_name, is_primary).
        self._icon_btns: list[tuple] = []

        self._build_ui()
        self._apply_theme()

        # Global keyboard shortcuts
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._on_export_clicked)
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self._on_open_clicked)
        QShortcut(QKeySequence("B"), self).activated.connect(self._on_toggle_bridge_mode)
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._on_undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self._on_redo)
        QShortcut(QKeySequence("Home"), self).activated.connect(self._on_fit_view)

        # Controls start disabled — enabled once the first pipeline run completes
        self._controls.set_controls_enabled(False)

        # Colour sampling signal from the image preview (erase mode)
        self._preview.img_preview.color_sampled.connect(self._on_color_sampled)

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.resize(1280, 780)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Build all widgets and lay them out inside the main window.

        Structure:
          QMainWindow
            └── central QWidget
                  ├── QToolBar (top — added separately via addToolBar)
                  └── QSplitter (horizontal)
                        ├── ControlsPanel (left, fixed width)
                        └── PreviewPanel  (right, takes remaining space)
        """
        # QMainWindow needs a "central widget" that fills the window below the toolbar
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Build a custom header bar — replaces QToolBar for full layout control
        header = self._build_header()
        main_layout.addWidget(header)

        # QSplitter lets the user drag the divider to resize the panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)   # very thin divider line
        self._splitter_ref = splitter  # saved so _apply_theme can re-style it

        # Left panel: settings controls
        self._controls = ControlsPanel()
        # When any setting changes, start the debounce timer
        self._controls.settings_changed.connect(self._on_settings_changed)

        # Right panel: drop zone / image preview / canvas
        self._preview = PreviewPanel()
        self._preview.file_dropped.connect(self._on_file_opened)
        # Canvas signals — keep our state in sync with the canvas
        self._preview.canvas.paths_modified.connect(self._on_canvas_modified)
        self._preview.canvas.mode_changed.connect(self._on_canvas_mode_changed)
        self._preview.canvas.selection_changed.connect(self._on_selection_changed)

        splitter.addWidget(self._controls)
        splitter.addWidget(self._preview)
        # stretchFactor(0, 0) = controls don't stretch; stretchFactor(1, 1) = preview takes all extra space
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 1000])   # initial widths in pixels

        main_layout.addWidget(splitter)

        # ── Status bar (bottom of window) ─────────────────────────────────
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        # Text label — updated by _set_status() to show pipeline progress and errors
        self._status_label = QLabel("Ready — open or drop an image to begin")

        # Thin progress bar shown on the right side of the status bar while the pipeline runs.
        # Range (0, 0) = indeterminate "busy" animation (no start/end values needed).
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)   # indeterminate — just pulses to show "working"
        self._progress_bar.setFixedWidth(100)
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.hide()   # hidden until a pipeline run starts

        # addWidget = left-aligned; addPermanentWidget = right-aligned (won't be pushed out)
        self._status_bar.addWidget(self._status_label)
        self._status_bar.addPermanentWidget(self._progress_bar)

    def _build_header(self) -> QWidget:
        """Build a custom integrated header bar — taller and more structured than QToolBar.

        Three sections in a horizontal layout:
          LEFT  — logo + app name + version
          CENTER — tool buttons grouped with separators
          RIGHT  — theme toggle + shortcuts
        """
        t = current_theme()
        header = QWidget()
        header.setFixedHeight(56)
        header.setObjectName("AppHeader")
        header.setStyleSheet(
            f"QWidget#AppHeader {{ background: {t['toolbar_bg']}; "
            f"border-bottom: 2px solid {t['accent']}; }}"
        )
        self._header_ref = header

        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 0, 12, 0)
        hlay.setSpacing(0)

        # ── LEFT: branding ────────────────────────────────────────────────
        self._logo_lbl = QLabel("◆")
        self._logo_lbl.setStyleSheet(
            f"color: {t['accent']}; font-size: 18px; padding-right: 6px;"
        )
        hlay.addWidget(self._logo_lbl)

        self._name_lbl = QLabel(APP_NAME)
        self._name_lbl.setStyleSheet(
            f"color: {t['text']}; font-size: 15px; font-weight: 700; letter-spacing: 0.5px;"
        )
        hlay.addWidget(self._name_lbl)

        ver_lbl = QLabel(f"  v{APP_VERSION}")
        ver_lbl.setStyleSheet(f"color: {t['text_muted']}; font-size: 10px; padding-top: 3px;")
        hlay.addWidget(ver_lbl)

        hlay.addSpacing(20)
        hlay.addWidget(self._header_sep())

        # ── CENTER: tool buttons ──────────────────────────────────────────
        # New icons — chosen for visual distinctiveness across shape and fill:
        #   ◫ = vertical rectangle (document/file)     → Open
        #   ⬆ = solid up arrow (send out)              → Export SVG
        #   ◉ = bullseye (viewfinder)                  → View Original
        #   △ = triangle outline (shape/geometry)      → View Cut Paths
        #   ✕ = bold X (remove)                        → Delete
        #   ⊞ = square-plus (add)                      → Add Bridge
        hlay.addSpacing(8)

        self._btn_open = self._header_btn("open", "Open Image  (Ctrl+O)")
        self._btn_open.clicked.connect(self._on_open_clicked)
        hlay.addWidget(self._btn_open)

        self._btn_export = self._header_btn("export", "Export SVG  (Ctrl+S)", primary=True)
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._on_export_clicked)
        hlay.addWidget(self._btn_export)

        hlay.addSpacing(4)
        hlay.addWidget(self._header_sep())
        hlay.addSpacing(4)

        self._btn_view_image = self._header_btn("original", "Original  (background-removed image)")
        self._btn_view_image.setEnabled(False)
        self._btn_view_image.clicked.connect(self._show_original)
        hlay.addWidget(self._btn_view_image)

        self._btn_view_svg = self._header_btn("paths", "Cut Paths  (interactive canvas)")
        self._btn_view_svg.setEnabled(False)
        self._btn_view_svg.clicked.connect(self._show_svg)
        hlay.addWidget(self._btn_view_svg)

        hlay.addSpacing(4)
        hlay.addWidget(self._header_sep())
        hlay.addSpacing(4)

        self._btn_delete = self._header_btn("delete", "Delete selected  (Delete key)")
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._on_delete_selected)
        hlay.addWidget(self._btn_delete)

        self._btn_add_bridge = self._header_btn("bridge", "Add Bridge  (B)")
        self._btn_add_bridge.setEnabled(False)
        self._btn_add_bridge.setCheckable(True)
        self._btn_add_bridge.clicked.connect(self._on_toggle_bridge_mode)
        hlay.addWidget(self._btn_add_bridge)

        hlay.addSpacing(4)
        hlay.addWidget(self._header_sep())
        hlay.addSpacing(4)

        self._btn_erase = self._header_btn("erase", "Erase Background  — click to enter erase mode")
        self._btn_erase.setEnabled(False)
        self._btn_erase.setCheckable(True)
        self._btn_erase.clicked.connect(self._on_toggle_erase_mode)
        hlay.addWidget(self._btn_erase)

        # ── RIGHT: meta controls ──────────────────────────────────────────
        hlay.addStretch()

        self._btn_theme = self._header_btn("theme", f"Theme: {theme_label()}  (click to cycle)")
        self._btn_theme.clicked.connect(self._on_theme_toggle)
        hlay.addWidget(self._btn_theme)

        btn_help = self._header_btn("shortcuts", "Keyboard shortcuts")
        btn_help.clicked.connect(self._show_shortcuts)
        hlay.addWidget(btn_help)

        return header

    def _header_sep(self) -> QWidget:
        """Thin vertical separator between button groups in the header."""
        t = current_theme()
        sep = QWidget()
        sep.setFixedSize(1, 28)
        sep.setStyleSheet(f"background: {t['border_faint']};")
        return sep

    @staticmethod
    def _make_btn_icon(icon_name: str, icon_color: str, t: dict):
        """Build a QIcon with Normal and Disabled pixmaps so Qt shows a dim icon when disabled.

        Renders the SVG directly to QPixmap twice (full opacity for normal, 30% for
        disabled) rather than calling QIcon.pixmap() which can cause heap corruption
        in some Qt builds when called on a freshly-created QIcon.
        """
        from bridgeit.gui.icons import _ICONS
        from PyQt6.QtCore import QByteArray, Qt
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtGui import QIcon, QPainter, QPixmap

        def _render(color: str, opacity: float = 1.0) -> QPixmap:
            svg = _ICONS[icon_name].replace("COLOR", color)
            renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
            pix = QPixmap(20, 20)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            if opacity < 1.0:
                p.setOpacity(opacity)
            renderer.render(p)
            p.end()
            return pix

        icon = QIcon()
        icon.addPixmap(_render(icon_color), QIcon.Mode.Normal)
        icon.addPixmap(_render(icon_color, opacity=0.3), QIcon.Mode.Disabled)
        return icon

    def _header_btn(self, icon_name: str, tooltip: str, primary: bool = False) -> QPushButton:
        """Create a compact SVG-icon header button with a hover tooltip.

        The icon is rendered from icons.py using the current theme colour.
        A reference is stored in self._icon_btns so _apply_theme() can
        re-render the icon whenever the user cycles themes.
        """
        from bridgeit.gui.icons import make_icon
        t = current_theme()
        icon_color = "#ffffff" if primary else t["text"]

        btn = QPushButton()
        btn.setIcon(self._make_btn_icon(icon_name, icon_color, t))
        btn.setIconSize(QSize(20, 20))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setFixedSize(38, 36)

        if primary:
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: {t['accent']};
                    border: 1px solid {t['accent']};
                    border-radius: 8px;
                    padding: 0;
                }}
                QPushButton:hover {{
                    background: {t['accent_hover']};
                    border-color: {t['accent_hover']};
                }}
                QPushButton:disabled {{
                    background: {t['surface']};
                    border-color: {t['border_faint']};
                }}
                """
            )
        else:
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid transparent;
                    border-radius: 8px;
                    padding: 0;
                }}
                QPushButton:hover {{
                    background: {t['surface_2']};
                    border-color: {t['border']};
                }}
                QPushButton:pressed {{
                    background: {t['surface']};
                }}
                QPushButton:checked {{
                    background: {t['accent_dim']};
                    border-color: {t['accent']};
                }}
                QPushButton:checked:hover {{
                    background: {t['surface_2']};
                    border-color: {t['accent_hover']};
                }}
                QPushButton:disabled {{
                    opacity: 0.35;
                }}
                """
            )

        # Track for icon re-rendering when theme changes
        self._icon_btns.append((btn, icon_name, primary))
        return btn

    def _style_primary_button(self, btn: QPushButton) -> None:
        """Re-apply primary (accent-fill) style to a button — used after theme change."""
        t = current_theme()
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {t['accent']};
                color: #fff;
                border: 1px solid {t['accent']};
                border-radius: 8px;
                font-size: 16px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {t['accent_hover']};
                border-color: {t['accent_hover']};
            }}
            QPushButton:disabled {{
                background: {t['surface']};
                border-color: {t['border_faint']};
                color: {t['border']};
            }}
            """
        )

    def _apply_theme(self) -> None:
        """Re-apply the active theme to every widget in the window.

        We build one large QSS string that covers all widget types and set it
        on QApplication so every widget everywhere (including dialogs) inherits it.
        Then we also re-style the few widgets that cache inline stylesheets.
        """
        t = current_theme()

        # ── Global application stylesheet ─────────────────────────────────
        # Setting this on QApplication makes it the base for every widget,
        # so we don't have to re-style each one individually after a theme change.
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {t["window_bg"]};
                color: {t["text"]};
                font-family: "Ubuntu", "Segoe UI", sans-serif;
            }}
            QWidget#AppHeader {{
                background: {t["toolbar_bg"]};
                border-bottom: 2px solid {t["accent"]};
            }}
            QStatusBar {{
                background: {t["statusbar_bg"]};
                border-top: 1px solid {t["border_faint"]};
                color: {t["text_muted"]};
                font-size: 11px;
                padding: 2px 8px;
            }}
            QSplitter {{
                background: {t["window_bg"]};
            }}
            QSplitter::handle {{
                background: {t["splitter"]};
            }}
            QToolTip {{
                background: {t["tooltip_bg"]};
                color: {t["text"]};
                border: 1px solid {t["tooltip_border"]};
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 11px;
            }}
            QPushButton {{
                background: transparent;
                color: {t["text"]};
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 5px 10px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: {t["surface_2"]};
                border-color: {t["border"]};
            }}
            QPushButton:pressed {{
                background: {t["surface"]};
            }}
            QPushButton:checked {{
                background: {t["accent_dim"]};
                border-color: {t["accent"]};
                color: {t["accent"]};
                font-weight: 600;
            }}
            QPushButton:checked:hover {{
                background: {t["surface_2"]};
                border-color: {t["accent_hover"]};
            }}
            QPushButton:disabled {{
                color: {t["border"]};
            }}
            QDoubleSpinBox, QSpinBox {{
                background: {t["surface"]};
                color: {t["text"]};
                border: 1px solid {t["border"]};
                border-radius: 6px;
                padding: 2px 4px;
            }}
            QDoubleSpinBox:focus, QSpinBox:focus {{
                border-color: {t["accent"]};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {t["border"]};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {t["accent"]};
                border: none;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{
                background: {t["accent"]};
                border-radius: 2px;
            }}
            QLabel {{
                background: transparent;
                color: {t["text"]};
            }}
            QLabel[muted="true"] {{
                color: {t["text_muted"]};
            }}
            QProgressBar {{
                background: {t["surface"]};
                border-radius: 2px;
                border: none;
            }}
            QProgressBar::chunk {{
                background: {t["accent"]};
                border-radius: 2px;
            }}
            QDialog {{
                background: {t["sidebar_bg"]};
                color: {t["text"]};
            }}
            QFrame[frameShape="4"], QFrame[frameShape="5"] {{
                color: {t["border"]};
            }}
        """)

        # ── Per-widget re-styling for items with inline overrides ─────────
        # These widgets set their own inline stylesheets during _build_toolbar /
        # _build_ui, so we need to refresh them after a theme change.
        if hasattr(self, "_header_ref"):
            self._header_ref.setStyleSheet(
                f"QWidget#AppHeader {{ background: {t['toolbar_bg']}; "
                f"border-bottom: 2px solid {t['accent']}; }}"
            )
        if hasattr(self, "_status_bar"):
            self._status_bar.setStyleSheet(
                f"QStatusBar {{ background: {t['statusbar_bg']}; "
                f"border-top: 1px solid {t['border_faint']}; "
                f"color: {t['text_muted']}; font-size: 11px; padding: 2px 8px; }}"
            )
        if hasattr(self, "_status_label"):
            self._status_label.setStyleSheet(
                f"color: {t['text_muted']}; font-size: 11px;"
            )
        if hasattr(self, "_splitter_ref"):
            self._splitter_ref.setStyleSheet(
                f"QSplitter::handle {{ background: {t['splitter']}; }}"
            )
        if hasattr(self, "_controls"):
            self._controls.setStyleSheet(f"background: {t['sidebar_bg']};")
            self._controls.apply_theme(t)
        if hasattr(self, "_preview"):
            self._preview.setStyleSheet(f"background: {t['canvas_bg']};")
            self._preview.canvas.update_theme()
        # Re-render all icon buttons with the new theme colour (including disabled state)
        if hasattr(self, "_icon_btns"):
            for btn, icon_name, is_primary in self._icon_btns:
                icon_color = "#ffffff" if is_primary else t["text"]
                btn.setIcon(self._make_btn_icon(icon_name, icon_color, t))
        # Update theme toggle button tooltip to show next theme name
        if hasattr(self, "_btn_theme"):
            self._btn_theme.setToolTip(f"Theme: {theme_label()}  (click to cycle)")
        if hasattr(self, "_logo_lbl"):
            self._logo_lbl.setStyleSheet(
                f"color: {t['accent']}; font-size: 14px; padding: 0 4px 0 4px;"
            )
        if hasattr(self, "_name_lbl"):
            self._name_lbl.setStyleSheet(
                f"color: {t['text']}; font-size: 15px; font-weight: 700; letter-spacing: 0.5px;"
            )
        if hasattr(self, "_preview"):
            self._preview._drop_zone.update_theme()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _on_theme_toggle(self) -> None:
        """Cycle Dark → Light → Blackout → Dark and re-apply the theme."""
        next_theme()          # advance the global theme state in themes.py
        self._apply_theme()   # re-style every widget with the new palette

    @pyqtSlot()
    def _on_open_clicked(self) -> None:
        """Show the file-open dialog and load the chosen image."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Image",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*)",
        )
        if path:
            self._on_file_opened(path)

    @pyqtSlot(str)
    def _on_file_opened(self, path: str) -> None:
        """Start processing a new image file.

        Shows the raw (un-processed) image immediately so the user has something
        to look at while the pipeline runs in the background.
        """
        # Reset canvas-dependent buttons so stale results from a previous image
        # can't be acted on while the new pipeline run is in progress.
        self._btn_export.setEnabled(False)
        self._btn_view_svg.setEnabled(False)
        self._btn_delete.setEnabled(False)
        self._btn_add_bridge.setEnabled(False)
        self._btn_erase.setEnabled(False)
        self._btn_erase.setChecked(False)
        self._preview.img_preview.set_erase_mode(False)

        # Clear erase colours — new image means fresh start
        self._erase_colors = []

        # Show the original file right away — don't wait for background removal
        try:
            from PIL import Image as _Image
            orig = _Image.open(path)
            orig.load()  # force full decode before storing
            self._source_image = orig.copy()
            self._preview.show_image_from_pil(orig)
            self._btn_view_image.setEnabled(True)
        except Exception:
            self._source_image = None
        self._run_pipeline(source=path, preview_only=False)

    @pyqtSlot(object)
    def _on_settings_changed(self, settings: PipelineSettings) -> None:
        """Called every time any settings control changes value.

        We don't run the pipeline immediately — we store the settings and
        start (or restart) the debounce timer.  This way rapid slider drags
        only trigger one re-run after the user stops moving.
        """
        if self._nobg_image is None:
            return   # no image loaded yet — nothing to re-run
        self._pending_settings = settings
        self._settings_timer.start()   # resets the 250ms countdown on every call

    @pyqtSlot()
    def _on_settings_debounced(self) -> None:
        """Called 250ms after the last settings change — actually applies the update.

        Two paths:
          1. A bridge is selected: resize it in-place without a pipeline re-run.
          2. Normal settings change: run the pipeline with the new settings.
        """
        if self._pending_settings is None:
            return
        settings = self._pending_settings
        self._pending_settings = None

        if self._editing_bridge_idx >= 0:
            # Special case: the Bridge Width control is showing a selected bridge's width.
            # Changing it should resize the bridge, not re-run the whole pipeline.
            from bridgeit.pipeline.bridge import mm_to_px
            width_px = mm_to_px(settings.bridge_width_mm)
            self._preview.canvas.update_selected_bridges_width(width_px)
            self._manual_bridges = self._preview.canvas.get_manual_bridges()
            return

        # If the pipeline is already running, save these settings and apply them
        # after it finishes (the finished handler will check _pending_settings)
        if self._worker_thread and self._worker_thread.isRunning():
            self._pending_settings = settings
            return

        # Normal re-run: skip background removal (preview_only=True)
        self._run_pipeline(source=None, preview_only=True, settings=settings)

    @pyqtSlot()
    def _on_export_clicked(self) -> None:
        """Save the current design as a fabrication-ready SVG file.

        Combines the pipeline's auto-generated paths with any manual edits:
          - Excludes paths the user deleted from the canvas
          - Appends manual bridge rectangles as additional cut paths
        Then writes the final SVG to the user-chosen file path.
        """
        if not self._last_result or not self._last_result.bridge_result:
            return

        # Suggest a filename based on the source image name
        default_name = "output.svg"
        if self._last_result.source_path:
            default_name = self._last_result.source_path.with_suffix(".svg").name

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export SVG",
            default_name,
            "SVG Files (*.svg);;All Files (*)",
        )
        if not path:
            return   # user cancelled the dialog

        try:
            from bridgeit.pipeline.export import export_svg
            from bridgeit.pipeline.bridge import BridgeResult, mm_to_px

            br = self._last_result.bridge_result
            bridge_px = mm_to_px(self._controls.get_settings().bridge_width_mm)

            # Build the final path list:
            # 1. Take all pipeline paths, skipping any the user deleted
            active_paths = [p for i, p in enumerate(br.paths) if i not in self._excluded_paths]

            # 2. Splice manual bridges into their source paths.
            # Unlike the old approach (appending a separate rectangle), this uses
            # the same algorithm as the auto-bridge: it mutates the path that pt1
            # lies on to detour out to pt2 and back, opening a physical tab gap.
            if self._manual_bridges:
                from bridgeit.pipeline.bridge import apply_manual_bridges
                active_paths = apply_manual_bridges(active_paths, self._manual_bridges)

            # Wrap the modified paths back into a BridgeResult so export_svg can use it
            modified_br = BridgeResult(
                paths=active_paths,
                bridges=br.bridges,
                image_size=br.image_size,
            )
            written = export_svg(modified_br, path)
            self._set_status(f"Exported: {written}", success=True)
        except Exception as exc:
            self._set_status(f"Export failed: {exc}", error=True)

    @pyqtSlot()
    def _show_original(self) -> None:
        if self._nobg_image:
            self._preview.show_image_from_pil(self._nobg_image)

    @pyqtSlot()
    def _show_svg(self) -> None:
        if self._last_result and self._last_result.bridge_result:
            self._preview.show_canvas()
            self._preview.canvas.setFocus()

    @pyqtSlot()
    def _on_selection_changed(self) -> None:
        """Called whenever the canvas selection changes.

        If confirmed bridges are selected, switch the Bridge Width control to
        "bridge editing mode" — it shows the selected bridge's width and lets
        the user adjust it.  If nothing (or only paths) is selected, revert
        the control to normal "new bridge width" mode.
        """
        from bridgeit.pipeline.bridge import px_to_mm
        canvas = self._preview.canvas
        bridges = canvas.get_selected_confirmed_bridges()  # [(index, width_px), ...]
        if bridges:
            # At least one confirmed bridge is selected — enter editing mode
            self._editing_bridge_idx = bridges[0][0]   # ≥0 flags editing mode to the debouncer
            # Reflect the first selected bridge's width in the spin-box
            width_mm = round(px_to_mm(bridges[0][1]), 2)
            self._controls.set_bridge_width_mm(width_mm)
            self._controls.set_bridge_editing_mode(True, count=len(bridges))
        else:
            # No bridges selected — leave editing mode
            self._editing_bridge_idx = -1
            self._controls.set_bridge_editing_mode(False)

    @pyqtSlot()
    def _on_delete_selected(self) -> None:
        self._preview.canvas.delete_selected()

    @pyqtSlot()
    def _show_shortcuts(self) -> None:
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox
        t = current_theme()
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.setMinimumWidth(440)
        dlg.setStyleSheet(
            f"background: {t['sidebar_bg']}; color: {t['text']}; font-size: 12px;"
        )

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(0)

        sections = [
            ("GLOBAL", [
                ("Ctrl+O",                "Open image file"),
                ("Ctrl+S",                "Export SVG"),
                ("B",                     "Toggle bridge mode"),
                ("Ctrl+Z",                "Undo last delete or bridge confirm"),
                ("Ctrl+Shift+Z",          "Redo"),
                ("Home",                  "Fit canvas to window"),
                ("Erase button",          "Click background pixels to remove colour range"),
            ]),
            ("NAVIGATION", [
                ("Scroll wheel",          "Zoom in / out"),
                ("Middle-click drag",     "Pan canvas"),
            ]),
            ("SELECT MODE", [
                ("Click",                 "Select a path or bridge"),
                ("Ctrl+click / Shift+click", "Add to selection"),
                ("Click+drag",            "Rubber-band select region"),
                ("Delete / Backspace",    "Remove selected items"),
                ("Escape",                "Deselect all"),
            ]),
            ("BRIDGE MODE", [
                ("Click (pt 1)",          "Place first endpoint (snaps to path)"),
                ("Click (pt 2)",          "Place second endpoint & stage bridge"),
                ("Shift+click (pt 2)",    "Straight bridge (0°/45°/90°/135°)"),
                ("Click staged bridge",   "Select staged bridge"),
                ("Delete",                "Remove selected staged bridge"),
                ("Enter",                 "Confirm all staged bridges"),
                ("Escape",                "Cancel pending point → clear staged → exit"),
            ]),
        ]

        for section_title, rows in sections:
            # Section header
            hdr = QLabel(section_title)
            hdr.setStyleSheet(
                f"color: {t['text_muted']}; font-size: 10px; font-weight: 600;"
                f"letter-spacing: 1px; padding-top: 16px; padding-bottom: 4px;"
            )
            layout.addWidget(hdr)

            # Divider
            div = QFrame()
            div.setFrameShape(QFrame.Shape.HLine)
            div.setStyleSheet(f"color: {t['border']};")
            layout.addWidget(div)

            for key, desc in rows:
                row = QHBoxLayout()
                row.setContentsMargins(0, 5, 0, 0)
                key_lbl = QLabel(key)
                key_lbl.setStyleSheet(
                    f"color: {t['text']}; font-family: monospace; font-size: 11px;"
                    f"background: {t['surface']}; border: 1px solid {t['border']};"
                    f"border-radius: 4px; padding: 1px 6px;"
                )
                key_lbl.setFixedWidth(200)
                desc_lbl = QLabel(desc)
                desc_lbl.setStyleSheet(f"color: {t['text_muted']}; font-size: 11px;")
                row.addWidget(key_lbl)
                row.addWidget(desc_lbl)
                row.addStretch()
                layout.addLayout(row)

        layout.addSpacing(16)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.setStyleSheet(
            f"QPushButton {{ background: {t['surface']}; color: {t['text']};"
            f"border: 1px solid {t['border']}; border-radius: 8px; padding: 5px 16px; }}"
            f"QPushButton:hover {{ background: {t['surface_2']}; }}"
        )
        btns.rejected.connect(dlg.accept)
        layout.addWidget(btns)

        dlg.exec()

    @pyqtSlot()
    def _on_toggle_bridge_mode(self) -> None:
        """Handle clicks on the '+ Bridge' / 'Confirm Bridge' toolbar button.

        The button has three behaviours depending on current state:
          1. Staged bridges exist   → button says "Confirm Bridge(s)" → confirm them
          2. Already in bridge mode → button says "Cancel Bridge"     → exit bridge mode
          3. In select mode         → button says "+ Bridge"          → enter bridge mode
        """
        canvas = self._preview.canvas
        if self._bridge_confirming:
            # Staged bridges are waiting — confirm them all
            canvas.confirm_staged_bridges()
            return
        if canvas.mode == CanvasMode.BRIDGE:
            # Already in bridge mode — cancel and return to select mode
            canvas.set_mode(CanvasMode.SELECT)
            self._btn_add_bridge.setChecked(False)
        else:
            # Enter bridge mode — sync the canvas's bridge width from the settings panel
            from bridgeit.pipeline.bridge import mm_to_px
            canvas.bridge_width_px = mm_to_px(self._controls.get_settings().bridge_width_mm)
            canvas.set_mode(CanvasMode.BRIDGE)
            self._btn_add_bridge.setChecked(True)
        # Make sure the canvas is visible and has keyboard focus
        self._preview.show_canvas()
        canvas.setFocus()

    @pyqtSlot()
    def _on_canvas_modified(self) -> None:
        """Called when the user deletes paths/bridges or confirms new bridges.

        Reads the current canvas state back into our local variables, then
        reloads the canvas from the pipeline result so all items are redrawn
        cleanly with the latest edits applied.
        """
        if not self._last_result or not self._last_result.bridge_result:
            return

        # Leave bridge-editing mode (the layout of bridges has changed)
        self._editing_bridge_idx = -1
        self._controls.set_bridge_editing_mode(False)

        # Sync our local state from the canvas before reloading
        canvas = self._preview.canvas
        self._excluded_paths        = canvas.get_excluded()
        self._manual_bridges        = canvas.get_manual_bridges()
        self._deleted_auto_bridges  = canvas.get_deleted_auto_bridges()

        # Reload the canvas — this redraws all items from the pipeline result,
        # applying the updated excluded/manual/deleted state
        br = self._last_result.bridge_result
        canvas.load(
            br.paths,
            br.bridges,
            excluded=self._excluded_paths,
            manual_bridges=self._manual_bridges,
            deleted_auto_bridges=self._deleted_auto_bridges,
        )

    @pyqtSlot(str)
    def _on_canvas_mode_changed(self, mode_str: str) -> None:
        """Update the toolbar button label and status bar text when the canvas mode changes.

        The canvas emits mode_changed with one of four strings:
          "bridge"         — in bridge mode, waiting for first click
          "bridge_pt2"     — first point placed, waiting for second click
          "bridge_confirm" — staged bridges waiting for confirmation
          "select"         — back in normal select mode
        """
        canvas = self._preview.canvas
        n = canvas.staged_count   # number of staged (unconfirmed) bridges

        if mode_str == "bridge_confirm":
            # Bridges staged — button glows orange; tooltip shows count + Enter hint
            self._bridge_confirming = True
            self._btn_add_bridge.setChecked(True)
            label = f"Confirm {n} Bridges  (Enter)" if n > 1 else "Confirm Bridge  (Enter)"
            self._btn_add_bridge.setToolTip(label)
            self._set_status(
                f"{n} bridge{'s' if n != 1 else ''} staged — "
                "place more, or press Enter / click ⊕ to apply  ·  Escape to discard"
            )
        elif mode_str == "bridge_pt2":
            # First point placed — prompt for second click
            self._bridge_confirming = False
            self._btn_add_bridge.setChecked(True)
            self._btn_add_bridge.setToolTip("Cancel Bridge Mode  (Escape)")
            self._set_status(
                "Click second point to complete bridge  ·  hold Shift for straight lines"
            )
        elif mode_str == "bridge":
            # Entered bridge mode — prompt for first click
            self._bridge_confirming = False
            self._btn_add_bridge.setChecked(True)
            self._btn_add_bridge.setToolTip("Cancel Bridge Mode  (Escape)")
            self._set_status(
                "Click a point on a path to start a bridge  ·  hold Shift for straight lines"
            )
        else:  # "select"
            # Back to normal select mode
            self._bridge_confirming = False
            self._btn_add_bridge.setChecked(False)
            self._btn_add_bridge.setToolTip("Add Bridge  (draw a bridge between paths)")
            self._set_status("Select mode — click paths to select, Delete to remove")

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def _run_pipeline(
        self,
        source,
        preview_only: bool,
        settings: Optional[PipelineSettings] = None,
    ) -> None:
        """Create a background worker and start a pipeline run.

        Args:
            source:       Image file path, PIL Image, or None (for preview re-runs).
            preview_only: If True, skip background removal and reuse _nobg_image.
            settings:     Pipeline settings to use (reads from controls panel if None).
        """
        # Don't start a new run if one is already in progress
        if self._worker_thread and self._worker_thread.isRunning():
            return

        if settings is None:
            settings = self._controls.get_settings()

        # Inject erase colours managed by the main window (not in the controls panel)
        settings.erase_colors = list(self._erase_colors)

        # Create a fresh PipelineRunner with the current settings.
        # on_progress only fires for preview-only (fast) re-runs; full pipeline
        # runs happen in a child process where this callback cannot reach the UI.
        runner = PipelineRunner(
            settings=settings,
            on_progress=lambda stage, msg: self._set_status(
                f"{msg}  ({_STAGE_NUM[stage]}/4)"
            ),
        )

        if not preview_only:
            self._set_status("Processing image…")

        # Create the worker object (not a thread itself — it just holds the logic)
        self._worker = _PipelineWorker(
            runner=runner,
            source=source,
            nobg_image=self._nobg_image if preview_only else None,
            preview_only=preview_only,
        )

        # Create a QThread and move the worker into it.
        # Qt requires objects to "live in" a thread before they can run there.
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        # Wire up signals:
        # Thread started → call worker.run()
        self._worker_thread.started.connect(self._worker.run)
        # Worker finished → call our finished handler (back on main thread)
        self._worker.finished.connect(self._on_pipeline_finished)
        self._worker.error.connect(self._on_pipeline_error)
        # After worker finishes (or errors), stop the thread cleanly
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.error.connect(self._worker_thread.quit)

        self._set_busy(True)        # show progress bar, disable Open button
        self._worker_thread.start() # kick off the background thread

    @pyqtSlot(object)
    def _on_pipeline_finished(self, result: PipelineResult) -> None:
        """Called on the main thread when the pipeline worker finishes.

        Updates the UI with the new results and decides whether to switch views.
        The key design decision: if the user is already looking at the canvas,
        we don't switch them back to the image view — that would be annoying.
        """
        self._set_busy(False)
        self._last_result = result

        if result.error:
            self._set_status(f"Error: {result.error}", error=True)
            return

        # Cache the background-removed image so future preview re-runs don't
        # have to redo the slow background removal step
        if result.nobg_image is not None:
            self._nobg_image = result.nobg_image

        # Check whether the canvas is currently visible — we use this below to
        # decide whether to switch the view or stay on the canvas
        on_canvas = self._preview.is_canvas_visible()

        if result.bridge_result:
            if not on_canvas:
                # This is a fresh image load — clear any edits from the previous image
                self._excluded_paths       = set()
                self._manual_bridges       = []
                self._deleted_auto_bridges = set()
            # Load the new paths and bridges into the canvas
            self._preview.canvas.load(
                result.bridge_result.paths,
                result.bridge_result.bridges,
                excluded=self._excluded_paths,
                manual_bridges=self._manual_bridges,
                deleted_auto_bridges=self._deleted_auto_bridges,
            )
            # Enable toolbar buttons that require a loaded design
            self._btn_view_svg.setEnabled(True)
            self._btn_delete.setEnabled(True)
            self._btn_add_bridge.setEnabled(True)

        # Switch to image view — but only if we're not already on the canvas.
        # This prevents the view from jumping away when the user tweaks a setting
        # while working on the canvas.
        if self._nobg_image:
            self._btn_view_image.setEnabled(True)
            if not on_canvas:
                self._preview.show_image_from_pil(self._nobg_image)

        # Update the info card with stats from this run
        islands = len(result.analysis.islands) if result.analysis else 0
        bridges = len(result.bridge_result.bridges) if result.bridge_result else 0
        paths   = len(result.bridge_result.paths)   if result.bridge_result else 0
        self._controls.update_info(islands, bridges, paths, result.elapsed_seconds)

        self._btn_export.setEnabled(True)
        self._btn_erase.setEnabled(True)
        self._controls.set_controls_enabled(True)
        self._set_status(
            f"Done — {islands} island(s), {bridges} bridge(s) in {result.elapsed_seconds:.2f}s",
            success=True,
        )

        # If settings changed while this run was in progress, kick off another run now
        if self._pending_settings:
            self._settings_timer.start()

    @pyqtSlot(str)
    def _on_pipeline_error(self, message: str) -> None:
        self._set_busy(False)
        self._set_status(f"Error: {message}", error=True)

    @pyqtSlot()
    def _on_undo(self) -> None:
        self._preview.canvas.undo()

    @pyqtSlot()
    def _on_redo(self) -> None:
        self._preview.canvas.redo()

    @pyqtSlot()
    def _on_fit_view(self) -> None:
        self._preview.canvas.fit_view()

    @pyqtSlot()
    def _on_toggle_erase_mode(self) -> None:
        """Enter or exit background-erase mode.

        First click: enter erase mode — show original image so the user can
        click on background areas to sample their colour.
        Second click (while in erase mode): clear all sampled colours and exit,
        reverting to auto background removal.
        """
        if self._btn_erase.isChecked():
            # Entering erase mode — show the original image for colour sampling
            if self._source_image is not None:
                self._preview.show_image_from_pil(self._source_image)
            self._preview.img_preview.set_erase_mode(True)
            n = len(self._erase_colors)
            tip = (
                "Erase mode ON — click on background areas to sample colours.  "
                f"({n} colour{'s' if n != 1 else ''} sampled)  "
                "Click button again to clear and exit."
            )
            self._btn_erase.setToolTip(tip)
            self._set_status("Erase mode: click on background areas to remove them")
        else:
            # Exiting erase mode — clear colours, revert to auto bg removal
            self._erase_colors = []
            self._preview.img_preview.set_erase_mode(False)
            self._btn_erase.setToolTip("Erase Background  — click to enter erase mode")
            self._set_status("Erase colours cleared — using auto background removal")
            # Show the nobg image again if we have one
            if self._nobg_image is not None:
                self._preview.show_image_from_pil(self._nobg_image)
            # Re-run so the pipeline reverts to auto-removal
            self._run_pipeline(source=self._last_result.source_path if self._last_result else None,
                               preview_only=False)

    @pyqtSlot(int, int, int)
    def _on_color_sampled(self, r: int, g: int, b: int) -> None:
        """Called when the user clicks on the image in erase mode.

        Adds the sampled colour to the erase list and kicks off a pipeline
        re-run with colour-range erasure applied.
        """
        color = (r, g, b)
        # Avoid duplicates (within ±5 per channel)
        for cr, cg, cb in self._erase_colors:
            if abs(cr - r) < 5 and abs(cg - g) < 5 and abs(cb - b) < 5:
                return
        self._erase_colors.append(color)
        n = len(self._erase_colors)
        self._btn_erase.setToolTip(
            f"Erase mode ON — {n} colour{'s' if n != 1 else ''} sampled.  "
            "Click more areas or click button to clear and exit."
        )
        self._set_status(f"Erase: sampled #{r:02x}{g:02x}{b:02x} — re-running pipeline…")
        # Re-run pipeline with the updated erase colours — pass the already-loaded
        # PIL image directly so the background thread never touches the file on disk
        if self._source_image is not None:
            self._run_pipeline(source=self._source_image, preview_only=False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        """Show/hide the progress indicator and disable/enable the Open button."""
        self._progress_bar.setVisible(busy)
        self._btn_open.setEnabled(not busy)   # prevent opening another file mid-run

    def _set_status(self, message: str, success: bool = False, error: bool = False) -> None:
        """Update the status bar text and colour.

        Args:
            message: Text to show.
            success: If True, colour the text green (done / exported successfully).
            error:   If True, colour the text red (something went wrong).
        """
        t = current_theme()
        color = t["success"] if success else (t["error"] if error else t["text_muted"])
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px; padding: 0;")
        self._status_label.setText(message)

    @property
    def _bridges(self):
        """Convenience accessor for the auto-generated bridge list from the last result."""
        if self._last_result and self._last_result.bridge_result:
            return self._last_result.bridge_result.bridges
        return []
