#!/usr/bin/env bash
# Build a standalone app with PyInstaller: dist/Tamis.app on macOS,
# dist/Tamis/ elsewhere. Run from an environment with `pip install -e ".[build]"`.
#
# On macOS the bundle gets the version of pyproject.toml and is signed ad hoc,
# which is enough on this Mac. scripts/package_macos.sh wraps it in a DMG for a
# release (and signs it with a Developer ID and notarizes it, given one).
set -euo pipefail
cd "$(dirname "$0")/.."

version=$(python -c "import tamis; print(tamis.__version__)")
if [[ "$version" == "unknown" ]]; then
    echo "Tamis is not installed in this environment: run pip install -e \".[build]\" first" >&2
    exit 1
fi

icon_args=()
if [[ "$(uname)" == "Darwin" ]]; then
    python scripts/make_icns.py build/Tamis.icns
    icon_args=(--icon build/Tamis.icns)
fi

# --copy-metadata: tamis.__version__ is read from the package's metadata.
python -m PyInstaller \
    --name Tamis \
    --windowed \
    --noconfirm \
    --clean \
    --osx-bundle-identifier io.github.ozraam.tamis \
    --collect-binaries rawpy \
    --copy-metadata tamis \
    --exclude-module PyQt6.QtDBus \
    --hidden-import PyQt6.QtNetwork \
    --add-data tamis/resources:tamis/resources \
    ${icon_args[@]+"${icon_args[@]}"} \
    Tamis.py

# Qt plugins Tamis doesn't use, and the frameworks only they need: PDFs as images
# and TUIO touch over the network. (QtNetwork stays: it asks GitHub for new versions.)
find dist/Tamis.app dist/Tamis -depth \( -name "libqpdf.*" -o -name "libqtuiotouchplugin.*" \
    -o -name "QtPdf.framework" -o -name "*Qt6Pdf*" \) \
    -exec rm -rf {} + 2>/dev/null || true

if [[ "$(uname)" == "Darwin" ]]; then
    plist=dist/Tamis.app/Contents/Info.plist
    plutil -replace CFBundleShortVersionString -string "$version" "$plist"
    plutil -replace CFBundleVersion -string "$version" "$plist"
    # Changing Info.plist breaks PyInstaller's ad hoc signature: sign again.
    codesign --force --deep --sign - dist/Tamis.app
    echo "Built dist/Tamis.app $version"
fi
