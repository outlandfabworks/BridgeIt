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

import logging
import os
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger(__name__)

from PIL import Image
from PyQt6.QtCore import QObject, QThread, QTimer, Qt, QSize, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMenu,
    QMessageBox,
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


# Maps each pipeline Stage to a human-readable position number (EXPORT omitted
# from status bar since it's near-instant and the "Done" message follows immediately).
_STAGE_NUM: dict = {
    Stage.REMOVE_BG: 1,
    Stage.TRACE:     2,
    Stage.ANALYZE:   3,
    Stage.BRIDGE:    4,
    Stage.EXPORT:    4,   # export is bundled with bridge step visually
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_dialog_theme(dlg: "QMessageBox") -> None:
    """Apply the app's dark theme stylesheet to a QMessageBox so it doesn't
    appear as a jarring system-native dialog."""
    from bridgeit.gui.themes import current_theme
    t = current_theme()
    dlg.setStyleSheet(f"""
        QMessageBox {{
            background-color: {t['surface']};
            color: {t['text']};
        }}
        QMessageBox QLabel {{
            color: {t['text']};
            font-size: 13px;
        }}
        QMessageBox QPushButton {{
            background-color: {t['border']};
            color: {t['text']};
            border: 1px solid {t['border']};
            border-radius: 4px;
            padding: 5px 16px;
            font-size: 12px;
            min-width: 64px;
        }}
        QMessageBox QPushButton:hover {{
            background-color: {t['accent']};
            color: #ffffff;
            border-color: {t['accent']};
        }}
        QMessageBox QPushButton:default {{
            border-color: {t['accent']};
        }}
        QTextEdit {{
            background-color: {t['bg']};
            color: {t['text_muted']};
            font-family: monospace;
            font-size: 11px;
            border: 1px solid {t['border']};
        }}
    """)


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

        Both full runs and preview-only re-runs execute in a child process
        so cv2 (trace_contours) never runs inside a QThread.  cv2 corrupts
        glibc malloc when called from a QThread on some Qt/OpenCV builds;
        an isolated subprocess avoids this entirely.
        """
        try:
            import multiprocessing as _mp
            from bridgeit.pipeline._subprocess_worker import (
                run_pipeline as _full_target,
                run_preview as _preview_target,
            )

            ctx = _mp.get_context("spawn")
            q = ctx.Queue()

            if self._preview_only and self._nobg_image is not None:
                p = ctx.Process(
                    target=_preview_target,
                    args=(q, self._nobg_image, self._runner.settings),
                )
            else:
                p = ctx.Process(
                    target=_full_target,
                    args=(q, self._source, self._runner.settings),
                )

            p.start()

            # Poll the queue so we detect if the child dies unexpectedly
            result_tuple = None
            while result_tuple is None:
                try:
                    result_tuple = q.get(timeout=5)
                except Exception:  # queue.Empty on timeout
                    if not p.is_alive():
                        raise RuntimeError("Pipeline process terminated unexpectedly")

            # Clean up child process — terminate first as fallback if it
            # hasn't exited yet, then join to release OS resources
            if p.is_alive():
                p.terminate()
            p.join(timeout=5)
            if p.is_alive():
                p.kill()   # last resort

            tag, value = result_tuple
            if tag == "err":
                raise RuntimeError(value)
            result = value

            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Update checker
# ---------------------------------------------------------------------------

class _UpdateChecker(QThread):
    """Background thread that checks GitHub for a newer release.

    Emits update_available(latest_version) if a newer tag is found.
    Silently swallows all network/parse errors.
    """
    update_available = pyqtSignal(str)

    def run(self) -> None:
        try:
            import urllib.request, json
            from bridgeit.config import APP_VERSION
            url = "https://api.github.com/repos/outlandfabworks/BridgeIt/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "BridgeIt-UpdateChecker"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            tag = data.get("tag_name", "").lstrip("vV")
            if tag and tuple(int(x) for x in tag.split(".")) > tuple(int(x) for x in APP_VERSION.split(".")):
                self.update_available.emit(tag)
        except Exception:
            pass


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
        self._update_version: Optional[str] = None

        # ── Canvas edit state (synced to/from the canvas widget) ──────────
        self._excluded_paths: set = set()        # path indices hidden by the user
        self._manual_bridges: list = []          # all confirmed bridges (auto + manual)

        # ── Bridge toolbar state ──────────────────────────────────────────
        # True when there are staged bridges and the toolbar button acts as "Confirm"
        self._bridge_confirming: bool = False

        # ── Background erase state ────────────────────────────────────────
        # Original PIL Image (pre-processing) — used for colour sampling in erase mode
        self._source_image: Optional[Image.Image] = None
        # Colours the user has sampled for erasure: [(r, g, b), ...]
        self._erase_colors: list = []
        # Lasso polygon points in source-image pixel coords: [(x,y), ...] or None
        self._lasso_points: Optional[list] = None
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

        # Auto-clear success/info status messages after 8 s; errors persist until next action.
        self._status_clear_timer = QTimer()
        self._status_clear_timer.setSingleShot(True)
        self._status_clear_timer.setInterval(8000)
        self._status_clear_timer.timeout.connect(self._on_status_timeout)

        # Tracks every icon button so _apply_theme() can re-render their icons
        # when the user cycles themes.  Each entry is (button, icon_name, is_primary).
        self._icon_btns: list[tuple] = []
        # Tracks header separator widgets so their color updates with the theme.
        self._sep_refs: list = []

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
        # Lasso polygon signal from the image preview (trace selection mode)
        self._preview.img_preview.lasso_selected.connect(self._on_lasso_selected)

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.resize(1280, 780)

        # Set window icon from bundled assets
        _icon_path = Path(__file__).parent.parent / "assets" / "icon.png"
        if _icon_path.exists():
            self.setWindowIcon(QIcon(str(_icon_path)))

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

        # Start background update check — button is added in _build_header
        self._update_checker = _UpdateChecker()
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()

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

        self._ver_lbl = QLabel(f"  v{APP_VERSION}")
        self._ver_lbl.setStyleSheet(f"color: {t['text_muted']}; font-size: 10px; padding-top: 3px;")
        hlay.addWidget(self._ver_lbl)

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

        self._btn_export = self._header_btn("export", "Export Cut Paths SVG  (Ctrl+S)", primary=True)
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._on_export_clicked)
        hlay.addWidget(self._btn_export)

        self._btn_export_image = self._header_btn(
            "export_image",
            "Export SVG Image  — filled coloured vector (for use as a graphic, not laser cutting)",
        )
        self._btn_export_image.setEnabled(False)
        self._btn_export_image.clicked.connect(self._on_export_image_svg)
        hlay.addWidget(self._btn_export_image)

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

        self._btn_auto_bridge = self._header_btn(
            "auto_bridge",
            "Auto Bridge  — suggest bridge placements for all islands",
        )
        self._btn_auto_bridge.setEnabled(False)
        self._btn_auto_bridge.clicked.connect(self._on_auto_bridge)
        hlay.addWidget(self._btn_auto_bridge)

        hlay.addSpacing(4)
        hlay.addWidget(self._header_sep())
        hlay.addSpacing(4)

        self._btn_erase = self._header_btn("erase", "Erase Background  — click to enter erase mode  · right-click to clear")
        self._btn_erase.setEnabled(False)
        self._btn_erase.setCheckable(True)
        self._btn_erase.clicked.connect(self._on_toggle_erase_mode)
        self._btn_erase.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._btn_erase.customContextMenuRequested.connect(self._on_erase_context_menu)
        hlay.addWidget(self._btn_erase)

        self._btn_crop = self._header_btn(
            "crop",
            "Trace Selection  — click points around what you want to keep; right-click or click first point to close",
        )
        self._btn_crop.setEnabled(False)
        self._btn_crop.setCheckable(True)
        self._btn_crop.clicked.connect(self._on_toggle_lasso_mode)
        hlay.addWidget(self._btn_crop)

        # ── RIGHT: meta controls ──────────────────────────────────────────
        hlay.addStretch()

        # Update button — hidden until _on_update_available fires
        self._btn_update = QPushButton("⬆ Update available")
        self._btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_update.hide()
        self._btn_update.clicked.connect(self._on_update_clicked)
        hlay.addWidget(self._btn_update)
        hlay.addSpacing(4)

        self._btn_theme = self._header_btn("theme", f"Theme: {theme_label()}  (click to cycle)")
        self._btn_theme.clicked.connect(self._on_theme_toggle)
        hlay.addWidget(self._btn_theme)

        btn_help = self._header_btn("shortcuts", "Keyboard shortcuts")
        btn_help.clicked.connect(self._show_shortcuts)
        hlay.addWidget(btn_help)

        btn_about = self._header_btn("about", "About BridgeIt / Support")
        btn_about.clicked.connect(self._show_about)
        hlay.addWidget(btn_about)

        return header

    def _header_sep(self) -> QWidget:
        """Thin vertical separator between button groups in the header."""
        t = current_theme()
        sep = QWidget()
        sep.setFixedSize(1, 28)
        sep.setStyleSheet(f"background: {t['border_faint']};")
        self._sep_refs.append(sep)
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
                    background: transparent;
                    border-color: transparent;
                    opacity: 0.35;
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
                background: transparent;
                border-color: transparent;
                opacity: 0.35;
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
        # Re-render all icon buttons with the new theme colour and re-apply their
        # inline stylesheets so hover highlight and borders match the new theme.
        if hasattr(self, "_icon_btns"):
            for btn, icon_name, is_primary in self._icon_btns:
                icon_color = "#ffffff" if is_primary else t["text"]
                btn.setIcon(self._make_btn_icon(icon_name, icon_color, t))
                if is_primary:
                    btn.setStyleSheet(
                        f"QPushButton {{"
                        f"  background: {t['accent']};"
                        f"  border: 1px solid {t['accent']};"
                        f"  border-radius: 8px; padding: 0;}}"
                        f"QPushButton:hover {{"
                        f"  background: {t['accent_hover']};"
                        f"  border-color: {t['accent_hover']};}}"
                        f"QPushButton:disabled {{"
                        f"  background: transparent;"
                        f"  border-color: transparent; opacity: 0.35;}}"
                    )
                else:
                    btn.setStyleSheet(
                        f"QPushButton {{"
                        f"  background: transparent;"
                        f"  border: 1px solid transparent;"
                        f"  border-radius: 8px; padding: 0;}}"
                        f"QPushButton:hover {{"
                        f"  background: {t['surface_2']};"
                        f"  border-color: {t['border']};}}"
                        f"QPushButton:pressed {{"
                        f"  background: {t['surface']};}}"
                        f"QPushButton:checked {{"
                        f"  background: {t['accent_dim']};"
                        f"  border-color: {t['accent']};}}"
                        f"QPushButton:checked:hover {{"
                        f"  background: {t['surface_2']};"
                        f"  border-color: {t['accent_hover']};}}"
                        f"QPushButton:disabled {{ opacity: 0.35;}}"
                    )
        # Update separator line colours
        if hasattr(self, "_sep_refs"):
            for sep in self._sep_refs:
                sep.setStyleSheet(f"background: {t['border_faint']};")
        # Update version label
        if hasattr(self, "_ver_lbl"):
            self._ver_lbl.setStyleSheet(
                f"color: {t['text_muted']}; font-size: 10px; padding-top: 3px;"
            )
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
        if hasattr(self, "_btn_update") and self._btn_update.isVisible():
            self._style_update_btn()

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
        # Reset canvas-dependent buttons and info panel so stale results from a
        # previous image can't be acted on while the new pipeline run is in progress.
        self._controls.reset_info()
        self._btn_export.setEnabled(False)
        self._btn_export_image.setEnabled(False)
        self._btn_view_svg.setEnabled(False)
        self._btn_delete.setEnabled(False)
        self._btn_add_bridge.setEnabled(False)
        self._btn_auto_bridge.setEnabled(False)
        self._btn_erase.setEnabled(False)
        self._btn_erase.setChecked(False)
        self._preview.img_preview.set_erase_mode(False)
        self._btn_crop.setEnabled(False)
        self._btn_crop.setChecked(False)
        self._preview.img_preview.set_lasso_mode(False)
        self._preview.img_preview.set_confirmed_lasso(None)
        self._lasso_points = None

        # Clear erase colours — new image means fresh start
        self._erase_colors = []

        # Show the original file right away — don't wait for background removal
        try:
            import os as _os
            _MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB
            if _os.path.getsize(path) > _MAX_FILE_BYTES:
                self._set_status(
                    "File too large (limit: 50 MB) — resize the image or reduce its resolution first",
                    error=True,
                )
                return
            from PIL import Image as _Image
            orig = _Image.open(path)
            orig.load()  # force full decode before storing
            self._source_image = orig.copy()
            self._preview.show_image_from_pil(orig)
            self._btn_view_image.setEnabled(True)
        except Exception as exc:
            _LOG.exception("Failed to open image: %s", path)
            self._source_image = None
            self._set_status(f"Could not open image: {exc}", error=True)
            return
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
            # 1. Start from pre-bridge paths; apply all confirmed bridges via apply_manual_bridges
            active_paths = [p for i, p in enumerate(self._last_result.paths)
                            if i not in self._excluded_paths]

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
            self._maybe_show_donation_prompt()
        except Exception as exc:
            _LOG.exception("SVG export failed")
            self._set_status(f"Export failed: {exc}", error=True)

    @pyqtSlot()
    def _on_export_image_svg(self) -> None:
        """Export the background-removed image as a filled, coloured SVG graphic.

        Unlike the cut-path export, this produces a proper vector image with
        each shape filled by its sampled colour — suitable for use as a logo
        or graphic in other applications, not for laser cutting.
        """
        if self._nobg_image is None:
            self._set_status("No image to export — run the pipeline first", error=True)
            return

        default_name = "image.svg"
        if self._last_result and self._last_result.source_path:
            default_name = self._last_result.source_path.with_suffix(".svg").name

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export SVG Image",
            default_name,
            "SVG Files (*.svg);;All Files (*)",
        )
        if not path:
            return

        try:
            from bridgeit.pipeline.export import export_image_svg
            settings = self._controls.get_settings()
            self._set_status("Exporting SVG image…")
            written = export_image_svg(
                self._nobg_image,
                path,
                smoothing=settings.contour_smoothing,
                min_area=settings.min_contour_area,
            )
            self._set_status(f"SVG image exported: {written}", success=True)
        except Exception as exc:
            _LOG.exception("SVG image export failed")
            self._set_status(f"SVG image export failed: {exc}", error=True)

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
                ("Escape",                "Cancel pending point / discard staged / exit mode"),
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
            # If there are staged bridges, ask before discarding them
            n = canvas.staged_count
            if n > 0:
                dlg = QMessageBox(self)
                dlg.setWindowTitle("Staged Bridges")
                dlg.setText(
                    f"You have {n} staged bridge{'s' if n != 1 else ''} "
                    "that haven't been confirmed yet."
                )
                confirm_btn = dlg.addButton("Confirm & Exit", QMessageBox.ButtonRole.AcceptRole)
                discard_btn = dlg.addButton("Discard & Exit", QMessageBox.ButtonRole.DestructiveRole)
                dlg.addButton("Keep Placing", QMessageBox.ButtonRole.RejectRole)
                dlg.setDefaultButton(confirm_btn)
                _apply_dialog_theme(dlg)
                dlg.exec()
                clicked = dlg.clickedButton()
                if clicked == confirm_btn:
                    canvas.confirm_staged_bridges()
                    canvas.set_mode(CanvasMode.SELECT)
                    self._btn_add_bridge.setChecked(False)
                elif clicked == discard_btn:
                    canvas.set_mode(CanvasMode.SELECT)
                    self._btn_add_bridge.setChecked(False)
                # "Keep Placing" → do nothing, stay in bridge mode
                return
            # No staged bridges — exit quietly
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
        if not self._last_result or not self._last_result.paths:
            return

        # Leave bridge-editing mode (the layout of bridges has changed)
        self._editing_bridge_idx = -1
        self._controls.set_bridge_editing_mode(False)

        # Sync our local state from the canvas before reloading
        canvas = self._preview.canvas
        self._excluded_paths = canvas.get_excluded()
        self._manual_bridges = canvas.get_manual_bridges()

        # Reload the canvas — redraws all items from the pre-bridge paths,
        # with confirmed bridges applied at export time via apply_manual_bridges
        canvas.load(
            self._last_result.paths,
            excluded=self._excluded_paths,
            manual_bridges=self._manual_bridges,
        )

        # Update info card to reflect confirmed bridge count
        if self._last_result:
            islands = len(self._last_result.analysis.islands) if self._last_result.analysis else 0
            paths   = len([i for i in range(len(self._last_result.paths or []))
                           if i not in self._excluded_paths])
            self._controls.update_info(islands, len(self._manual_bridges),
                                       paths, self._last_result.elapsed_seconds)

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
            self._btn_add_bridge.setToolTip("Cancel point  (Escape)")
            self._set_status(
                "Click second point to complete bridge  ·  hold Shift for straight lines"
            )
        elif mode_str == "bridge":
            # Entered bridge mode — prompt for first click
            self._bridge_confirming = False
            self._btn_add_bridge.setChecked(True)
            self._btn_add_bridge.setToolTip("Exit Bridge Mode  (Escape)")
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

        # Apply lasso mask: fill everything outside the polygon with white so
        # the background removal stage cleanly strips it away.
        if self._lasso_points is not None and self._source_image is not None:
            from PIL import Image as _PILImg, ImageDraw as _IDraw
            src_rgb = self._source_image.convert("RGB")
            mask = _PILImg.new("L", src_rgb.size, 0)
            _IDraw.Draw(mask).polygon(self._lasso_points, fill=255)
            white_bg = _PILImg.new("RGB", src_rgb.size, (255, 255, 255))
            source = _PILImg.composite(src_rgb, white_bg, mask)
            preview_only = False   # background must be re-run on the masked image

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
            # Warn the user if rembg will need to download its AI model (~170 MB).
            # This only applies when erase_colors is empty (auto bg removal path).
            if not settings.erase_colors:
                from bridgeit.pipeline.remove_bg import rembg_model_downloaded
                if not rembg_model_downloaded():
                    self._set_status(
                        "First run: downloading AI background-removal model (~170 MB) "
                        "— this may take several minutes…"
                    )
                else:
                    self._set_status("Processing image…")
            else:
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
        # After worker finishes (or errors), stop the thread; cleanup via slot
        # that nulls instance references BEFORE calling deleteLater() so no
        # code ever calls isRunning() on a deleted C++ object.
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.error.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker_thread)

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
            self._show_pipeline_error(result.error)
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
                self._excluded_paths = set()
                self._manual_bridges = []
            # Load pre-bridge paths — no bridges auto-applied; user triggers via Auto Bridge
            self._preview.canvas.load(
                result.paths,
                excluded=self._excluded_paths,
                manual_bridges=self._manual_bridges,
            )
            # Enable toolbar buttons that require a loaded design
            self._btn_view_svg.setEnabled(True)
            self._btn_delete.setEnabled(True)
            self._btn_add_bridge.setEnabled(True)
            self._btn_auto_bridge.setEnabled(True)

        # Switch to image view — but only if we're not already on the canvas.
        # This prevents the view from jumping away when the user tweaks a setting
        # while working on the canvas.
        if self._nobg_image:
            self._btn_view_image.setEnabled(True)
            if not on_canvas:
                self._preview.show_image_from_pil(self._nobg_image)

        # Update the info card — bridges count shows confirmed bridges only (starts at 0)
        islands = len(result.analysis.islands) if result.analysis else 0
        paths   = len(result.paths) if result.paths else 0
        self._controls.update_info(islands, len(self._manual_bridges), paths, result.elapsed_seconds)

        self._btn_export.setEnabled(True)
        self._btn_export_image.setEnabled(True)
        self._btn_erase.setEnabled(True)
        self._btn_crop.setEnabled(True)
        self._controls.set_controls_enabled(True)
        self._set_status(
            f"Done — {islands} island(s) detected in {result.elapsed_seconds:.2f}s  "
            "· click Auto Bridge to suggest placements",
            success=True,
        )

        # If settings changed while this run was in progress, kick off another run now
        if self._pending_settings:
            self._settings_timer.start()

    @pyqtSlot(str)
    def _on_pipeline_error(self, message: str) -> None:
        self._set_busy(False)
        # If a previous successful result exists, re-enable the export and editing
        # buttons so the user can still work with the last good output without
        # restarting the app.
        if self._last_result:
            self._btn_export.setEnabled(True)
            self._btn_export_image.setEnabled(True)
            self._btn_erase.setEnabled(True)
            self._btn_crop.setEnabled(True)
            self._controls.set_controls_enabled(True)
        self._show_pipeline_error(message)

    def _show_pipeline_error(self, message: str) -> None:
        """Show a pipeline error in a themed QMessageBox with traceback in Details."""
        lines = message.strip().splitlines()
        summary = lines[0] if lines else "An unknown error occurred"
        self._set_status(f"Error: {summary}", error=True)
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Pipeline Error")
        dlg.setIcon(QMessageBox.Icon.Critical)
        dlg.setText(summary)
        if len(lines) > 1:
            dlg.setDetailedText(message)
        _apply_dialog_theme(dlg)
        dlg.exec()

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
    def _on_auto_bridge(self) -> None:
        """Load pipeline-suggested bridges as staged items for review.

        The pipeline already computed bridge suggestions (bridge_result.bridges).
        This method pushes them into the canvas as staged overlays so the user
        can delete unwanted ones before confirming the rest.
        """
        if not self._last_result or not self._last_result.bridge_result:
            return
        canvas = self._preview.canvas
        suggestions = self._last_result.bridge_result.bridges
        if not suggestions:
            self._set_status("No islands detected — no bridges to suggest")
            return
        # Warn if existing staged bridges would be replaced
        if canvas.staged_count > 0:
            n = canvas.staged_count
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Replace Staged Bridges?")
            dlg.setText(
                f"Auto Bridge will replace your {n} staged bridge{'s' if n != 1 else ''} "
                "with new suggestions."
            )
            replace_btn = dlg.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
            dlg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            _apply_dialog_theme(dlg)
            dlg.exec()
            if dlg.clickedButton() != replace_btn:
                return
        from bridgeit.pipeline.bridge import mm_to_px
        canvas.bridge_width_px = mm_to_px(self._controls.get_settings().bridge_width_mm)
        canvas.load_auto_bridge_suggestions(suggestions)
        canvas.set_mode(CanvasMode.BRIDGE)
        self._btn_add_bridge.setChecked(True)
        self._preview.show_canvas()
        canvas.setFocus()
        n = len(suggestions)
        self._set_status(
            f"{n} bridge suggestion{'s' if n != 1 else ''} — "
            "delete unwanted ones, then press Enter or click Confirm to accept"
        )

    @pyqtSlot()
    def _on_toggle_erase_mode(self) -> None:
        """Enter or exit background-erase mode.

        First click: enter erase mode — show original image for colour sampling.
        Second click: exit sampling UI and return to the processed result.
          The sampled colours are KEPT so the erase stays active; they are only
          cleared when the user loads a new image or right-clicks the button.
        """
        if self._btn_erase.isChecked():
            # Entering erase mode — show the original image for colour sampling
            if self._source_image is not None:
                self._preview.show_image_from_pil(self._source_image)
            self._preview.img_preview.set_erase_mode(True)
            n = len(self._erase_colors)
            tip = (
                "Erase mode — click background areas to sample colours.  "
                f"({n} colour{'s' if n != 1 else ''} sampled)  "
                "Click this button again to finish; right-click to clear all colours."
            )
            self._btn_erase.setToolTip(tip)
            if n:
                self._set_status(
                    f"Erase mode: {n} colour{'s' if n != 1 else ''} active — click more areas to add"
                )
            else:
                self._set_status("Erase mode: click on background areas to remove them")
        else:
            # Exiting sampling UI — keep the erase colours, just stop sampling
            self._preview.img_preview.set_erase_mode(False)
            n = len(self._erase_colors)
            if n:
                self._btn_erase.setToolTip(
                    f"Erase Background — {n} colour{'s' if n != 1 else ''} active  "
                    "(click to add more · right-click to clear)"
                )
                self._set_status(
                    f"Erase: {n} colour{'s' if n != 1 else ''} active — "
                    "right-click the button to clear"
                )
            else:
                self._btn_erase.setToolTip("Erase Background  — click to enter erase mode")
                self._set_status("Erase mode off")
            # Show the current nobg result
            if self._nobg_image is not None:
                self._preview.show_image_from_pil(self._nobg_image)

    @pyqtSlot()
    def _on_erase_context_menu(self, pos) -> None:
        """Show context menu on right-click of the Erase button."""
        t = current_theme()
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']}; }}"
            f"QMenu::item:selected {{ background: {t['accent']}; color: #fff; }}"
        )
        clear_act = menu.addAction("Clear erase colours")
        clear_act.setEnabled(bool(self._erase_colors))
        chosen = menu.exec(self._btn_erase.mapToGlobal(pos))
        if chosen == clear_act:
            self._on_erase_clear()

    def _on_erase_clear(self) -> None:
        """Clear all sampled erase colours and revert to automatic background removal."""
        if not self._erase_colors:
            return
        self._erase_colors = []
        self._btn_erase.setChecked(False)
        self._preview.img_preview.set_erase_mode(False)
        self._btn_erase.setToolTip("Erase Background  — click to enter erase mode")
        self._set_status("Erase colours cleared — reverting to auto background removal")
        if self._nobg_image is not None:
            self._preview.show_image_from_pil(self._nobg_image)
        src = self._source_image
        if src is not None:
            self._run_pipeline(source=src, preview_only=False)

    @pyqtSlot()
    def _on_toggle_lasso_mode(self) -> None:
        """Enter or exit polygon trace-selection mode.

        First click: show original image, user clicks points to trace a polygon.
        Second click (toggle off): clear the selection and re-run at full size.
        """
        if self._btn_crop.isChecked():
            # Entering crop mode — show original so the user can see full extent
            if self._source_image is not None:
                self._preview.show_image_from_pil(self._source_image)
            self._preview.img_preview.set_lasso_mode(True)
            # Keep any existing polygon visible as a starting reference
            if self._lasso_points:
                self._preview.img_preview.set_confirmed_lasso(self._lasso_points)
            self._set_status(
                "Trace Selection: click points around what to keep  ·  "
                "right-click or click first point to close  ·  Backspace = undo last point"
            )
        else:
            # Toggling off — clear the lasso and revert to full-image processing
            self._lasso_points = None
            self._preview.img_preview.set_lasso_mode(False)
            self._preview.img_preview.set_confirmed_lasso(None)
            self._btn_crop.setToolTip(
                "Trace Selection  — click points around what you want to keep"
            )
            self._set_status("Trace selection cleared — processing full image")
            if self._nobg_image is not None:
                self._preview.show_image_from_pil(self._nobg_image)
            src = self._source_image
            if src is not None:
                self._run_pipeline(source=src, preview_only=False)

    @pyqtSlot(object)
    def _on_lasso_selected(self, points: list) -> None:
        """Called when the user closes the lasso polygon."""
        self._lasso_points = points
        self._btn_crop.setToolTip(f"Trace Selection active: {len(points)} pts — click to clear")
        self._set_status(f"Trace Selection: {len(points)}-point polygon — re-running pipeline…")
        self._preview.img_preview.set_lasso_mode(False)
        if self._source_image is not None:
            self._run_pipeline(source=self._source_image, preview_only=False)

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

    @pyqtSlot()
    def _cleanup_worker_thread(self) -> None:
        """Called when the worker thread finishes. Nulls instance references
        before scheduling Qt deletion so isRunning() is never called on a
        deleted C++ object."""
        thread = self._worker_thread
        worker = self._worker
        self._worker_thread = None
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.deleteLater()

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

        Success/info messages auto-clear after 8 s; error messages persist until
        the next action so the user has time to read them.
        """
        t = current_theme()
        color = t["success"] if success else (t["error"] if error else t["text_muted"])
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px; padding: 0;")
        self._status_label.setText(message)
        # Auto-clear non-error messages; cancel any pending clear on errors so they stick.
        if error:
            self._status_clear_timer.stop()
        else:
            self._status_clear_timer.start()

    @pyqtSlot()
    def _on_status_timeout(self) -> None:
        """Fade out the status bar text after the auto-clear delay."""
        t = current_theme()
        self._status_label.setStyleSheet(f"color: {t['text_muted']}; font-size: 11px; padding: 0;")
        self._status_label.setText("")

    @pyqtSlot(str)
    def _on_update_available(self, version: str) -> None:
        self._update_version = version
        self._btn_update.setText(f"⬆  v{version} available")
        self._btn_update.setToolTip(f"Version {version} is available — click to open the releases page")
        self._style_update_btn()
        self._btn_update.show()

    def _style_update_btn(self) -> None:
        t = current_theme()
        self._btn_update.setStyleSheet(f"""
            QPushButton {{
                background: {t['accent']};
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 0 12px;
                font-size: 11px;
                font-weight: 600;
                height: 28px;
            }}
            QPushButton:hover {{
                background: {t['accent_hover']};
            }}
        """)

    @pyqtSlot()
    def _on_update_clicked(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl("https://github.com/outlandfabworks/BridgeIt/releases/latest"))

    def _maybe_show_donation_prompt(self) -> None:
        """Show a one-time donation prompt after every 3rd successful export.

        Uses QSettings to persist the export count and whether the user has
        permanently dismissed the prompt.
        """
        from PyQt6.QtCore import QSettings
        s = QSettings("OutlandFabworks", "BridgeIt")
        if s.value("donation/dismissed", False, type=bool):
            return
        count = s.value("donation/export_count", 0, type=int) + 1
        s.setValue("donation/export_count", count)
        if count % 3 != 0:
            return
        self._show_donation_prompt()

    def _show_donation_prompt(self) -> None:
        from PyQt6.QtCore import QSettings, QUrl
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout

        t = current_theme()
        dlg = QDialog(self)
        dlg.setWindowTitle("Enjoying BridgeIt?")
        dlg.setFixedWidth(400)
        dlg.setStyleSheet(
            f"QDialog {{ background: {t['surface']}; "
            f"color: {t['text']}; }} "
            f"QLabel {{ color: {t['text']}; }} "
            f"QPushButton {{ padding: 6px 14px; border-radius: 6px; "
            f"background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']}; }} "
            f"QPushButton#donate {{ background: {t['accent']}; color: #fff; border: none; }}"
        )

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        from PyQt6.QtWidgets import QLabel
        title = QLabel("If BridgeIt is saving you time, consider buying me a coffee.")
        title.setWordWrap(True)
        title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {t['text']};")
        layout.addWidget(title)

        sub = QLabel(
            "BridgeIt is free and always will be. A small donation helps keep it maintained and improved."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"font-size: 11px; color: {t['text_muted']};")
        layout.addWidget(sub)

        layout.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_donate = QPushButton("Support BridgeIt")
        btn_donate.setObjectName("donate")
        btn_donate.setCursor(Qt.CursorShape.PointingHandCursor)

        btn_later = QPushButton("Maybe later")
        btn_dismiss = QPushButton("Don't ask again")

        for b in (btn_donate, btn_later, btn_dismiss):
            btn_row.addWidget(b)

        layout.addLayout(btn_row)

        def _on_donate():
            QDesktopServices.openUrl(QUrl("https://ko-fi.com/outlandfabworks"))
            dlg.accept()

        def _on_dismiss():
            from PyQt6.QtCore import QSettings
            QSettings("OutlandFabworks", "BridgeIt").setValue("donation/dismissed", True)
            dlg.reject()

        btn_donate.clicked.connect(_on_donate)
        btn_later.clicked.connect(dlg.reject)
        btn_dismiss.clicked.connect(_on_dismiss)

        dlg.exec()

    def _show_about(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel

        t = current_theme()
        dlg = QDialog(self)
        dlg.setWindowTitle("About BridgeIt")
        dlg.setFixedWidth(380)
        dlg.setStyleSheet(
            f"QDialog {{ background: {t['sidebar_bg']}; color: {t['text']}; }} "
            f"QLabel {{ color: {t['text']}; }} "
            f"QPushButton {{ padding: 6px 14px; border-radius: 6px; "
            f"background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']}; }} "
            f"QPushButton#donate {{ background: {t['accent']}; color: #fff; border: none; }}"
        )

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        name_lbl = QLabel(f"BridgeIt  <span style='color:{t['text_muted']}; font-weight:normal;'>v{APP_VERSION}</span>")
        name_lbl.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(name_lbl)

        desc = QLabel("Convert images to fabrication-ready SVGs with automatic bridge generation for laser cutting.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 11px; color: {t['text_muted']};")
        layout.addWidget(desc)

        layout.addSpacing(4)

        links_lbl = QLabel(
            f"<a href='https://outlandfabworks.github.io/BridgeIt' style='color:{t['accent']};'>Website</a>"
            f"  ·  "
            f"<a href='https://github.com/outlandfabworks/BridgeIt' style='color:{t['accent']};'>GitHub</a>"
            f"  ·  "
            f"<a href='https://ko-fi.com/outlandfabworks' style='color:{t['accent']};'>Support / Donate</a>"
        )
        links_lbl.setOpenExternalLinks(True)
        links_lbl.setStyleSheet("font-size: 11px;")
        layout.addWidget(links_lbl)

        layout.addSpacing(8)

        btn_donate = QPushButton("Support BridgeIt on Ko-fi")
        btn_donate.setObjectName("donate")
        btn_donate.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_donate.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://ko-fi.com/outlandfabworks"))
        )
        layout.addWidget(btn_donate)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.accept)
        layout.addWidget(btn_close)

        dlg.exec()

    @property
    def _bridges(self):
        """Convenience accessor for the auto-generated bridge list from the last result."""
        if self._last_result and self._last_result.bridge_result:
            return self._last_result.bridge_result.bridges
        return []
