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

# PIL is the Python Imaging Library (Pillow fork) — used to open and manipulate images
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
from bridgeit.pipeline.remove_bg import color_erase_removal, remove_background
from bridgeit.pipeline.trace import Path2D, get_image_size, trace_contours


# Enum.auto() assigns sequential integer values automatically.
# These names identify each processing stage for progress reporting.
class Stage(Enum):
    REMOVE_BG = auto()   # background removal
    TRACE = auto()        # contour tracing
    ANALYZE = auto()      # island detection
    BRIDGE = auto()       # bridge generation
    EXPORT = auto()       # SVG writing


@dataclass
class PipelineSettings:
    # All user-adjustable pipeline parameters in one convenient bundle.
    # The default values come from config.py so there is one canonical source.
    bridge_width_mm: float = DEFAULT_BRIDGE_WIDTH_MM     # how wide each bridge is
    contour_smoothing: float = DEFAULT_CONTOUR_SMOOTHING # path simplification amount
    min_contour_area: float = DEFAULT_MIN_CONTOUR_AREA   # noise filter threshold
    dpi: float = DEFAULT_DPI                              # resolution for mm↔px conversion
    # Manual background erase colours picked by the user in the GUI.
    # When non-empty, colour-range erasure replaces the auto bg-removal step.
    erase_colors: list = field(default_factory=list)   # [(r, g, b), ...]
    erase_tolerance: float = 50.0                       # Euclidean RGB tolerance


@dataclass
class PipelineResult:
    """Full pipeline output — all intermediate results attached."""
    source_path: Optional[Path]                        # original input file, if any

    # Each field stores the output of one pipeline stage.
    # They are None until the pipeline reaches that stage.
    nobg_image: Optional[Image.Image] = None           # background-removed image
    paths: Optional[list[Path2D]] = None               # traced vector paths
    analysis: Optional[AnalysisResult] = None          # island classification
    bridge_result: Optional[BridgeResult] = None       # paths with bridges inserted
    svg_path: Optional[Path] = None                    # saved SVG file path
    svg_string: Optional[str] = None                   # SVG as a string (for preview)
    elapsed_seconds: float = 0.0                       # total wall-clock time
    error: Optional[str] = None                        # error message if pipeline failed

    @property
    def success(self) -> bool:
        # Convenience: True if no error occurred
        return self.error is None


# Type alias: a progress callback receives the current Stage and a human-readable message.
# The GUI uses this to update the status bar; the CLI uses it to print to the terminal.
ProgressCallback = Callable[[Stage, str], None]


class PipelineRunner:
    """Runs the BridgeIt processing pipeline."""

    def __init__(
        self,
        settings: Optional[PipelineSettings] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        # Use default settings if none were provided
        self.settings = settings or PipelineSettings()

        # The progress callback is optional — the pipeline works fine without it
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
        # Record start time so we can report elapsed seconds at the end
        t0 = time.monotonic()

        # If source is a file path, store it for later use in the result
        source_path = Path(source) if isinstance(source, (str, Path)) else None
        result = PipelineResult(source_path=source_path)

        try:
            # Stage 1: Remove background — this is the slowest step for photos
            # because it runs an AI model; logos use the faster threshold method.
            # If the user has sampled erase colours in the GUI, use colour-range
            # erasure instead of the auto-detection / AI path.
            self._progress(Stage.REMOVE_BG, "Removing background…")
            if self.settings.erase_colors:
                from bridgeit.pipeline.remove_bg import _load_image, _cap_size
                img = _cap_size(_load_image(source))
                result.nobg_image = color_erase_removal(
                    img,
                    self.settings.erase_colors,
                    self.settings.erase_tolerance,
                )
            else:
                result.nobg_image = remove_background(source)

            # Stage 2: Trace the outlines of all shapes in the transparent image
            self._progress(Stage.TRACE, "Tracing contours…")
            result.paths = trace_contours(
                result.nobg_image,
                smoothing=self.settings.contour_smoothing,
                min_area=self.settings.min_contour_area,
            )
            img_size = get_image_size(result.nobg_image)

            # Stage 3: Classify shapes as mainland or floating island
            self._progress(Stage.ANALYZE, "Detecting islands…")
            result.analysis = analyze_islands(result.paths, img_size)

            # Stage 4: Insert bridge geometry into island paths
            self._progress(Stage.BRIDGE, "Generating bridges…")
            result.bridge_result = add_bridges(
                result.analysis,
                bridge_width_mm=self.settings.bridge_width_mm,
                dpi=self.settings.dpi,
            )

            # Stage 5: Serialise paths to SVG format
            self._progress(Stage.EXPORT, "Exporting SVG…")
            result.svg_string = export_svg_string(result.bridge_result)

            # Optionally save the SVG to disk if the caller provided a path
            if output_svg:
                result.svg_path = export_svg(result.bridge_result, output_svg)

        except Exception as exc:
            # Capture any error from any stage and store it in the result.
            # This lets the caller decide how to display it rather than crashing.
            result.error = str(exc)

        # Calculate total processing time
        result.elapsed_seconds = time.monotonic() - t0
        return result

    def run_to_preview(
        self,
        nobg_image: Image.Image,
    ) -> PipelineResult:
        """Re-run trace→analyze→bridge→export with updated settings.

        Skips the slow remove_bg stage, useful for live preview updates.
        """
        # This method is called when the user adjusts a setting slider while
        # an image is already loaded — we reuse the cached background-removed
        # image and only re-run the fast stages.
        t0 = time.monotonic()

        # nobg_image is already provided by the caller (cached from the full run)
        result = PipelineResult(source_path=None, nobg_image=nobg_image)

        try:
            # Re-trace contours with new smoothing/area settings
            self._progress(Stage.TRACE, "Tracing contours…")
            result.paths = trace_contours(
                nobg_image,
                smoothing=self.settings.contour_smoothing,
                min_area=self.settings.min_contour_area,
            )
            img_size = get_image_size(nobg_image)

            # Re-classify islands (island membership can change with different smoothing)
            self._progress(Stage.ANALYZE, "Detecting islands…")
            result.analysis = analyze_islands(result.paths, img_size)

            # Re-generate bridges with the new width setting
            self._progress(Stage.BRIDGE, "Generating bridges…")
            result.bridge_result = add_bridges(
                result.analysis,
                bridge_width_mm=self.settings.bridge_width_mm,
                dpi=self.settings.dpi,
            )

            # Produce an SVG string for the canvas to display
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
        # Fire the progress callback if one was provided.
        # The guard prevents a crash when the runner is used without a callback.
        if self._on_progress:
            self._on_progress(stage, message)
