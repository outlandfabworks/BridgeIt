# BridgeIt

Convert images to fabrication-ready SVGs with automatic bridge generation for laser cutting.

![BridgeIt Icon](bridgeit/assets/icon.png)

## What it does

BridgeIt takes a photo or graphic, removes the background, traces the outlines as smooth vector paths, detects floating "islands" (parts that would fall away during laser cutting), and automatically adds thin bridge tabs to keep everything connected in a single piece.

## Features

- **Background removal** — AI-powered (U2Net via rembg) with manual erase and lasso tools
- **Contour tracing** — smooth vector paths from raster images using OpenCV + Chaikin subdivision
- **Island detection** — identifies parts that would fall away when cut
- **Auto bridge** — suggests bridge placements; review and confirm before export
- **Manual bridge** — click-and-drag to place bridges exactly where you want them
- **Image SVG export** — colored, filled SVG matching the original artwork
- **Cut-path SVG export** — stroke-only SVG ready for laser cutter software
- **Dark / light theme** — toggle with the theme button in the toolbar

## Installation

### Linux (recommended)

Both the pre-built binary and the source install use the same one-command installer, which handles the app, taskbar icon, and `.desktop` launcher entry automatically.

**From a release download** — extract the `.tar.gz`, then:
```bash
cd BridgeIt-linux
./install.sh
```

**From source**:
```bash
git clone https://github.com/outlandfabworks/BridgeIt.git
cd BridgeIt
./install.sh
```

To uninstall: `./install.sh --uninstall`

### Manual / other platforms

Requirements: Python 3.10 or newer with a display (Qt6 GUI application).

```bash
git clone https://github.com/outlandfabworks/BridgeIt.git
cd BridgeIt
pip install .
```

### First launch

The first time you process an image, BridgeIt downloads the U2Net background-removal model (~170 MB) to `~/.u2net/`. Subsequent runs use the cached model.

## Usage

```bash
bridgeit          # launch GUI
bridgeit-gui      # same, via gui-scripts entry point
```

1. Click **Open Image** (or drag a file onto the window) to load a photo or graphic
2. Adjust **Smoothing** and **Min Area** in the left panel if needed
3. Use **Erase** mode to clean up background remnants
4. Click **Auto Bridge** to generate bridge suggestions, then **Confirm** to accept them
5. Click **Export SVG** to save the cut-path file, or **Export Image SVG** for the colored version

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+O` | Open image |
| `Ctrl+S` | Export cut-path SVG |
| `B` | Toggle bridge mode |
| `Ctrl+Z` | Undo |
| `Ctrl+Shift+Z` | Redo |
| `Home` | Fit view |
| `Delete` | Delete selected path or bridge |
| `Escape` | Exit current mode |

## Output formats

### Cut-path SVG (`Export SVG`)
Stroke-only paths suitable for direct import into laser cutter software (LightBurn, RDWorks, etc.). Includes bridge tabs as part of the path geometry.

### Image SVG (`Export Image SVG`)
Filled, colored SVG that reproduces the original artwork's appearance. Uses `fill-rule="evenodd"` compound paths so interior holes (letter counters, logo cutouts) are correctly punched through.

## Dependencies

| Package | Purpose |
|---------|---------|
| PyQt6 | GUI framework |
| rembg | AI background removal |
| opencv-python | Contour tracing |
| shapely | Bridge geometry calculations |
| svgwrite | SVG file generation |
| Pillow | Image processing |
| numpy | Array operations |
| onnxruntime | U2Net model inference |

## License

MIT — see [LICENSE](LICENSE)
