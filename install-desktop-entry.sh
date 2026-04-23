#!/usr/bin/env bash
# Creates a .desktop entry so BridgeIt appears in your app launcher and
# shows its icon in the taskbar.
# Run this once from the extracted BridgeIt folder:
#   chmod +x install-desktop-entry.sh
#   ./install-desktop-entry.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXEC="$SCRIPT_DIR/BridgeIt"
ICON="$SCRIPT_DIR/bridgeit/assets/icon_256.png"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons"

mkdir -p "$DESKTOP_DIR" "$ICON_DIR"

cp "$ICON" "$ICON_DIR/BridgeIt.png"

cat > "$DESKTOP_DIR/BridgeIt.desktop" <<EOF
[Desktop Entry]
Name=BridgeIt
Comment=Convert images to fabrication-ready SVGs
Exec=$EXEC
Icon=BridgeIt
Type=Application
Categories=Graphics;
StartupWMClass=BridgeIt
EOF

update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "Done. BridgeIt is now registered in your app launcher."
echo "Taskbar icon will appear correctly the next time you launch the app."
