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
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
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


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _PipelineWorker(QObject):
    """Runs the pipeline in a QThread."""

    progress = pyqtSignal(str)       # stage message
    finished = pyqtSignal(object)    # PipelineResult
    error = pyqtSignal(str)

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
        self._preview_only = preview_only

    @pyqtSlot()
    def run(self) -> None:
        try:
            if self._preview_only and self._nobg_image is not None:
                result = self._runner.run_to_preview(self._nobg_image)
            else:
                result = self._runner.run(self._source)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self._nobg_image: Optional[Image.Image] = None
        self._last_result: Optional[PipelineResult] = None
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[_PipelineWorker] = None
        self._pending_settings: Optional[PipelineSettings] = None

        self._build_ui()
        self._apply_theme()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.resize(1280, 780)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Toolbar
        toolbar = self._build_toolbar()
        self.addToolBar(toolbar)

        # Content area: controls | preview
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: #2d2d42; }}")

        self._controls = ControlsPanel()
        self._controls.setStyleSheet(f"background: #16162a;")
        self._controls.settings_changed.connect(self._on_settings_changed)

        self._preview = PreviewPanel()
        self._preview.file_dropped.connect(self._on_file_opened)

        splitter.addWidget(self._controls)
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 1000])

        main_layout.addWidget(splitter)

        # Status bar
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(f"background: #0f0f1e; color: {MUTED_COLOR}; font-size: 11px;")
        self.setStatusBar(self._status_bar)

        self._status_label = QLabel("Ready — open or drop an image to begin")
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate
        self._progress_bar.setFixedWidth(120)
        self._progress_bar.hide()
        self._progress_bar.setStyleSheet(
            f"QProgressBar {{ background: #2a2a3e; border-radius: 3px; border: none; }}"
            f"QProgressBar::chunk {{ background: {ACCENT_COLOR}; border-radius: 3px; }}"
        )

        self._status_bar.addWidget(self._status_label)
        self._status_bar.addPermanentWidget(self._progress_bar)

    def _build_toolbar(self) -> QToolBar:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setStyleSheet(
            f"""
            QToolBar {{
                background: #0f0f1e;
                border-bottom: 1px solid #2a2a3e;
                padding: 4px 12px;
                spacing: 8px;
            }}
            """
        )

        # App name
        name_lbl = QLabel(f"  {APP_NAME}")
        name_lbl.setStyleSheet(
            f"color: {TEXT_COLOR}; font-size: 16px; font-weight: 700; letter-spacing: 1px;"
        )
        toolbar.addWidget(name_lbl)

        # Separator
        sep = QWidget()
        sep.setFixedWidth(16)
        toolbar.addWidget(sep)

        # Open button
        self._btn_open = self._toolbar_button("Open Image", primary=False)
        self._btn_open.clicked.connect(self._on_open_clicked)
        toolbar.addWidget(self._btn_open)

        # Export button
        self._btn_export = self._toolbar_button("Export SVG", primary=True)
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._on_export_clicked)
        toolbar.addWidget(self._btn_export)

        # Stretch
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # View toggle
        self._btn_view_image = self._toolbar_button("Show Original", primary=False)
        self._btn_view_image.setEnabled(False)
        self._btn_view_image.clicked.connect(self._show_original)
        toolbar.addWidget(self._btn_view_image)

        self._btn_view_svg = self._toolbar_button("Show SVG", primary=False)
        self._btn_view_svg.setEnabled(False)
        self._btn_view_svg.clicked.connect(self._show_svg)
        toolbar.addWidget(self._btn_view_svg)

        return toolbar

    @staticmethod
    def _toolbar_button(text: str, primary: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: {ACCENT_COLOR};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 16px;
                    font-weight: 600;
                    font-size: 12px;
                }}
                QPushButton:hover {{ background: #6d28d9; }}
                QPushButton:disabled {{ background: #3a2a4e; color: #6a5a7e; }}
                """
            )
        else:
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: {SURFACE_COLOR};
                    color: {TEXT_COLOR};
                    border: 1px solid #3a3a54;
                    border-radius: 6px;
                    padding: 6px 14px;
                    font-size: 12px;
                }}
                QPushButton:hover {{ background: #34344e; }}
                QPushButton:disabled {{ color: {MUTED_COLOR}; }}
                """
            )
        return btn

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: {PREVIEW_BG_COLOR}; }}
            QSplitter {{ background: {PREVIEW_BG_COLOR}; }}
            """
        )

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _on_open_clicked(self) -> None:
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
        self._run_pipeline(source=path, preview_only=False)

    @pyqtSlot(object)
    def _on_settings_changed(self, settings: PipelineSettings) -> None:
        if self._nobg_image is None:
            return
        if self._worker_thread and self._worker_thread.isRunning():
            # Queue the settings for after current run finishes
            self._pending_settings = settings
            return
        self._run_pipeline(source=None, preview_only=True, settings=settings)

    @pyqtSlot()
    def _on_export_clicked(self) -> None:
        if not self._last_result or not self._last_result.bridge_result:
            return

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
            return

        try:
            from bridgeit.pipeline.export import export_svg
            written = export_svg(self._last_result.bridge_result, path)
            self._set_status(f"Exported: {written}", success=True)
        except Exception as exc:
            self._set_status(f"Export failed: {exc}", error=True)

    @pyqtSlot()
    def _show_original(self) -> None:
        if self._nobg_image:
            self._preview.show_image_from_pil(self._nobg_image)

    @pyqtSlot()
    def _show_svg(self) -> None:
        if self._last_result and self._last_result.svg_string:
            self._preview.show_svg(self._last_result.svg_string)

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def _run_pipeline(
        self,
        source,
        preview_only: bool,
        settings: Optional[PipelineSettings] = None,
    ) -> None:
        if self._worker_thread and self._worker_thread.isRunning():
            return

        if settings is None:
            settings = self._controls.get_settings()

        runner = PipelineRunner(
            settings=settings,
            on_progress=lambda stage, msg: self._set_status(msg),
        )

        self._worker = _PipelineWorker(
            runner=runner,
            source=source,
            nobg_image=self._nobg_image if preview_only else None,
            preview_only=preview_only,
        )
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_pipeline_finished)
        self._worker.error.connect(self._on_pipeline_error)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.error.connect(self._worker_thread.quit)

        self._set_busy(True)
        self._worker_thread.start()

    @pyqtSlot(object)
    def _on_pipeline_finished(self, result: PipelineResult) -> None:
        self._set_busy(False)
        self._last_result = result

        if result.error:
            self._set_status(f"Error: {result.error}", error=True)
            return

        # Cache background-removed image for re-runs
        if result.nobg_image is not None:
            self._nobg_image = result.nobg_image

        # Update preview
        if result.svg_string:
            self._preview.show_svg(result.svg_string)
            self._btn_view_svg.setEnabled(True)
        if self._nobg_image:
            self._btn_view_image.setEnabled(True)

        # Update info panel
        islands = len(result.analysis.islands) if result.analysis else 0
        bridges = len(result.bridges) if result.bridge_result else 0
        paths = len(result.bridge_result.paths) if result.bridge_result else 0
        self._controls.update_info(islands, bridges, paths, result.elapsed_seconds)

        self._btn_export.setEnabled(True)
        self._set_status(
            f"Done — {islands} island(s), {bridges} bridge(s) in {result.elapsed_seconds:.2f}s",
            success=True,
        )

        # Process pending settings update
        if self._pending_settings:
            s = self._pending_settings
            self._pending_settings = None
            self._run_pipeline(source=None, preview_only=True, settings=s)

    @pyqtSlot(object)
    def _on_pipeline_error(self, message: str) -> None:
        self._set_busy(False)
        self._set_status(f"Error: {message}", error=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._progress_bar.setVisible(busy)
        self._btn_open.setEnabled(not busy)

    def _set_status(self, message: str, success: bool = False, error: bool = False) -> None:
        color = SUCCESS_COLOR if success else (ERROR_COLOR if error else MUTED_COLOR)
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._status_label.setText(message)

    @property
    def _bridges(self):
        if self._last_result and self._last_result.bridge_result:
            return self._last_result.bridge_result.bridges
        return []
