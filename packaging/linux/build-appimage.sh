#!/usr/bin/env bash
# Build dist/Curvis-<version>-<arch>.AppImage from dist/Curvis with appimagetool.
# An AppImage cannot register file types by itself; the .deb does that.
set -euo pipefail
version="$1"
appimagetool="$2"
here="$(cd "$(dirname "$0")" && pwd)"
appdir="build/Curvis.AppDir"
rm -rf "$appdir"
mkdir -p "$appdir/usr"
cp -a dist/Curvis "$appdir/usr/curvis"
cp "$here/org.curvis.Curvis.desktop" "$appdir/"
cp build/icons/org.curvis.Curvis.svg "$appdir/"
cp build/icons/hicolor/256x256/apps/org.curvis.Curvis.png "$appdir/.DirIcon"
cat > "$appdir/AppRun" <<'APPRUN'
#!/bin/sh
here="$(dirname "$(readlink -f "$0")")"
exec "$here/usr/curvis/curvis" "$@"
APPRUN
chmod +x "$appdir/AppRun"
ARCH="$(uname -m)" "$appimagetool" --no-appstream "$appdir" "dist/Curvis-${version}-$(uname -m).AppImage"
