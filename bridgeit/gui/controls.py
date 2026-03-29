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
    DEFAULT_BRIDGE_WIDTH_MM,
    DEFAULT_CONTOUR_SMOOTHING,
    DEFAULT_MIN_CONTOUR_AREA,
)
from bridgeit.gui.themes import current_theme
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
        self.setObjectName("ControlsPanel")
        self.setMinimumWidth(260)
        self.setMaximumWidth(320)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Scrollable content area ───────────────────────────────────────
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        inner = QVBoxLayout(content)
        inner.setContentsMargins(14, 16, 14, 16)
        inner.setSpacing(12)

        # ── Card 1: Conversion Settings ───────────────────────────────────
        # Each section lives in a rounded card so it reads as a visual unit.
        conv_card, conv_inner = self._make_card("CONVERSION SETTINGS")
        self._conv_card = conv_card   # kept for apply_theme()

        # Bridge width: spinbox + slider pair
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
        conv_inner.addLayout(bridge_row)
        self._bridge_slider = self._make_slider(1, 50, int(DEFAULT_BRIDGE_WIDTH_MM * 10))
        conv_inner.addWidget(self._bridge_slider)
        conv_inner.addSpacing(10)

        # Contour smoothing: spinbox + slider pair
        self._smooth_spin, smooth_row, _ = self._labeled_double_spin(
            label="Contour Smoothing",
            unit="",
            value=DEFAULT_CONTOUR_SMOOTHING,
            minimum=0.0,
            maximum=10.0,
            step=0.5,
            decimals=1,
            tooltip="Douglas-Peucker simplification factor.\n0 = no simplification.\nHigher = smoother, fewer points.",
        )
        conv_inner.addLayout(smooth_row)
        self._smooth_slider = self._make_slider(0, 100, int(DEFAULT_CONTOUR_SMOOTHING * 10))
        conv_inner.addWidget(self._smooth_slider)
        conv_inner.addSpacing(10)

        # Min contour area: spinbox + slider pair
        self._area_spin, area_row = self._labeled_int_spin(
            label="Min Area Filter",
            unit="px²",
            value=int(DEFAULT_MIN_CONTOUR_AREA),
            minimum=0,
            maximum=5000,
            step=50,
            tooltip="Contours smaller than this area are ignored as noise.\nIncrease if you see speckles in the output.",
        )
        conv_inner.addLayout(area_row)
        self._area_slider = self._make_slider(0, 5000, int(DEFAULT_MIN_CONTOUR_AREA))
        conv_inner.addWidget(self._area_slider)

        inner.addWidget(conv_card)

        # ── Card 2: Analysis Info ─────────────────────────────────────────
        info_card, info_inner = self._make_card("ANALYSIS INFO")
        self._info_card = info_card   # kept for apply_theme()

        self._info_islands = self._info_row(
            "Islands detected", "",
            "Closed shapes found in the image — each one needs bridges to stay attached"
        )
        self._info_bridges = self._info_row(
            "Bridges added", "",
            "Physical tabs connecting islands to the surrounding sheet so they don't fall out when cut"
        )
        self._info_paths   = self._info_row(
            "Total paths", "",
            "Total number of cut paths exported in the SVG (islands + bridge outlines)"
        )
        self._info_time    = self._info_row(
            "Processing time", "",
            "Time taken to remove background, trace contours, and calculate bridges"
        )

        for row_layout, _ in [self._info_islands, self._info_bridges,
                               self._info_paths, self._info_time]:
            info_inner.addLayout(row_layout)

        inner.addWidget(info_card)

        inner.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        root.addWidget(content)
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
        t = current_theme()
        if editing:
            # Change the label text to indicate we're editing a specific bridge
            label = f"{count} Bridges" if count > 1 else "Selected Bridge"
            self._bridge_lbl.setText(label)
            # Use the accent colour to visually distinguish "editing a bridge" mode
            self._bridge_lbl.setStyleSheet(f"color: {t['accent']}; font-size: 12px; font-weight: 600;")
        else:
            # Restore normal label text and colour
            self._bridge_lbl.setText("Bridge Width")
            self._bridge_lbl.setStyleSheet(f"color: {t['text']}; font-size: 12px;")

    def apply_theme(self, t: dict) -> None:
        """Re-style all controls to match the given theme dict.

        Most widget colours are now driven by the global app QSS (set in
        MainWindow._apply_theme), so only widgets with unavoidable inline
        stylesheets need explicit updates here.
        """
        card_style = (
            f"QWidget#card {{ background: {t['surface']}; "
            f"border: 1px solid {t['border_faint']}; border-radius: 10px; }}"
        )
        sep_style = f"background: {t['border_faint']}; border: none;"
        for card in [self._conv_card, self._info_card]:
            if card:
                card.setStyleSheet(card_style)
                # Update the thin separator line stored on the card widget
                if hasattr(card, "_sep"):
                    card._sep.setStyleSheet(sep_style)

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
    def _make_card(title: str):
        """Create a rounded, bordered card widget for grouping related controls.

        Returns (card_widget, inner_layout) so the caller can add controls to
        the inner layout while the card widget gets added to the parent layout.
        The card has a visible title label at the top as the section heading.
        """
        t = current_theme()

        card = QWidget()
        card.setStyleSheet(
            f"QWidget#card {{ "
            f"background: {t['surface']}; "
            f"border: 1px solid {t['border_faint']}; "
            f"border-radius: 10px; }}"
        )
        card.setObjectName("card")

        outer = QVBoxLayout(card)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(8)

        # Section title sits inside the card at the top
        title_lbl = QLabel(title)
        title_lbl.setProperty("muted", True)
        # No inline color — global QSS QLabel[muted="true"] handles it
        title_lbl.setStyleSheet(
            "font-size: 9px; font-weight: 700; letter-spacing: 1.8px; "
            "background: transparent; border: none;"
        )
        outer.addWidget(title_lbl)

        # Thin separator line — colour must come from inline style (no selector available)
        # We store it on the card so apply_theme() can update it on theme change.
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {t['border_faint']}; border: none;")
        card._sep = sep   # store ref for apply_theme() re-colouring
        outer.addWidget(sep)
        outer.addSpacing(2)

        return card, outer

    @staticmethod
    def _section_label(text: str) -> QWidget:
        # Creates a compact section heading with a coloured accent bar on the left.
        # Returns a QWidget (not a QLabel) so we can include the bar + text together.
        t = current_theme()
        container = QWidget()
        container.setFixedHeight(18)
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # Small coloured rectangle acting as a visual accent bar
        bar = QWidget()
        bar.setFixedSize(3, 12)
        bar.setStyleSheet(f"background: {t['accent']}; border-radius: 1px;")

        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {t['text_muted']}; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;"
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
        # No inline color — global app QSS supplies QLabel { color: t['text'] }
        lbl.setStyleSheet("font-size: 12px;")
        lbl.setToolTip(tooltip)

        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setFixedWidth(72)
        spin.setToolTip(tooltip)
        # No inline stylesheet — global app QSS handles QDoubleSpinBox colours.
        # Inline styles bake in the build-time theme and can't be overridden by
        # the app stylesheet when the user cycles themes.

        unit_lbl = QLabel(unit)
        # "muted" property lets the global QSS target these with
        # QLabel[muted="true"] { color: t['text_muted']; }
        unit_lbl.setProperty("muted", True)
        unit_lbl.setStyleSheet("font-size: 11px;")

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
        lbl.setStyleSheet("font-size: 12px;")
        lbl.setToolTip(tooltip)

        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setValue(value)
        spin.setFixedWidth(72)
        spin.setToolTip(tooltip)
        # No inline stylesheet — global app QSS handles QSpinBox colours.

        unit_lbl = QLabel(unit)
        unit_lbl.setProperty("muted", True)
        unit_lbl.setStyleSheet("font-size: 11px;")

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
        # No inline stylesheet — global app QSS handles QSlider colours.
        # This means sliders always reflect the current theme when it cycles.
        return slider

    @staticmethod
    def _info_row(label: str, value: str, tooltip: str = ""):
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setProperty("muted", True)
        lbl.setStyleSheet("font-size: 11px;")
        if tooltip:
            lbl.setToolTip(tooltip)
        val = QLabel(value)
        # Keep font-weight inline (not a colour, safe to bake in)
        val.setStyleSheet("font-size: 11px; font-weight: 600;")
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(val)
        return row, val
