"""Reading GitHub releases, picking an update and the file for this computer, and the
commands that install it (curvis.update.releases, curvis.update.install)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from curvis.update.install import (
    MAC_SCRIPT,
    WINDOWS_ARGUMENTS,
    Method,
    Target,
    detect,
    download_folder,
    install_command,
    replace_appimage,
)
from curvis.update.releases import (
    Release,
    Version,
    newest_update,
    parse_checksums,
    parse_releases,
)

V100_ASSETS = [
    "Curvis-1.0.0-macos-arm64.dmg",
    "Curvis-1.0.0-setup.exe",
    "Curvis-1.0.0-x86_64.AppImage",
    "curvis-validation-report.md",
    "curvis-validation-report.pdf",
    "curvis_1.0.0_amd64.deb",
    "SHA256SUMS.txt",
]
"""The files of the v1.0.0 release, as published."""


def release_json(
    tag: str, *, prerelease: bool = False, draft: bool = False, names: list[str] | None = None
) -> dict[str, Any]:
    version = tag.lstrip("v")
    names = names or [n.replace("1.0.0", version) for n in V100_ASSETS]
    return {
        "tag_name": tag,
        "name": f"Curvis {version}",
        "body": f"Notes for {version}",
        "html_url": f"https://github.com/curvis-labs/curvis/releases/tag/{tag}",
        "draft": draft,
        "prerelease": prerelease,
        "assets": [
            {
                "name": n,
                "state": "uploaded",
                "size": 1000,
                "browser_download_url": f"https://github.com/curvis-labs/curvis/releases/download/{tag}/{n}",
                "digest": "sha256:" + hashlib.sha256(n.encode()).hexdigest(),
            }
            for n in names
        ],
    }


# --- versions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.0.0", Version(1, 0, 0)),
        ("v1.2.3", Version(1, 2, 3)),
        ("1.2", Version(1, 2, 0)),
        ("0.1.0b1", Version(0, 1, 0, "b", 1)),
        ("v1.1.0-rc2", Version(1, 1, 0, "rc", 2)),
        ("v1.1.0-beta.3", Version(1, 1, 0, "b", 3)),
    ],
)
def test_parse_version(text: str, expected: Version) -> None:
    assert Version.parse(text) == expected


@pytest.mark.parametrize("text", ["", "latest", "v1", "1.0.0-final", "1.0.0.0"])
def test_not_a_version(text: str) -> None:
    with pytest.raises(ValueError):
        Version.parse(text)


def test_version_order() -> None:
    order = ["0.1.0b1", "1.0.0", "1.0.1", "1.1.0a1", "1.1.0b2", "1.1.0rc1", "1.1.0", "1.10.0"]
    versions = [Version.parse(v) for v in order]
    assert versions == sorted(versions)
    assert Version.parse("v1.1.0-rc1") == Version.parse("1.1.0rc1")
    assert str(Version.parse("v1.1.0-rc1")) == "1.1.0rc1"


# --- releases -------------------------------------------------------------------------


def test_parse_releases_newest_first_without_drafts_or_odd_tags() -> None:
    data = [
        release_json("v1.0.0"),
        release_json("v1.2.0", draft=True),
        release_json("v1.1.0"),
        {"tag_name": "nightly", "assets": []},
        "not a release",
    ]
    releases = parse_releases(data)
    assert [str(r.version) for r in releases] == ["1.1.0", "1.0.0"]
    first = releases[0]
    assert first.notes == "Notes for 1.1.0"
    assert first.page.endswith("/tag/v1.1.0")
    exe = first.asset("Curvis-1.1.0-setup.exe")
    assert exe is not None and exe.sha256 is not None and exe.url.startswith("https://")


def test_parse_releases_skips_unfinished_and_insecure_files() -> None:
    data = release_json("v1.1.0")
    data["assets"][0]["state"] = "starter"
    data["assets"][1]["browser_download_url"] = "http://example.org/x"
    (release,) = parse_releases([data])
    assert len(release.assets) == len(V100_ASSETS) - 2


def test_parse_releases_needs_a_list() -> None:
    with pytest.raises(ValueError):
        parse_releases({"message": "API rate limit exceeded"})


def releases(*tags: str) -> list[Release]:
    return parse_releases(
        [release_json(t, prerelease="-" in t or "rc" in t or "b" in t) for t in tags]
    )


def test_newest_update_skips_pre_releases_for_a_release() -> None:
    found = newest_update(releases("v1.0.0", "v1.1.0", "v1.2.0-rc1"), Version.parse("1.0.0"))
    assert found is not None and str(found.version) == "1.1.0"


def test_newest_update_offers_pre_releases_to_a_pre_release() -> None:
    found = newest_update(releases("v1.1.0-rc1", "v1.1.0-rc2"), Version.parse("1.1.0rc1"))
    assert found is not None and str(found.version) == "1.1.0rc2"


def test_no_update_when_current_is_newest() -> None:
    assert newest_update(releases("v0.1.0b1", "v1.0.0"), Version.parse("1.0.0")) is None


def test_parse_checksums() -> None:
    a, b = "a" * 64, "B" * 64
    text = f"{a}  Curvis-1.0.0-setup.exe\n{b} *curvis_1.0.0_amd64.deb\n\nnot a line\nxyz  bad\n"
    assert parse_checksums(text) == {
        "Curvis-1.0.0-setup.exe": a,
        "curvis_1.0.0_amd64.deb": "b" * 64,
    }


# --- which file -------------------------------------------------------------------------

V100 = parse_releases([release_json("v1.0.0")])[0]


@pytest.mark.parametrize(
    ("target", "name"),
    [
        (Target(Method.RUN_INSTALLER, "windows", "AMD64"), "Curvis-1.0.0-setup.exe"),
        (Target(Method.REPLACE_APP_BUNDLE, "macos", "arm64"), "Curvis-1.0.0-macos-arm64.dmg"),
        (Target(Method.REPLACE_APPIMAGE, "appimage", "x86_64"), "Curvis-1.0.0-x86_64.AppImage"),
        (Target(Method.APT, "deb", "amd64"), "curvis_1.0.0_amd64.deb"),
        (Target(Method.OPEN_FILE, "deb", "amd64"), "curvis_1.0.0_amd64.deb"),
    ],
)
def test_asset_for_this_computer(target: Target, name: str) -> None:
    asset = target.asset_for(V100)
    assert asset is not None and asset.name == name


@pytest.mark.parametrize(
    "target",
    [
        Target(Method.REPLACE_APP_BUNDLE, "macos", "x86_64"),  # 1.0 is Apple silicon only
        Target(Method.APT, "deb", "arm64"),
        Target(Method.OPEN_PAGE, "source", "x86_64"),
    ],
)
def test_no_asset_for_this_computer(target: Target) -> None:
    assert target.asset_for(V100) is None


# --- how this copy is installed ---------------------------------------------------------


def test_detect_from_source() -> None:
    target = detect(sys_platform="linux", executable="/usr/bin/python3", frozen=False, environ={})
    assert target.method is Method.OPEN_PAGE


def test_detect_windows() -> None:
    target = detect(
        sys_platform="win32",
        executable=r"C:\Users\me\AppData\Local\Programs\Curvis\Curvis.exe",
        frozen=True,
        environ={},
        machine="AMD64",
    )
    assert (target.method, target.platform) == (Method.RUN_INSTALLER, "windows")


def test_detect_macos(tmp_path: Path) -> None:
    app = tmp_path / "Curvis.app"
    exe = app / "Contents" / "MacOS" / "Curvis"
    exe.parent.mkdir(parents=True)
    exe.touch()
    target = detect(
        sys_platform="darwin", executable=str(exe), frozen=True, environ={}, machine="arm64"
    )
    assert target == Target(Method.REPLACE_APP_BUNDLE, "macos", "arm64", app)


def test_detect_macos_read_only_opens_the_file(tmp_path: Path) -> None:
    exe = Path("/nonexistent/Curvis.app/Contents/MacOS/Curvis")
    target = detect(sys_platform="darwin", executable=str(exe), frozen=True, environ={})
    assert target.method is Method.OPEN_FILE


def test_detect_appimage(tmp_path: Path) -> None:
    image = tmp_path / "Curvis-1.0.0-x86_64.AppImage"
    image.touch()
    target = detect(
        sys_platform="linux",
        executable="/tmp/.mount_Curvis/usr/curvis/curvis",
        frozen=True,
        environ={"APPIMAGE": str(image)},
        machine="x86_64",
    )
    assert target == Target(Method.REPLACE_APPIMAGE, "appimage", "x86_64", image)


def test_detect_deb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    target = detect(
        sys_platform="linux",
        executable="/opt/curvis/curvis",
        frozen=True,
        environ={},
        machine="x86_64",
    )
    assert target == Target(Method.APT, "deb", "amd64")
    monkeypatch.setattr("shutil.which", lambda name: None)
    target = detect(sys_platform="linux", executable="/opt/curvis/curvis", frozen=True, environ={})
    assert target.method is Method.OPEN_FILE


# --- commands ------------------------------------------------------------------------


def test_windows_runs_the_installer_silently_and_relaunches() -> None:
    target = Target(Method.RUN_INSTALLER, "windows", "AMD64")
    command = install_command(target, Path("C:/cache/Curvis-1.1.0-setup.exe"), pid=1)
    assert command.program.endswith("Curvis-1.1.0-setup.exe")
    assert command.arguments == WINDOWS_ARGUMENTS
    assert "/SILENT" in command.arguments and "/RELAUNCH=1" in command.arguments
    assert not command.wait


def test_windows_installer_reads_relaunch() -> None:
    iss = (Path(__file__).parents[2] / "packaging" / "windows" / "curvis.iss").read_text()
    assert "{param:RELAUNCH|0}" in iss
    # The old version's program files go before the new ones are copied.
    assert 'Name: "{app}\\_internal"' in iss


def test_mac_runs_the_script() -> None:
    app = Path("/Applications/Curvis.app")
    target = Target(Method.REPLACE_APP_BUNDLE, "macos", "arm64", app)
    command = install_command(target, Path("/c/u.dmg"), pid=42, script=Path("/c/s.sh"))
    assert command.program == "/bin/bash"
    assert command.arguments == ("/c/s.sh", "42", "/c/u.dmg", str(app))
    assert 'open "$app"' in MAC_SCRIPT and "hdiutil attach" in MAC_SCRIPT


def test_deb_waits_for_apt() -> None:
    command = install_command(Target(Method.APT, "deb", "amd64"), Path("/c/x.deb"), pid=1)
    assert (command.program, command.arguments, command.wait) == (
        "pkexec",
        ("apt-get", "install", "-y", "/c/x.deb"),
        True,
    )


def test_open_file_has_no_command() -> None:
    with pytest.raises(ValueError):
        install_command(Target(Method.OPEN_FILE, "deb", "amd64"), Path("/c/x.deb"), pid=1)


def test_replace_appimage(tmp_path: Path) -> None:
    image = tmp_path / "Curvis-1.0.0-x86_64.AppImage"
    image.write_bytes(b"old")
    image.chmod(0o755)
    target = Target(Method.REPLACE_APPIMAGE, "appimage", "x86_64", image)
    folder = download_folder(target, tmp_path / "cache")
    assert folder == tmp_path  # same folder: one rename replaces it
    new = folder / "Curvis-1.1.0-x86_64.AppImage"
    new.write_bytes(b"new")
    replace_appimage(new, image)
    assert image.read_bytes() == b"new" and not new.exists()
    assert image.stat().st_mode & 0o111
    other = Target(Method.RUN_INSTALLER, "windows", "AMD64")
    assert download_folder(other, tmp_path / "cache") == tmp_path / "cache" / "updates"
