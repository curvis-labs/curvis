"""How this copy of Curvis is installed, which release file replaces it, and how.

``detect()`` looks at the platform and where the running program is; the result says
which asset to download and what to do with it once its SHA-256 has been checked:

- **Windows** (Inno Setup installer): run the new ``setup.exe`` silently. It installs
  over the old version (same AppId, same folder, same install mode), closes Curvis if
  it still runs and starts the new version (``/RELAUNCH=1``, see
  packaging/windows/curvis.iss).
- **macOS** (.dmg): a small shell script waits for Curvis to quit, mounts the image,
  copies the new ``Curvis.app`` next to the old one, swaps them and starts it.
- **AppImage**: the new file replaces the old one under the same name, and is started.
- **.deb**: ``pkexec apt-get install`` with the downloaded package (asks for the
  password), then Curvis starts again.
- Otherwise (no write access, no pkexec): the file is downloaded and opened for the
  user to install. Running from source only links to the release page.

Settings are in the user's QSettings and projects are the user's own files, so none of
these touch them.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from curvis.update.releases import Asset, Release


class Method(Enum):
    """What to do with the downloaded file."""

    RUN_INSTALLER = "run_installer"
    REPLACE_APP_BUNDLE = "replace_app_bundle"
    REPLACE_APPIMAGE = "replace_appimage"
    APT = "apt"
    OPEN_FILE = "open_file"
    """Download it and open it: the user installs it."""
    OPEN_PAGE = "open_page"
    """Nothing to download: show the release page."""

    @property
    def restarts(self) -> bool:
        """Curvis installs the update itself and starts again."""
        return self in (
            Method.RUN_INSTALLER,
            Method.REPLACE_APP_BUNDLE,
            Method.REPLACE_APPIMAGE,
            Method.APT,
        )


@dataclass(frozen=True)
class Target:
    """How this copy of Curvis is updated."""

    method: Method
    platform: str
    """"windows", "macos", "appimage", "deb" or "source"."""
    machine: str
    """The CPU, as the release files name it (x86_64, arm64, aarch64, amd64)."""
    location: Path | None = None
    """What is replaced: the .app bundle or the AppImage file."""

    def asset_for(self, release: Release) -> Asset | None:
        """The file of ``release`` for this computer, or None."""
        if self.platform == "source":
            return None
        for asset in release.assets:
            if _matches(asset.name, self.platform, self.machine):
                return asset
        return None


def _matches(name: str, family: str, machine: str) -> bool:
    if family == "windows":
        return name.startswith("Curvis-") and name.endswith("-setup.exe")
    if family == "macos":
        return name.startswith("Curvis-") and name.endswith(f"-macos-{machine}.dmg")
    if family == "appimage":
        return name.startswith("Curvis-") and name.endswith(f"-{machine}.AppImage")
    if family == "deb":
        return name.startswith("curvis_") and name.endswith(f"_{machine}.deb")
    return False


_DEB_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def detect(
    *,
    sys_platform: str | None = None,
    executable: str | None = None,
    frozen: bool | None = None,
    environ: Mapping[str, str] | None = None,
    machine: str | None = None,
) -> Target:
    """How the running copy of Curvis is updated. The arguments are for tests."""
    sys_platform = sys.platform if sys_platform is None else sys_platform
    exe = Path(sys.executable if executable is None else executable)
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    environ = os.environ if environ is None else environ
    machine = platform.machine() if machine is None else machine
    if not frozen:
        return Target(Method.OPEN_PAGE, "source", machine)
    if sys_platform == "win32":
        return Target(Method.RUN_INSTALLER, "windows", machine)
    if sys_platform == "darwin":
        mac_machine = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
        bundle = _app_bundle(exe)
        if bundle is not None and _writable(bundle) and _writable(bundle.parent):
            return Target(Method.REPLACE_APP_BUNDLE, "macos", mac_machine, bundle)
        return Target(Method.OPEN_FILE, "macos", mac_machine, bundle)
    if sys_platform.startswith("linux"):
        appimage = environ.get("APPIMAGE")
        if appimage:
            path = Path(appimage)
            if _writable(path) and _writable(path.parent):
                return Target(Method.REPLACE_APPIMAGE, "appimage", machine, path)
            return Target(Method.OPEN_FILE, "appimage", machine, path)
        deb_machine = _DEB_ARCH.get(machine, machine)
        if exe.parent == Path("/opt/curvis"):
            if shutil.which("pkexec") and shutil.which("apt-get"):
                return Target(Method.APT, "deb", deb_machine)
            return Target(Method.OPEN_FILE, "deb", deb_machine)
    return Target(Method.OPEN_PAGE, "source", machine)


def _app_bundle(executable: Path) -> Path | None:
    """``…/Curvis.app`` for ``…/Curvis.app/Contents/MacOS/Curvis``."""
    parents = executable.parents
    if len(parents) >= 3 and parents[2].suffix == ".app":
        return parents[2]
    return None


def _writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


# --- the commands -------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """A program to start, and whether Curvis waits for it (True) or quits (False)."""

    program: str
    arguments: tuple[str, ...]
    wait: bool


WINDOWS_ARGUMENTS = (
    "/SILENT",  # shows progress, asks nothing
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS",  # closes Curvis if it has not quit yet
    "/RELAUNCH=1",  # start Curvis when done (curvis.iss)
)

MAC_SCRIPT = r"""#!/bin/bash
# Written by Curvis to install an update: $1 the process to wait for, $2 the .dmg,
# $3 the Curvis.app to replace. Starts Curvis again, the new one if all went well.
set -u
pid="$1"; dmg="$2"; app="$3"
while kill -0 "$pid" 2>/dev/null; do sleep 0.2; done
mnt="$(mktemp -d)"
if hdiutil attach "$dmg" -nobrowse -readonly -noautoopen -mountpoint "$mnt" >/dev/null; then
  new="$app.update-$$"
  old="$app.old-$$"
  if ditto "$mnt/Curvis.app" "$new"; then
    if mv "$app" "$old"; then
      if mv "$new" "$app"; then rm -rf "$old"; else mv "$old" "$app"; fi
    fi
  fi
  rm -rf "$new"
  hdiutil detach "$mnt" -quiet || hdiutil detach "$mnt" -force -quiet
fi
rmdir "$mnt" 2>/dev/null
rm -f "$dmg" "$0"
open "$app"
"""


def install_command(target: Target, file: Path, *, pid: int, script: Path | None = None) -> Command:
    """The command that installs ``file`` (already checked) for ``target``.

    For macOS, ``script`` is where :data:`MAC_SCRIPT` was written. The AppImage is
    replaced by :func:`replace_appimage` before its command starts the new one.
    """
    method = target.method
    if method is Method.RUN_INSTALLER:
        return Command(str(file), WINDOWS_ARGUMENTS, wait=False)
    if method is Method.REPLACE_APP_BUNDLE:
        if script is None or target.location is None:
            raise ValueError("Replacing the app needs the script and the app's place.")
        return Command(
            "/bin/bash", (str(script), str(pid), str(file), str(target.location)), wait=False
        )
    if method is Method.REPLACE_APPIMAGE:
        if target.location is None:
            raise ValueError("Replacing the AppImage needs its place.")
        return Command(str(target.location), (), wait=False)
    if method is Method.APT:
        return Command("pkexec", ("apt-get", "install", "-y", str(file)), wait=True)
    raise ValueError(f"{method.value} does not install by itself.")


def restart_command(target: Target) -> Command:
    """Starts Curvis again after ``pkexec apt-get install`` has finished."""
    return Command("/usr/bin/curvis", (), wait=False)


def replace_appimage(download: Path, appimage: Path) -> None:
    """Make ``download`` (in the same folder) the new ``appimage``, executable."""
    mode = appimage.stat().st_mode if appimage.exists() else 0o755
    os.chmod(download, mode | 0o111)
    os.replace(download, appimage)


def download_folder(target: Target, cache: Path) -> Path:
    """Where the download goes: next to the AppImage (so it can replace it in one
    rename), otherwise the app's cache folder."""
    if target.method is Method.REPLACE_APPIMAGE and target.location is not None:
        return target.location.parent
    return cache / "updates"
