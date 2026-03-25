"""
main.py — Application entry point for BridgeIt.

Supports two modes:
  1. GUI mode (default): launches the PyQt6 desktop application.
  2. CLI mode: pass --cli <image_path> [--output <out.svg>] for headless
     pipeline execution (useful for batch processing / CI validation).
"""

from __future__ import annotations

import sys


def _run_gui() -> None:
    from PyQt6.QtGui import QIcon, QPalette, QColor
    from PyQt6.QtWidgets import QApplication
    from bridgeit.gui.mainwindow import MainWindow
    from bridgeit.config import PREVIEW_BG_COLOR, TEXT_COLOR

    app = QApplication(sys.argv)
    app.setApplicationName("BridgeIt")
    app.setOrganizationName("BridgeIt")

    # Dark palette as a base (stylesheet overrides most, but this helps
    # Qt-drawn elements like scroll bars)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(PREVIEW_BG_COLOR))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_COLOR))
    palette.setColor(QPalette.ColorRole.Base, QColor("#16162a"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1e1e2e"))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT_COLOR))
    palette.setColor(QPalette.ColorRole.Button, QColor("#2a2a3e"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_COLOR))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#7c3aed"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def _run_cli(args: list[str]) -> None:
    import argparse
    from bridgeit.pipeline.pipeline import PipelineRunner, PipelineSettings

    parser = argparse.ArgumentParser(
        prog="bridgeit",
        description="BridgeIt — Convert images to fabrication-ready SVGs",
    )
    parser.add_argument("image", help="Input PNG/JPG image path")
    parser.add_argument("-o", "--output", default=None, help="Output SVG path")
    parser.add_argument("--bridge-width", type=float, default=0.5, help="Bridge width in mm (default: 0.5)")
    parser.add_argument("--smoothing", type=float, default=2.0, help="Contour smoothing factor")
    parser.add_argument("--min-area", type=float, default=100.0, help="Minimum contour area (px²)")

    parsed = parser.parse_args(args)

    settings = PipelineSettings(
        bridge_width_mm=parsed.bridge_width,
        contour_smoothing=parsed.smoothing,
        min_contour_area=parsed.min_area,
    )

    def on_progress(stage, msg):
        print(f"  [{stage.name}] {msg}")

    runner = PipelineRunner(settings=settings, on_progress=on_progress)
    print(f"Processing: {parsed.image}")
    result = runner.run(parsed.image, output_svg=parsed.output)

    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)
        sys.exit(1)

    print(f"Islands:  {len(result.analysis.islands)}")
    print(f"Bridges:  {len(result.bridge_result.bridges)}")
    print(f"Paths:    {len(result.bridge_result.paths)}")
    print(f"Time:     {result.elapsed_seconds:.2f}s")

    if result.svg_path:
        print(f"SVG saved: {result.svg_path}")
    else:
        print("(no output path specified — SVG not saved)")


def main() -> None:
    args = sys.argv[1:]
    if "--cli" in args:
        args.remove("--cli")
        _run_cli(args)
    else:
        _run_gui()


if __name__ == "__main__":
    main()
