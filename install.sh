#!/usr/bin/env bash
# BridgeIt installer for Linux
# Works from the source repo (pip install) or from an extracted binary release.
# Usage:  ./install.sh           — install
#         ./install.sh --uninstall — remove

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; RESET=''
fi

info()    { echo -e "${CYAN}▸${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
die()     { echo -e "${RED}✗${RESET} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/BridgeIt"
BIN_DIR="$HOME/.local/bin"
ICON_BASE="$HOME/.local/share/icons/hicolor"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/BridgeIt.desktop"

# ── Uninstall ─────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
    header "Uninstalling BridgeIt"
    rm -f  "$DESKTOP_FILE"                                   && success "Removed .desktop file"
    rm -f  "$BIN_DIR/bridgeit"                               && success "Removed launcher"
    rm -rf "$APP_DIR"                                        && success "Removed app directory"
    for sz in 16 32 48 64 128 256 512; do
        rm -f "$ICON_BASE/${sz}x${sz}/apps/BridgeIt.png"
    done
    success "Removed icons"
    command -v gtk-update-icon-cache &>/dev/null && \
        gtk-update-icon-cache -f -t "$ICON_BASE" 2>/dev/null || true
    command -v update-desktop-database &>/dev/null && \
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    # If installed via pip, offer to remove that too
    if command -v pip &>/dev/null && pip show bridgeit &>/dev/null 2>&1; then
        info "Removing pip package…"
        pip uninstall -y bridgeit
        success "pip package removed"
    fi
    echo -e "\n${GREEN}BridgeIt has been uninstalled.${RESET}"
    exit 0
fi

# ── Detect install mode ───────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/BridgeIt" ] && [ -x "$SCRIPT_DIR/BridgeIt" ]; then
    MODE="binary"
elif [ -f "$SCRIPT_DIR/pyproject.toml" ] && [ -d "$SCRIPT_DIR/bridgeit" ]; then
    MODE="source"
else
    die "Could not detect install mode. Run this script from the BridgeIt source directory or extracted release folder."
fi

# ── Find assets ───────────────────────────────────────────────────────────────
# PyInstaller 6+ puts data files under _internal/; earlier versions put them directly
if [ "$MODE" = "binary" ]; then
    if   [ -d "$SCRIPT_DIR/_internal/bridgeit/assets" ]; then
        ASSET_DIR="$SCRIPT_DIR/_internal/bridgeit/assets"
    elif [ -d "$SCRIPT_DIR/bridgeit/assets" ]; then
        ASSET_DIR="$SCRIPT_DIR/bridgeit/assets"
    else
        ASSET_DIR=""
        warn "Could not find assets — icon may not appear in taskbar"
    fi
else
    ASSET_DIR="$SCRIPT_DIR/bridgeit/assets"
fi

# ── Banner ────────────────────────────────────────────────────────────────────
header "Installing BridgeIt  (mode: $MODE)"

# ── Step 1: install the app ───────────────────────────────────────────────────
header "Step 1/4  —  Installing application"

if [ "$MODE" = "source" ]; then
    # Prefer pip3, fall back to pip
    PIP=""
    for cmd in pip3 pip; do
        if command -v "$cmd" &>/dev/null; then PIP="$cmd"; break; fi
    done
    [ -n "$PIP" ] || die "pip not found. Install Python 3 and pip first:\n  sudo apt install python3-pip"

    info "Running: $PIP install --user \"$SCRIPT_DIR\""
    "$PIP" install --user "$SCRIPT_DIR"
    EXEC_CMD="bridgeit"
    success "Package installed via pip"

else
    # Binary mode: copy the whole release folder to ~/.local/share/BridgeIt/
    info "Copying release to $APP_DIR …"
    rm -rf "$APP_DIR"
    cp -r "$SCRIPT_DIR" "$APP_DIR"
    chmod +x "$APP_DIR/BridgeIt"

    # Create a thin wrapper in ~/.local/bin so `bridgeit` works from a terminal
    mkdir -p "$BIN_DIR"
    cat > "$BIN_DIR/bridgeit" <<WRAPPER
#!/usr/bin/env bash
exec "$APP_DIR/BridgeIt" "\$@"
WRAPPER
    chmod +x "$BIN_DIR/bridgeit"
    EXEC_CMD="$APP_DIR/BridgeIt"
    success "Binary installed to $APP_DIR"
fi

# ── Step 2: install icons ─────────────────────────────────────────────────────
header "Step 2/4  —  Installing icons"

if [ -n "$ASSET_DIR" ]; then
    for sz in 16 32 48 64 128 256 512; do
        icon_src="$ASSET_DIR/icon_${sz}.png"
        if [ -f "$icon_src" ]; then
            icon_dst="$ICON_BASE/${sz}x${sz}/apps"
            mkdir -p "$icon_dst"
            cp "$icon_src" "$icon_dst/BridgeIt.png"
        fi
    done
    success "Icons installed"
else
    warn "Skipped icon install — asset directory not found"
fi

# ── Step 3: install .desktop file ────────────────────────────────────────────
header "Step 3/4  —  Installing desktop entry"

mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.1
Name=BridgeIt
GenericName=Laser Cutting SVG Converter
Comment=Convert images to fabrication-ready SVGs with automatic bridge generation
Exec=$EXEC_CMD
Icon=BridgeIt
Categories=Graphics;VectorGraphics;2DGraphics;
Keywords=laser;cutting;svg;vector;bridge;fabrication;
StartupWMClass=BridgeIt
MimeType=image/png;image/jpeg;image/webp;image/bmp;
DESKTOP

success ".desktop file written to $DESKTOP_FILE"

# ── Step 4: refresh caches ────────────────────────────────────────────────────
header "Step 4/4  —  Refreshing caches"

if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "$ICON_BASE" 2>/dev/null && success "Icon cache updated"
else
    warn "gtk-update-icon-cache not found — you may need to log out and back in for the icon to appear"
fi

if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null && success "Desktop database updated"
fi

# ── PATH check ────────────────────────────────────────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    warn "$BIN_DIR is not in your PATH."
    warn "Add this line to your ~/.bashrc or ~/.zshrc and restart your terminal:"
    echo -e "    ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}BridgeIt installed successfully.${RESET}"
echo -e "Launch it from your app menu, or run ${BOLD}bridgeit${RESET} in a terminal."
echo -e "To uninstall: ${BOLD}./install.sh --uninstall${RESET}"
