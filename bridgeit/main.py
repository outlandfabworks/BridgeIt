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
    # Import PyQt6 GUI components — these are only needed in GUI mode,
    # so we import inside the function to keep CLI mode lean.
    from PyQt6.QtGui import QIcon, QPalette, QColor
    from PyQt6.QtWidgets import QApplication
    from bridgeit.gui.mainwindow import MainWindow
    from bridgeit.config import PREVIEW_BG_COLOR, TEXT_COLOR

    # These three lines must be set BEFORE QApplication is fully constructed
    # so the window-manager class hint on X11/Wayland reads "BridgeIt"
    # instead of "python3".
    QApplication.setApplicationName("BridgeIt")
    QApplication.setApplicationDisplayName("BridgeIt")
    QApplication.setOrganizationName("BridgeIt")

    # QApplication is the root object for every PyQt6 app.
    # sys.argv lets Qt parse any Qt-specific command-line flags.
    app = QApplication(sys.argv)

    # On Linux this links the running process to the BridgeIt.desktop file,
    # which controls the taskbar icon and app name.
    app.setDesktopFileName("BridgeIt")   # links to BridgeIt.desktop on Linux

    # QPalette lets us set a dark colour theme for Qt's own widgets
    # (e.g. scroll bars) that the stylesheet doesn't fully cover.
    palette = QPalette()

    # ColorRole.Window is the background of top-level windows
    palette.setColor(QPalette.ColorRole.Window, QColor(PREVIEW_BG_COLOR))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_COLOR))

    # Base is the background of text-editing areas (e.g. input boxes)
    palette.setColor(QPalette.ColorRole.Base, QColor("#16162a"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1e1e2e"))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT_COLOR))

    # Button colours affect native-drawn buttons inside dialogs
    palette.setColor(QPalette.ColorRole.Button, QColor("#2a2a3e"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_COLOR))

    # Highlight is the selection colour (e.g. text selected in a spinbox)
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#7c3aed"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    # Create and display the main window
    window = MainWindow()
    window.show()

    # app.exec() starts the Qt event loop — it blocks until the window closes,
    # then sys.exit() relays the exit code to the OS.
    sys.exit(app.exec())


def _run_cli(args: list[str]) -> None:
    # In CLI mode we only import what's needed for the processing pipeline
    import argparse
    from bridgeit.pipeline.pipeline import PipelineRunner, PipelineSettings

    # argparse builds a nice --help message and validates command-line arguments
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

    # Build a settings object from the parsed arguments
    settings = PipelineSettings(
        bridge_width_mm=parsed.bridge_width,
        contour_smoothing=parsed.smoothing,
        min_contour_area=parsed.min_area,
    )

    # This callback is called by the pipeline for each processing stage
    # so the user sees live progress in the terminal.
    def on_progress(stage, msg):
        print(f"  [{stage.name}] {msg}")

    runner = PipelineRunner(settings=settings, on_progress=on_progress)
    print(f"Processing: {parsed.image}")

    # Run the full pipeline: remove background → trace → analyze → bridge → export
    result = runner.run(parsed.image, output_svg=parsed.output)

    # If anything went wrong inside the pipeline, print the error and exit
    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)
        sys.exit(1)

    # Print a summary of what was found and created
    print(f"Islands:  {len(result.analysis.islands)}")
    print(f"Bridges:  {len(result.bridge_result.bridges)}")
    print(f"Paths:    {len(result.bridge_result.paths)}")
    print(f"Time:     {result.elapsed_seconds:.2f}s")

    if result.svg_path:
        print(f"SVG saved: {result.svg_path}")
    else:
        print("(no output path specified — SVG not saved)")


def main() -> None:
    # sys.argv[1:] strips the script name from the argument list
    args = sys.argv[1:]

    # --cli flag switches from GUI mode to headless command-line mode
    if "--cli" in args:
        args.remove("--cli")
        _run_cli(args)
    else:
        _run_gui()


# This guard ensures main() only runs when executing this file directly,
# not when it's imported as a module by another script.
if __name__ == "__main__":
    main()
