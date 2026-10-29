# PyInstaller spec for Curvis. Build with:
#     uv run --group build pyinstaller packaging/curvis.spec --noconfirm
# Output: dist/Curvis/ (Windows, Linux) or dist/Curvis.app (macOS).
# A one-folder build is used because it starts much faster than a one-file build.

import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 (defined by PyInstaller)
VERSION = re.search(
    r'__version__ = "([^"]+)"', (ROOT / "src" / "curvis" / "__init__.py").read_text()
).group(1)
BUNDLE_ID = "io.github.curvis-labs.curvis"

# Qt modules Curvis does not use. PyInstaller only bundles what is imported, but these
# keep a stray import (or a hook) from pulling in hundreds of MB.
EXCLUDES = [
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtDesigner",
    "PySide6.QtPdf",
    "pyarrow.flight",
    "pyarrow._flight",
    "pyarrow._s3fs",
    "pyarrow.tests",
    "yaml",
]

# Files that the hooks collect but Curvis never loads. Matched on the path in the bundle.
DROP = [
    "libarrow_flight",  # pyarrow.flight (RPC)
    "libarrow_s3",  # S3 file system
    "pyarrow/include",  # C++ headers
    "pyarrow/tests",
    "libqtvirtualkeyboardplugin",  # pulls in Qt Quick and QML
    "imageformats/libqpdf",  # pulls in Qt PDF
    "imageformats/qpdf",
    "Qt6VirtualKeyboard",
    "Qt6Quick",
    "Qt6Qml",
    "Qt6Pdf",
]


def keep(entry):
    path = entry[0].replace("\\", "/")
    return not any(part in path for part in DROP)


a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    excludes=EXCLUDES,
    noarchive=False,
)
a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Curvis" if sys.platform != "linux" else "curvis",
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    upx=False,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="Curvis",
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="Curvis.app",
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": "Curvis",
            "CFBundleDisplayName": "Curvis",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # allow dark mode
            "LSMinimumSystemVersion": "12.0",
            # .cvp project files open in Curvis.
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Curvis project",
                    "CFBundleTypeRole": "Editor",
                    "LSHandlerRank": "Owner",
                    "LSItemContentTypes": [f"{BUNDLE_ID}.project"],
                }
            ],
            "UTExportedTypeDeclarations": [
                {
                    "UTTypeIdentifier": f"{BUNDLE_ID}.project",
                    "UTTypeDescription": "Curvis project",
                    "UTTypeConformsTo": ["public.data", "public.content"],
                    "UTTypeTagSpecification": {
                        "public.filename-extension": ["cvp"],
                        "public.mime-type": ["application/x-curvis-project"],
                    },
                }
            ],
        },
    )
