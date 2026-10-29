#!/usr/bin/env bash
# Build dist/curvis_<version>_<arch>.deb from the PyInstaller output in dist/Curvis.
# Installs to /opt/curvis with a /usr/bin/curvis link, a desktop entry and the
# .cvp MIME type (dpkg triggers refresh the MIME and desktop databases).
set -euo pipefail
version="$1"
here="$(cd "$(dirname "$0")" && pwd)"
arch="$(dpkg --print-architecture)"
root="build/deb/curvis"
rm -rf "$root"
mkdir -p "$root/DEBIAN" "$root/opt" "$root/usr/bin" \
  "$root/usr/share/applications" "$root/usr/share/mime/packages" \
  "$root/usr/share/icons/hicolor/scalable/apps"
cp -a dist/Curvis "$root/opt/curvis"
ln -s /opt/curvis/curvis "$root/usr/bin/curvis"
cp "$here/curvis.desktop" "$root/usr/share/applications/curvis.desktop"
cp "$here/curvis-mime.xml" "$root/usr/share/mime/packages/curvis.xml"
cp "$here/curvis.svg" "$root/usr/share/icons/hicolor/scalable/apps/curvis.svg"
size="$(du -sk "$root" | cut -f1)"
cat > "$root/DEBIAN/control" <<CONTROL
Package: curvis
Version: ${version}
Section: science
Priority: optional
Architecture: ${arch}
Maintainer: Curvis developers <noreply@github.com>
Installed-Size: ${size}
Depends: libegl1, libgl1, libxkbcommon0, libxkbcommon-x11-0, libfontconfig1, libdbus-1-3, libglib2.0-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-shape0, libxcb-xkb1
Homepage: https://github.com/curvis-labs/curvis
Description: Scientific data analysis and plotting
 Curvis analyzes and plots scientific data: data tables, statistical tests,
 curve fits, survival analysis and publication-ready graphs.
CONTROL
dpkg-deb --root-owner-group --build "$root" "dist/curvis_${version}_${arch}.deb"
