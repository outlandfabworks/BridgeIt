"""
controls.py — Settings panel widget.

Provides sliders and inputs for:
  - Bridge width (mm)
  - Contour smoothing
  - Min contour area (noise filter)

Emits a settings_changed signal whenever any control changes.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bridgeit.config import (
    ACCENT_COLOR,
    DEFAULT_BRIDGE_WIDTH_MM,
    DEFAULT_CONTOUR_SMOOTHING,
    DEFAULT_MIN_CONTOUR_AREA,
    MUTED_COLOR,
    SURFACE_COLOR,
    TEXT_COLOR,
)
from bridgeit.pipeline.pipeline import PipelineSettings


class ControlsPanel(QWidget):
    """Left-side settings panel."""

    settings_changed = pyqtSignal(PipelineSettings)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setObjectName("ControlsPanel")
        self.setMinimumWidth(260)
        self.setMaximumWidth(320)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Panel header ──────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(44)
        header.setStyleSheet("background: #0a0a18; border-bottom: 1px solid #1a1a2e;")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 0, 16, 0)
        hdr_lbl = QLabel("Settings")
        hdr_lbl.setStyleSheet(
            f"color: {TEXT_COLOR}; font-size: 12px; font-weight: 600; letter-spacing: 0.5px;"
        )
        hlay.addWidget(hdr_lbl)
        hlay.addStretch()
        root.addWidget(header)

        # ── Scrollable content ────────────────────────────────────────────
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        inner = QVBoxLayout(content)
        inner.setContentsMargins(16, 18, 16, 18)
        inner.setSpacing(6)

        inner.addWidget(self._section_label("CONVERSION SETTINGS"))
        inner.addSpacing(8)

        # Bridge width
        self._bridge_spin, bridge_row, self._bridge_lbl = self._labeled_double_spin(
            label="Bridge Width",
            unit="mm",
            value=DEFAULT_BRIDGE_WIDTH_MM,
            minimum=0.1,
            maximum=5.0,
            step=0.1,
            decimals=2,
            tooltip="Width of bridges connecting floating islands to the main shape.\nSmaller = less visible but weaker.",
        )
        inner.addLayout(bridge_row)
        self._bridge_slider = self._make_slider(1, 50, int(DEFAULT_BRIDGE_WIDTH_MM * 10))
        inner.addWidget(self._bridge_slider)
        inner.addSpacing(14)

        # Contour smoothing
        self._smooth_spin, smooth_row, _ = self._labeled_double_spin(
            label="Contour Smoothing",
            unit="",
            value=DEFAULT_CONTOUR_SMOOTHING,
            minimum=0.0,
            maximum=10.0,
            step=0.5,
            decimals=1,
            tooltip="Douglas-Peucker simplification factor.\n0 = no simplification (most detail).\nHigher = smoother, fewer points.",
        )
        inner.addLayout(smooth_row)
        self._smooth_slider = self._make_slider(0, 100, int(DEFAULT_CONTOUR_SMOOTHING * 10))
        inner.addWidget(self._smooth_slider)
        inner.addSpacing(14)

        # Min contour area
        self._area_spin, area_row = self._labeled_int_spin(
            label="Min Area Filter",
            unit="px²",
            value=int(DEFAULT_MIN_CONTOUR_AREA),
            minimum=0,
            maximum=5000,
            step=50,
            tooltip="Contours smaller than this area are ignored as noise.\nIncrease if you see speckles in the output.",
        )
        inner.addLayout(area_row)
        self._area_slider = self._make_slider(0, 5000, int(DEFAULT_MIN_CONTOUR_AREA))
        inner.addWidget(self._area_slider)

        inner.addSpacing(24)
        inner.addWidget(self._section_label("ANALYSIS INFO"))
        inner.addSpacing(10)

        # Info card
        info_card = QWidget()
        info_card.setStyleSheet(
            "background: #0e0e1e;"
            "border: 1px solid #1e1e30;"
            "border-radius: 6px;"
        )
        card_layout = QVBoxLayout(info_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(8)

        self._info_islands = self._info_row("Islands detected", "—")
        self._info_bridges = self._info_row("Bridges added", "—")
        self._info_paths = self._info_row("Total paths", "—")
        self._info_time = self._info_row("Processing time", "—")

        card_layout.addLayout(self._info_islands[0])
        card_layout.addLayout(self._info_bridges[0])
        card_layout.addLayout(self._info_paths[0])
        card_layout.addLayout(self._info_time[0])
        inner.addWidget(info_card)

        inner.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        root.addWidget(content)

        root.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_settings(self) -> PipelineSettings:
        return PipelineSettings(
            bridge_width_mm=self._bridge_spin.value(),
            contour_smoothing=self._smooth_spin.value(),
            min_contour_area=float(self._area_spin.value()),
        )

    def update_info(
        self,
        islands: int,
        bridges: int,
        paths: int,
        elapsed: float,
    ) -> None:
        self._info_islands[1].setText(str(islands))
        self._info_bridges[1].setText(str(bridges))
        self._info_paths[1].setText(str(paths))
        self._info_time[1].setText(f"{elapsed:.2f}s")

    def set_bridge_width_mm(self, value: float) -> None:
        """Update the bridge width spinbox silently (no settings_changed emission)."""
        for w in (self._bridge_spin, self._bridge_slider):
            w.blockSignals(True)
        self._bridge_spin.setValue(value)
        self._bridge_slider.setValue(int(value * 10))
        for w in (self._bridge_spin, self._bridge_slider):
            w.blockSignals(False)

    def set_bridge_editing_mode(self, editing: bool, count: int = 1) -> None:
        """Highlight the Bridge Width label when editing selected bridge(s)."""
        if editing:
            label = f"{count} Bridges" if count > 1 else "Selected Bridge"
            self._bridge_lbl.setText(label)
            self._bridge_lbl.setStyleSheet(f"color: {ACCENT_COLOR}; font-size: 12px; font-weight: 600;")
        else:
            self._bridge_lbl.setText("Bridge Width")
            self._bridge_lbl.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")

    def reset_info(self) -> None:
        for _, lbl in [self._info_islands, self._info_bridges, self._info_paths, self._info_time]:
            lbl.setText("—")

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        # Spin ↔ slider sync
        self._bridge_spin.valueChanged.connect(
            lambda v: self._bridge_slider.setValue(int(v * 10))
        )
        self._bridge_slider.valueChanged.connect(
            lambda v: self._bridge_spin.setValue(v / 10.0)
        )

        self._smooth_spin.valueChanged.connect(
            lambda v: self._smooth_slider.setValue(int(v * 10))
        )
        self._smooth_slider.valueChanged.connect(
            lambda v: self._smooth_spin.setValue(v / 10.0)
        )

        self._area_spin.valueChanged.connect(self._area_slider.setValue)
        self._area_slider.valueChanged.connect(self._area_spin.setValue)

        # Emit settings_changed on any change
        self._bridge_spin.valueChanged.connect(self._emit_settings)
        self._smooth_spin.valueChanged.connect(self._emit_settings)
        self._area_spin.valueChanged.connect(self._emit_settings)

    def _emit_settings(self) -> None:
        self.settings_changed.emit(self.get_settings())

    # ------------------------------------------------------------------
    # Widget helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _section_label(text: str) -> QWidget:
        container = QWidget()
        container.setFixedHeight(18)
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        bar = QWidget()
        bar.setFixedSize(3, 12)
        bar.setStyleSheet(f"background: {ACCENT_COLOR}; border-radius: 1px;")
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {MUTED_COLOR}; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;"
        )
        lay.addWidget(bar)
        lay.addWidget(lbl)
        lay.addStretch()
        return container

    @staticmethod
    def _labeled_double_spin(
        label: str, unit: str, value: float,
        minimum: float, maximum: float, step: float, decimals: int,
        tooltip: str = "",
    ):
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")
        lbl.setToolTip(tooltip)

        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setFixedWidth(72)
        spin.setToolTip(tooltip)
        spin.setStyleSheet(
            f"QDoubleSpinBox {{ background: {SURFACE_COLOR}; color: {TEXT_COLOR}; "
            f"border: 1px solid #3a3a54; border-radius: 4px; padding: 2px 4px; }}"
        )

        unit_lbl = QLabel(unit)
        unit_lbl.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px;")

        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(spin)
        if unit:
            row.addWidget(unit_lbl)
        return spin, row, lbl

    @staticmethod
    def _labeled_int_spin(
        label: str, unit: str, value: int,
        minimum: int, maximum: int, step: int,
        tooltip: str = "",
    ):
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")
        lbl.setToolTip(tooltip)

        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setValue(value)
        spin.setFixedWidth(72)
        spin.setToolTip(tooltip)
        spin.setStyleSheet(
            f"QSpinBox {{ background: {SURFACE_COLOR}; color: {TEXT_COLOR}; "
            f"border: 1px solid #3a3a54; border-radius: 4px; padding: 2px 4px; }}"
        )

        unit_lbl = QLabel(unit)
        unit_lbl.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px;")

        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(spin)
        if unit:
            row.addWidget(unit_lbl)
        return spin, row

    @staticmethod
    def _make_slider(minimum: int, maximum: int, value: int) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.setStyleSheet(
            f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: #3a3a54;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT_COLOR};
                border: none;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ACCENT_COLOR};
                border-radius: 2px;
            }}
            """
        )
        return slider

    @staticmethod
    def _info_row(label: str, value: str):
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px;")
        val = QLabel(value)
        val.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 11px; font-weight: 600;")
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(val)
        return row, val
