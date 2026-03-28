"""
controls.py — Settings panel widget.

Provides sliders and inputs for:
  - Bridge width (mm)
  - Contour smoothing
  - Min contour area (noise filter)

Emits a settings_changed signal whenever any control changes.
"""

from __future__ import annotations

# Qt core: orientation flags and the signal mechanism
from PyQt6.QtCore import Qt, pyqtSignal

# QFont lets us create font objects for styling labels
from PyQt6.QtGui import QFont

# Qt widget types used to build the panel:
# QDoubleSpinBox — a numeric input that allows decimal values (e.g. 0.50)
# QFrame — a container with an optional visible border/line
# QHBoxLayout — arranges children horizontally (left to right)
# QLabel — displays a text string (non-editable)
# QSlider — a draggable track for picking a numeric value visually
# QSizePolicy — controls how a widget grows/shrinks to fill space
# QSpacerItem — an invisible spacer that pushes other widgets apart
# QSpinBox — a numeric input for integer values
# QVBoxLayout — arranges children vertically (top to bottom)
# QWidget — the base class for all visible elements
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


# ControlsPanel is the left sidebar that holds all the user-adjustable settings.
# It inherits from QWidget so it can be placed inside other layouts.
class ControlsPanel(QWidget):
    """Left-side settings panel."""

    # settings_changed is emitted whenever any control value changes.
    # It carries the updated PipelineSettings object as its payload,
    # so the main window can immediately kick off a pipeline re-run.
    settings_changed = pyqtSignal(PipelineSettings)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Separate building the UI from wiring up the signals so each step
        # is easier to read and test independently
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # setObjectName lets us target this specific widget in stylesheets
        self.setObjectName("ControlsPanel")
        self.setMinimumWidth(260)
        self.setMaximumWidth(320)

        # Root vertical layout — everything stacks top to bottom
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Panel header ──────────────────────────────────────────────────
        # A thin strip at the top labelled "Settings"
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
        hlay.addStretch()   # push the label to the left
        root.addWidget(header)

        # ── Scrollable content ────────────────────────────────────────────
        # The main body of the panel holding all the settings controls
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        inner = QVBoxLayout(content)
        inner.setContentsMargins(16, 18, 16, 18)
        inner.setSpacing(6)

        # Section heading — "CONVERSION SETTINGS"
        inner.addWidget(self._section_label("CONVERSION SETTINGS"))
        inner.addSpacing(8)

        # ── Bridge width control ──────────────────────────────────────────
        # A spinbox (numeric input) paired with a slider — they stay in sync.
        # self._bridge_lbl is returned so we can change its text later when
        # the user is editing a selected bridge (see set_bridge_editing_mode).
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

        # Slider value is stored as integer 1–50 representing 0.1–5.0 mm (×10)
        self._bridge_slider = self._make_slider(1, 50, int(DEFAULT_BRIDGE_WIDTH_MM * 10))
        inner.addWidget(self._bridge_slider)
        inner.addSpacing(14)

        # ── Contour smoothing control ─────────────────────────────────────
        # Higher smoothing = fewer points on the traced path = smoother but less detailed
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

        # Slider is 0–100 representing 0.0–10.0 (×10 to keep integer precision)
        self._smooth_slider = self._make_slider(0, 100, int(DEFAULT_CONTOUR_SMOOTHING * 10))
        inner.addWidget(self._smooth_slider)
        inner.addSpacing(14)

        # ── Min contour area control ──────────────────────────────────────
        # Any detected shape smaller than this area (in px²) is discarded as noise
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

        # Slider range matches the spinbox range directly (no scaling needed)
        self._area_slider = self._make_slider(0, 5000, int(DEFAULT_MIN_CONTOUR_AREA))
        inner.addWidget(self._area_slider)

        inner.addSpacing(24)

        # ── Analysis info section ─────────────────────────────────────────
        # Read-only statistics updated after each pipeline run
        inner.addWidget(self._section_label("ANALYSIS INFO"))
        inner.addSpacing(10)

        # A dark card widget to visually group the stats rows
        info_card = QWidget()
        info_card.setStyleSheet(
            "background: #0e0e1e;"
            "border: 1px solid #1e1e30;"
            "border-radius: 6px;"
        )
        card_layout = QVBoxLayout(info_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(8)

        # _info_row returns a tuple of (layout, value_label) so we can update
        # the value label later without rebuilding the whole row
        self._info_islands = self._info_row("Islands detected", "—")
        self._info_bridges = self._info_row("Bridges added", "—")
        self._info_paths   = self._info_row("Total paths", "—")
        self._info_time    = self._info_row("Processing time", "—")

        card_layout.addLayout(self._info_islands[0])
        card_layout.addLayout(self._info_bridges[0])
        card_layout.addLayout(self._info_paths[0])
        card_layout.addLayout(self._info_time[0])
        inner.addWidget(info_card)

        # Push everything up — the spacer fills any leftover vertical space
        inner.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        root.addWidget(content)

        # Another spacer at the root level ensures the panel doesn't grow taller
        # than its content if the window is very tall
        root.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_settings(self) -> PipelineSettings:
        # Read the current values from all spinboxes and package them into
        # a PipelineSettings dataclass for passing to the pipeline runner
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
        # Update the analysis info card after a pipeline run completes
        self._info_islands[1].setText(str(islands))
        self._info_bridges[1].setText(str(bridges))
        self._info_paths[1].setText(str(paths))
        self._info_time[1].setText(f"{elapsed:.2f}s")

    def set_bridge_width_mm(self, value: float) -> None:
        """Update the bridge width spinbox silently (no settings_changed emission)."""
        # blockSignals(True) temporarily prevents the spinbox and slider from
        # emitting their valueChanged signals while we update them programmatically.
        # Without this, setting one would trigger the other in an infinite loop.
        for w in (self._bridge_spin, self._bridge_slider):
            w.blockSignals(True)
        self._bridge_spin.setValue(value)
        self._bridge_slider.setValue(int(value * 10))
        for w in (self._bridge_spin, self._bridge_slider):
            w.blockSignals(False)

    def set_bridge_editing_mode(self, editing: bool, count: int = 1) -> None:
        """Highlight the Bridge Width label when editing selected bridge(s)."""
        if editing:
            # Change the label text to indicate we're editing a specific bridge
            label = f"{count} Bridges" if count > 1 else "Selected Bridge"
            self._bridge_lbl.setText(label)
            # Use the accent colour to visually distinguish "editing a bridge" mode
            self._bridge_lbl.setStyleSheet(f"color: {ACCENT_COLOR}; font-size: 12px; font-weight: 600;")
        else:
            # Restore normal label text and colour
            self._bridge_lbl.setText("Bridge Width")
            self._bridge_lbl.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")

    def reset_info(self) -> None:
        # Clear all info card values back to "—" (e.g. when a new image is opened)
        for _, lbl in [self._info_islands, self._info_bridges, self._info_paths, self._info_time]:
            lbl.setText("—")

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        # Keep the spinbox and slider in sync with each other.
        # When the spinbox changes, update the slider, and vice versa.
        # The ×10 / ÷10 scaling converts between float mm values and integer slider ticks.

        # Bridge width: spinbox → slider (convert mm to slider ticks)
        self._bridge_spin.valueChanged.connect(
            lambda v: self._bridge_slider.setValue(int(v * 10))
        )
        # Bridge width: slider → spinbox (convert slider ticks back to mm)
        self._bridge_slider.valueChanged.connect(
            lambda v: self._bridge_spin.setValue(v / 10.0)
        )

        # Contour smoothing: same pattern
        self._smooth_spin.valueChanged.connect(
            lambda v: self._smooth_slider.setValue(int(v * 10))
        )
        self._smooth_slider.valueChanged.connect(
            lambda v: self._smooth_spin.setValue(v / 10.0)
        )

        # Min area: integer values, so no scaling needed
        self._area_spin.valueChanged.connect(self._area_slider.setValue)
        self._area_slider.valueChanged.connect(self._area_spin.setValue)

        # Emit a settings_changed signal whenever any of the spinboxes change.
        # We use spinboxes (not sliders) here to avoid double-firing when both
        # the spinbox and slider update in response to a single user action.
        self._bridge_spin.valueChanged.connect(self._emit_settings)
        self._smooth_spin.valueChanged.connect(self._emit_settings)
        self._area_spin.valueChanged.connect(self._emit_settings)

    def _emit_settings(self) -> None:
        # Gather the current settings and broadcast them to any connected listeners
        self.settings_changed.emit(self.get_settings())

    # ------------------------------------------------------------------
    # Widget helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _section_label(text: str) -> QWidget:
        # Creates a compact section heading with a coloured accent bar on the left.
        # Returns a QWidget (not a QLabel) so we can include the bar + text together.
        container = QWidget()
        container.setFixedHeight(18)
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # Small coloured rectangle acting as a visual accent bar
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
        # Builds a horizontal row: [Label] [spacer] [SpinBox] [unit text]
        # Returns the spinbox and row layout as a tuple so the caller can
        # add the row to a parent layout and also keep a reference to the spinbox.
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")
        lbl.setToolTip(tooltip)

        # QDoubleSpinBox shows floating-point numbers with Up/Down arrow buttons
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)       # how much one arrow-click changes the value
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setFixedWidth(72)
        spin.setToolTip(tooltip)
        spin.setStyleSheet(
            f"QDoubleSpinBox {{ background: {SURFACE_COLOR}; color: {TEXT_COLOR}; "
            f"border: 1px solid #3a3a54; border-radius: 4px; padding: 2px 4px; }}"
        )

        # Small muted label showing the unit (e.g. "mm")
        unit_lbl = QLabel(unit)
        unit_lbl.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px;")

        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(spin)
        if unit:
            row.addWidget(unit_lbl)

        # Return the spinbox AND the label — the label ref is needed by
        # set_bridge_editing_mode to change the "Bridge Width" text
        return spin, row, lbl

    @staticmethod
    def _labeled_int_spin(
        label: str, unit: str, value: int,
        minimum: int, maximum: int, step: int,
        tooltip: str = "",
    ):
        # Same pattern as _labeled_double_spin but for integer-only values.
        # Does NOT return the label reference (callers don't need to change it).
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")
        lbl.setToolTip(tooltip)

        # QSpinBox is for integers only; use QDoubleSpinBox for decimals
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
        # QSlider is a horizontal draggable track.
        # Qt.Orientation.Horizontal means the track goes left-to-right.
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)

        # The stylesheet controls the appearance of the track (groove) and
        # the draggable circle (handle). sub-page is the filled portion to
        # the left of the handle (shows progress).
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
        # Creates a key-value row: [muted label] [spacer] [bold value label]
        # Returns a tuple of (layout, value_label) so the caller can later
        # call value_label.setText(new_value) to update the displayed number.
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px;")
        val = QLabel(value)
        val.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 11px; font-weight: 600;")
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(val)
        return row, val
