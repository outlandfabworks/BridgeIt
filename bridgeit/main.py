"""
main.py — Application entry point for BridgeIt.

Supports two modes:
  1. GUI mode (default): launches the PyQt6 desktop application.
  2. CLI mode: pass --cli <image_path> [--output <out.svg>] for headless
     pipeline execution (useful for batch processing / CI validation).
"""

from __future__ import annotations

import logging
import sys

# Configure root logger: INFO to stderr so users can capture diagnostics with
# `bridgeit 2>bridgeit.log`.  Only set up when this module initialises (not on
# every import) so library users aren't affected.
logging.basicConfig(
    format="%(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)


def _init_theme_from_system(app) -> None:
    """Set the initial BridgeIt theme based on the OS colour scheme.

    Uses Qt's StyleHints.colorScheme() (available in Qt 6.5+).  On older Qt
    or platforms that don't expose the setting, we keep the default "dark" theme.
    """
    try:
        from PyQt6.QtCore import Qt
        from bridgeit.gui import themes
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            themes._current = "light"
        # Dark and Unknown → keep default "dark"
    except Exception:
        pass   # any failure is non-fatal; theme stays at "dark"


def _ensure_linux_integration() -> None:
    """On first run from a PyInstaller binary on Linux, silently install the
    .desktop file and icons so the taskbar shows the BridgeIt icon without
    the user needing to run install.sh manually."""
    import sys, os, shutil, subprocess
    if not getattr(sys, 'frozen', False):
        return
    if sys.platform != 'linux':
        return
    desktop_dir = os.path.expanduser("~/.local/share/applications")
    desktop_file = os.path.join(desktop_dir, "BridgeIt.desktop")
    if os.path.exists(desktop_file):
        return

    exe = sys.executable
    asset_dir = os.path.join(sys._MEIPASS, "bridgeit", "assets")
    if not os.path.isdir(asset_dir):
        return

    try:
        # Install icons
        icon_base = os.path.expanduser("~/.local/share/icons/hicolor")
        for sz in [16, 32, 48, 64, 128, 256, 512]:
            src = os.path.join(asset_dir, f"icon_{sz}.png")
            if os.path.exists(src):
                dst_dir = os.path.join(icon_base, f"{sz}x{sz}", "apps")
                os.makedirs(dst_dir, exist_ok=True)
                shutil.copy2(src, os.path.join(dst_dir, "BridgeIt.png"))

        # Install .desktop file
        os.makedirs(desktop_dir, exist_ok=True)
        with open(desktop_file, "w") as f:
            f.write(f"""[Desktop Entry]
Type=Application
Version=1.1
Name=BridgeIt
GenericName=Laser Cutting SVG Converter
Comment=Convert images to fabrication-ready SVGs with automatic bridge generation
Exec={exe}
Icon=BridgeIt
Categories=Graphics;VectorGraphics;2DGraphics;
Keywords=laser;cutting;svg;vector;bridge;fabrication;
StartupWMClass=BridgeIt
MimeType=image/png;image/jpeg;image/webp;image/bmp;
""")

        # Refresh caches silently
        subprocess.run(
            ["gtk-update-icon-cache", "-f", "-t", icon_base],
            capture_output=True
        )
        subprocess.run(
            ["update-desktop-database", desktop_dir],
            capture_output=True
        )
    except Exception:
        pass  # non-fatal — app still works, icon just might not show


def _run_gui() -> None:
    # Auto-install desktop entry and icons on first run from a binary
    _ensure_linux_integration()

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

    # QApplication is the root object for every PyQt6 app — it manages
    # the event loop that keeps the window alive and responsive.
    # sys.argv lets Qt parse any Qt-specific command-line flags.
    app = QApplication(sys.argv)

    # On Linux this links the running process to the BridgeIt.desktop file,
    # which controls the taskbar icon and app name.
    app.setDesktopFileName("BridgeIt")   # links to BridgeIt.desktop on Linux

    # Resolve icon path — use sys._MEIPASS when running as a PyInstaller
    # binary (more reliable than __file__-relative paths in frozen builds).
    from pathlib import Path
    if getattr(sys, 'frozen', False):
        _icon_path = Path(sys._MEIPASS) / "bridgeit" / "assets" / "icon_256.png"
    else:
        _icon_path = Path(__file__).parent / "assets" / "icon_256.png"
    if _icon_path.exists():
        app.setWindowIcon(QIcon(str(_icon_path)))

    # QPalette is Qt's colour theme system; setting it here ensures that
    # widgets drawn by the OS (like scroll bars) also use our dark theme,
    # not just the widgets we style manually via stylesheets.
    palette = QPalette()

    # ColorRole.Window is the background of top-level windows
    palette.setColor(QPalette.ColorRole.Window, QColor(PREVIEW_BG_COLOR))
    # ColorRole.WindowText is the default text colour for window backgrounds
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_COLOR))

    # Base is the background of text-editing areas (e.g. input boxes)
    palette.setColor(QPalette.ColorRole.Base, QColor("#16162a"))
    # AlternateBase is used for alternating row colours in lists/tables
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1e1e2e"))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT_COLOR))

    # Button colours affect native-drawn buttons inside dialogs
    palette.setColor(QPalette.ColorRole.Button, QColor("#2a2a3e"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_COLOR))

    # Highlight is the selection colour (e.g. text selected in a spinbox)
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#7c3aed"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))

    # Apply our palette so the entire application uses the dark theme
    app.setPalette(palette)

    # Pick the initial theme based on the OS colour scheme so the app feels
    # at home on both dark and light desktops.  Falls back to "dark" on any
    # platform that doesn't expose this information.
    _init_theme_from_system(app)

    # Create and display the main window
    window = MainWindow()
    window.show()

    # app.exec() starts the Qt event loop — it blocks here, processing user
    # input and screen redraws, until the window is closed.
    # sys.exit() relays the exit code to the OS so scripts can detect failure.
    sys.exit(app.exec())


def _run_cli(args: list[str]) -> None:
    # In CLI mode we only import what's needed for the processing pipeline.
    # This avoids loading PyQt6 on systems without a display.
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
    parser.add_argument("--min-area", type=float, default=50.0, help="Minimum contour area (px²)")

    # Parse the arguments list into a Namespace object with named attributes
    parsed = parser.parse_args(args)

    # Build a settings object from the parsed arguments.
    # PipelineSettings is a simple dataclass that bundles all tunable values.
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
    # with a non-zero code so the caller knows it failed.
    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)
        sys.exit(1)

    # Print a summary of what was found and created.
    # These counts come from the intermediate analysis/bridge result objects.
    print(f"Islands:  {len(result.analysis.islands)}")
    print(f"Bridges:  {len(result.bridge_result.bridges)}")
    print(f"Paths:    {len(result.bridge_result.paths)}")
    print(f"Time:     {result.elapsed_seconds:.2f}s")

    if result.svg_path:
        print(f"SVG saved: {result.svg_path}")
    else:
        print("(no output path specified — SVG not saved)")


def main() -> None:
    # sys.argv[1:] strips the script name from the argument list,
    # leaving only the arguments the user actually typed.
    args = sys.argv[1:]

    # --cli flag switches from GUI mode to headless command-line mode.
    # We remove it from the list before passing args to the CLI parser
    # so argparse doesn't see it as an unknown flag.
    if "--cli" in args:
        args.remove("--cli")
        _run_cli(args)
    else:
        _run_gui()


# This guard ensures main() only runs when executing this file directly,
# not when it's imported as a module by another script.
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
