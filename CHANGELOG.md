# Changelog

All notable changes to BridgeIt are listed here.

---

## [1.5.4] - 2026-05-04
### Fixed
- Bridge tabs now attach cleanly to the path without diagonal artifacts. Previously the bridge was snapping to the nearest vertex which could cause a line to shoot off at a wrong angle; it now finds the exact point on the path edge and inserts cleanly between two vertices.

---

## [1.5.3] - 2026-05-02
### Fixed
- DXF open wire ends and distorted bridges caused by the path being resampled twice before export.

---

## [1.5.2] - 2026-05-01
### Fixed
- FreeCAD Draft-to-Sketch no longer crashes. DXF paths are now exported at 64 points per shape instead of 4000+, which is the same accuracy but at a size CAD tools can handle.

---

## [1.5.1] - 2026-04-29
### Fixed
- Bridge tabs no longer pull or distort the surrounding path. Smoothing is now applied before bridge geometry is inserted, so the tab attaches as a clean rectangular detour rather than a rounded curve that drags the nearby lines inward.

---

## [1.5.0] - 2026-04-29
### Added
- **DXF export** — new toolbar button exports a CAD-ready `.dxf` file for Fusion 360, FreeCAD, AutoCAD, SolidWorks, and LibreCAD. Paths are exported in millimetres with correct physical dimensions.
- Website version now updates automatically when a new release tag is pushed.

---

## [1.0.6.1] - 2026-04-29
### Fixed
- SVG output was visually corrupted on complex concave shapes (like letter counters). Reverted the Bézier curve experiment — Chaikin polylines are stable and correct.

---

## [1.0.6] - 2026-04-29
### Fixed
- Missing DPI value when re-exporting with manual bridges, which could produce SVGs with wrong physical dimensions.

---

## [1.0.5.1] - 2026-04-29
### Fixed
- SVG and DXF now use physical millimetre dimensions in the file header. Fixes the "doesn't contain absolute units" error when importing into FreeCAD and other CAD tools.
- Update button now opens the BridgeIt website instead of the GitHub releases page.

---

## [1.0.4] - 2026-04-28
### Added
- macOS Apple Silicon (M1+) support — `.dmg` download now available.
- Linux desktop integration: icon and `.desktop` file installed automatically on first launch from the binary.
### Fixed
- Multiple windows crash on launch in the packaged binary (freeze_support fix).
- Update and Ko-fi buttons were silently doing nothing in the packaged binary.

---

## [1.0.3] - 2026-04-26
### Added
- Checkerboard transparency indicator behind the image preview so transparent areas are clearly visible on all themes.
### Fixed
- Theme toggle now correctly updates all toolbar button styles and separator colours.
- Canvas background stays dark in dark themes.

---

## [1.0.2] - 2026-04-26
### Added
- Initial public release with Windows and Linux binaries.
- AI background removal, contour tracing, floating island detection, and automatic bridge tab generation.
- Manual bridge drawing and deletion tools.
- Lasso and erase tools for cleaning up backgrounds.
- SVG cut-path export (fabrication-ready, hairline stroke).
- SVG image export (filled, coloured vector matching the original artwork).
- Three UI themes: dark, light, and OLED.
