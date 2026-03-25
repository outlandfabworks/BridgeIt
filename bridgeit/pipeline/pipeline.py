"""
pipeline.py — Orchestrates all processing stages.

Provides a single PipelineRunner class that chains:
  remove_bg → trace → analyze → bridge → export

Also supports partial runs (e.g. run only up to the trace stage for a
faster preview while the user adjusts settings).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional, Union

from PIL import Image

from bridgeit.config import (
    DEFAULT_BRIDGE_WIDTH_MM,
    DEFAULT_CONTOUR_SMOOTHING,
    DEFAULT_DPI,
    DEFAULT_MIN_CONTOUR_AREA,
)
from bridgeit.pipeline.analyze import AnalysisResult, analyze_islands
from bridgeit.pipeline.bridge import BridgeResult, add_bridges
from bridgeit.pipeline.export import export_svg, export_svg_string
from bridgeit.pipeline.remove_bg import remove_background
from bridgeit.pipeline.trace import Path2D, get_image_size, trace_contours


class Stage(Enum):
    REMOVE_BG = auto()
    TRACE = auto()
    ANALYZE = auto()
    BRIDGE = auto()
    EXPORT = auto()


@dataclass
class PipelineSettings:
    bridge_width_mm: float = DEFAULT_BRIDGE_WIDTH_MM
    contour_smoothing: float = DEFAULT_CONTOUR_SMOOTHING
    min_contour_area: float = DEFAULT_MIN_CONTOUR_AREA
    dpi: float = DEFAULT_DPI


@dataclass
class PipelineResult:
    """Full pipeline output — all intermediate results attached."""
    source_path: Optional[Path]
    nobg_image: Optional[Image.Image] = None
    paths: Optional[list[Path2D]] = None
    analysis: Optional[AnalysisResult] = None
    bridge_result: Optional[BridgeResult] = None
    svg_path: Optional[Path] = None
    svg_string: Optional[str] = None
    elapsed_seconds: float = 0.0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


ProgressCallback = Callable[[Stage, str], None]


class PipelineRunner:
    """Runs the BridgeIt processing pipeline."""

    def __init__(
        self,
        settings: Optional[PipelineSettings] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        self.settings = settings or PipelineSettings()
        self._on_progress = on_progress

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        source: Union[str, Path, Image.Image],
        output_svg: Optional[Union[str, Path]] = None,
    ) -> PipelineResult:
        """Run the full pipeline from source image to SVG.

        Args:
            source: Input image path or PIL Image.
            output_svg: Optional output .svg path. If None, SVG is only
                        returned as a string in PipelineResult.svg_string.

        Returns:
            PipelineResult with all stage outputs.
        """
        t0 = time.monotonic()
        source_path = Path(source) if isinstance(source, (str, Path)) else None
        result = PipelineResult(source_path=source_path)

        try:
            # Stage 1: Remove background
            self._progress(Stage.REMOVE_BG, "Removing background…")
            result.nobg_image = remove_background(source)

            # Stage 2: Trace contours
            self._progress(Stage.TRACE, "Tracing contours…")
            result.paths = trace_contours(
                result.nobg_image,
                smoothing=self.settings.contour_smoothing,
                min_area=self.settings.min_contour_area,
            )
            img_size = get_image_size(result.nobg_image)

            # Stage 3: Detect islands
            self._progress(Stage.ANALYZE, "Detecting islands…")
            result.analysis = analyze_islands(result.paths, img_size)

            # Stage 4: Generate bridges
            self._progress(Stage.BRIDGE, "Generating bridges…")
            result.bridge_result = add_bridges(
                result.analysis,
                bridge_width_mm=self.settings.bridge_width_mm,
                dpi=self.settings.dpi,
            )

            # Stage 5: Export SVG
            self._progress(Stage.EXPORT, "Exporting SVG…")
            result.svg_string = export_svg_string(result.bridge_result)
            if output_svg:
                result.svg_path = export_svg(result.bridge_result, output_svg)

        except Exception as exc:
            result.error = str(exc)

        result.elapsed_seconds = time.monotonic() - t0
        return result

    def run_to_preview(
        self,
        nobg_image: Image.Image,
    ) -> PipelineResult:
        """Re-run trace→analyze→bridge→export with updated settings.

        Skips the slow remove_bg stage, useful for live preview updates.
        """
        t0 = time.monotonic()
        result = PipelineResult(source_path=None, nobg_image=nobg_image)

        try:
            self._progress(Stage.TRACE, "Tracing contours…")
            result.paths = trace_contours(
                nobg_image,
                smoothing=self.settings.contour_smoothing,
                min_area=self.settings.min_contour_area,
            )
            img_size = get_image_size(nobg_image)

            self._progress(Stage.ANALYZE, "Detecting islands…")
            result.analysis = analyze_islands(result.paths, img_size)

            self._progress(Stage.BRIDGE, "Generating bridges…")
            result.bridge_result = add_bridges(
                result.analysis,
                bridge_width_mm=self.settings.bridge_width_mm,
                dpi=self.settings.dpi,
            )

            self._progress(Stage.EXPORT, "Rendering SVG…")
            result.svg_string = export_svg_string(result.bridge_result)

        except Exception as exc:
            result.error = str(exc)

        result.elapsed_seconds = time.monotonic() - t0
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _progress(self, stage: Stage, message: str) -> None:
        if self._on_progress:
            self._on_progress(stage, message)
