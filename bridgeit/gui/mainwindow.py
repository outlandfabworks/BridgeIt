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
from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QFont, QPalette
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
    ACCENT_COLOR,
    APP_NAME,
    APP_VERSION,
    ERROR_COLOR,
    MUTED_COLOR,
    PREVIEW_BG_COLOR,
    SUCCESS_COLOR,
    SURFACE_COLOR,
    TEXT_COLOR,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from bridgeit.gui.controls import ControlsPanel
from bridgeit.gui.preview import PreviewPanel
from bridgeit.pipeline.pipeline import PipelineResult, PipelineRunner, PipelineSettings, Stage
from bridgeit.gui.canvas import InteractiveCanvas, Mode as CanvasMode
from bridgeit.pipeline.export import make_preview_svg


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
                # Full run: all five pipeline stages including background removal
                result = self._runner.run(self._source)
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

        self._build_ui()
        self._apply_theme()
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

        # Build and attach the toolbar (it docks to the top automatically)
        toolbar = self._build_toolbar()
        self.addToolBar(toolbar)

        # QSplitter lets the user drag the divider to resize the panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)   # very thin divider line
        splitter.setStyleSheet(f"QSplitter::handle {{ background: #2d2d42; }}")

        # Left panel: settings controls
        self._controls = ControlsPanel()
        self._controls.setStyleSheet(f"background: #16162a;")
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
        self._status_bar.setStyleSheet(
            "QStatusBar {"
            "  background: #0a0a18;"
            "  border-top: 1px solid #1e1e30;"
            f" color: {MUTED_COLOR};"
            "  font-size: 11px;"
            "  padding: 2px 8px;"
            "}"
        )
        self.setStatusBar(self._status_bar)

        # Text label — updated by _set_status() to show pipeline progress and errors
        self._status_label = QLabel("Ready — open or drop an image to begin")
        self._status_label.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px;")

        # Thin progress bar shown on the right side of the status bar while the pipeline runs.
        # Range (0, 0) = indeterminate "busy" animation (no start/end values needed).
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)   # indeterminate — just pulses to show "working"
        self._progress_bar.setFixedWidth(100)
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.hide()   # hidden until a pipeline run starts
        self._progress_bar.setStyleSheet(
            "QProgressBar { background: #1e1e30; border-radius: 2px; border: none; }"
            f"QProgressBar::chunk {{ background: {ACCENT_COLOR}; border-radius: 2px; }}"
        )

        # addWidget = left-aligned; addPermanentWidget = right-aligned (won't be pushed out)
        self._status_bar.addWidget(self._status_label)
        self._status_bar.addPermanentWidget(self._progress_bar)

    def _build_toolbar(self) -> QToolBar:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setStyleSheet(
            """
            QToolBar {
                background: #0a0a18;
                border-bottom: 1px solid #1e1e30;
                padding: 5px 16px;
                spacing: 4px;
            }
            """
        )

        # ── Branding ──────────────────────────────────────────────────────
        logo = QLabel("◆")
        logo.setStyleSheet(f"color: {ACCENT_COLOR}; font-size: 13px; padding: 0 3px 0 4px;")
        toolbar.addWidget(logo)

        name_lbl = QLabel(APP_NAME)
        name_lbl.setStyleSheet(
            f"color: {TEXT_COLOR}; font-size: 14px; font-weight: 700; letter-spacing: 0.5px;"
        )
        toolbar.addWidget(name_lbl)

        ver_lbl = QLabel(f"v{APP_VERSION}")
        ver_lbl.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 10px; padding: 3px 12px 0 5px;")
        toolbar.addWidget(ver_lbl)

        toolbar.addWidget(self._toolbar_sep())

        # ── File actions ──────────────────────────────────────────────────
        self._btn_open = self._toolbar_button("Open")
        self._btn_open.setToolTip("Open an image file")
        self._btn_open.clicked.connect(self._on_open_clicked)
        toolbar.addWidget(self._btn_open)

        self._btn_export = self._toolbar_button("Export SVG", primary=True)
        self._btn_export.setEnabled(False)
        self._btn_export.setToolTip("Export fabrication-ready SVG")
        self._btn_export.clicked.connect(self._on_export_clicked)
        toolbar.addWidget(self._btn_export)

        toolbar.addWidget(self._toolbar_sep())

        # ── View toggles ──────────────────────────────────────────────────
        self._btn_view_image = self._toolbar_button("Original")
        self._btn_view_image.setEnabled(False)
        self._btn_view_image.setToolTip("Show background-removed original")
        self._btn_view_image.clicked.connect(self._show_original)
        toolbar.addWidget(self._btn_view_image)

        self._btn_view_svg = self._toolbar_button("Paths")
        self._btn_view_svg.setEnabled(False)
        self._btn_view_svg.setToolTip("Show cut paths / interactive canvas")
        self._btn_view_svg.clicked.connect(self._show_svg)
        toolbar.addWidget(self._btn_view_svg)

        toolbar.addWidget(self._toolbar_sep())

        # ── Edit tools ────────────────────────────────────────────────────
        self._btn_delete = self._toolbar_button("Delete ✕")
        self._btn_delete.setEnabled(False)
        self._btn_delete.setToolTip("Remove selected paths or bridges  (Delete key)")
        self._btn_delete.clicked.connect(self._on_delete_selected)
        toolbar.addWidget(self._btn_delete)

        self._btn_add_bridge = self._toolbar_button("＋ Bridge")
        self._btn_add_bridge.setEnabled(False)
        self._btn_add_bridge.setCheckable(True)
        self._btn_add_bridge.setToolTip("Place manual bridges between paths")
        self._btn_add_bridge.clicked.connect(self._on_toggle_bridge_mode)
        toolbar.addWidget(self._btn_add_bridge)

        # ── Spacer + help ─────────────────────────────────────────────────
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        btn_help = self._toolbar_button("?")
        btn_help.setFixedWidth(32)
        btn_help.setToolTip("Keyboard shortcuts  (?)")
        btn_help.clicked.connect(self._show_shortcuts)
        toolbar.addWidget(btn_help)

        return toolbar

    @staticmethod
    def _toolbar_sep() -> QWidget:
        """Thin vertical divider between toolbar groups."""
        outer = QWidget()
        outer.setFixedWidth(17)
        inner = QWidget(outer)
        inner.setFixedSize(1, 22)
        inner.setStyleSheet("background: #252538;")
        inner.move(8, 5)
        return outer

    @staticmethod
    def _toolbar_button(text: str, primary: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            btn.setStyleSheet(
                """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #8b5cf6, stop:1 #7c3aed);
                    color: #fff;
                    border: 1px solid #6d28d9;
                    border-radius: 5px;
                    padding: 5px 18px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #9d6ef8, stop:1 #8b5cf6);
                }
                QPushButton:disabled {
                    background: #1e1228;
                    border-color: #2a1a3a;
                    color: #3d2a52;
                }
                """
            )
        else:
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: transparent;
                    color: {TEXT_COLOR};
                    border: 1px solid transparent;
                    border-radius: 5px;
                    padding: 5px 12px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background: #1e1e30;
                    border-color: #2d2d45;
                }}
                QPushButton:pressed {{
                    background: #161626;
                }}
                QPushButton:checked {{
                    background: rgba(124, 58, 237, 0.18);
                    border-color: #7c3aed;
                    color: #a78bfa;
                    font-weight: 600;
                }}
                QPushButton:checked:hover {{
                    background: rgba(124, 58, 237, 0.28);
                }}
                QPushButton:disabled {{
                    color: #2d2d45;
                }}
                """
            )
        return btn

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: {PREVIEW_BG_COLOR}; }}
            QSplitter {{ background: {PREVIEW_BG_COLOR}; }}
            QSplitter::handle {{ background: #1a1a2e; }}
            QToolTip {{
                background: #1e1e30;
                color: {TEXT_COLOR};
                border: 1px solid #3a3a54;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }}
            """
        )

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

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
        # Show the original file right away — don't wait for background removal
        try:
            from PIL import Image as _Image
            orig = _Image.open(path)
            self._preview.show_image_from_pil(orig)
            self._btn_view_image.setEnabled(True)
        except Exception:
            pass   # if loading fails, we'll get a proper error from the pipeline
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

            # 2. Append each manual bridge as a solid rectangle path
            for bridge_data in self._manual_bridges:
                pt1, pt2 = bridge_data[0], bridge_data[1]
                # Use the stored per-bridge width; fall back to current settings if missing
                w_px = bridge_data[2] if len(bridge_data) > 2 else bridge_px
                rect = _bridge_rect(pt1, pt2, w_px)
                if rect:
                    active_paths.append(rect)

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
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(
            f"background: #16162a; color: {TEXT_COLOR};"
            f"font-size: 12px;"
        )

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(0)

        sections = [
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
                f"color: {MUTED_COLOR}; font-size: 10px; font-weight: 600;"
                f"letter-spacing: 1px; padding-top: 16px; padding-bottom: 4px;"
            )
            layout.addWidget(hdr)

            # Divider
            div = QFrame()
            div.setFrameShape(QFrame.Shape.HLine)
            div.setStyleSheet(f"color: #2a2a3e;")
            layout.addWidget(div)

            for key, desc in rows:
                row = QHBoxLayout()
                row.setContentsMargins(0, 5, 0, 0)
                key_lbl = QLabel(key)
                key_lbl.setStyleSheet(
                    f"color: {TEXT_COLOR}; font-family: monospace; font-size: 11px;"
                    f"background: #2a2a3e; border-radius: 3px; padding: 1px 6px;"
                )
                key_lbl.setFixedWidth(190)
                desc_lbl = QLabel(desc)
                desc_lbl.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px;")
                row.addWidget(key_lbl)
                row.addWidget(desc_lbl)
                row.addStretch()
                layout.addLayout(row)

        layout.addSpacing(16)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.setStyleSheet(
            f"QPushButton {{ background: {SURFACE_COLOR}; color: {TEXT_COLOR};"
            f"border: 1px solid #3a3a54; border-radius: 6px; padding: 5px 16px; }}"
            f"QPushButton:hover {{ background: #34344e; }}"
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
            # Bridges staged — change button to "Confirm" and give count
            self._bridge_confirming = True
            self._btn_add_bridge.setChecked(True)
            label = f"Confirm Bridges ({n})" if n > 1 else "Confirm Bridge"
            self._btn_add_bridge.setText(label)
            self._set_status(
                f"{n} bridge{'s' if n != 1 else ''} staged — "
                "place more, or press Enter / Confirm to apply  ·  Escape to discard"
            )
        elif mode_str == "bridge_pt2":
            # First point placed — prompt for second click
            self._bridge_confirming = False
            self._btn_add_bridge.setChecked(True)
            self._btn_add_bridge.setText("Cancel Bridge")
            self._set_status(
                "Click second point to complete bridge  ·  hold Shift for straight lines"
            )
        elif mode_str == "bridge":
            # Entered bridge mode — prompt for first click
            self._bridge_confirming = False
            self._btn_add_bridge.setChecked(True)
            self._btn_add_bridge.setText("Cancel Bridge")
            self._set_status(
                "Click a point on a path to start a bridge  ·  hold Shift for straight lines"
            )
        else:  # "select"
            # Back to normal select mode
            self._bridge_confirming = False
            self._btn_add_bridge.setChecked(False)
            self._btn_add_bridge.setText("Add Bridge")
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

        # Create a fresh PipelineRunner with the current settings.
        # The on_progress lambda updates the status bar at each stage.
        runner = PipelineRunner(
            settings=settings,
            on_progress=lambda stage, msg: self._set_status(msg),
        )

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
        color = SUCCESS_COLOR if success else (ERROR_COLOR if error else MUTED_COLOR)
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px; padding: 0;")
        self._status_label.setText(message)

    @property
    def _bridges(self):
        """Convenience accessor for the auto-generated bridge list from the last result."""
        if self._last_result and self._last_result.bridge_result:
            return self._last_result.bridge_result.bridges
        return []
