"""Releases of Curvis on GitHub: versions, which release is an update, and which of its
files this computer needs.

Nothing here uses the network or Qt: it reads the JSON of GitHub's *list releases* API
(``GET /repos/{owner}/{repo}/releases``) and the release's ``SHA256SUMS.txt``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import Any

REPOSITORY = "curvis-labs/curvis"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=20"
"""The newest releases, pre-releases included (drafts are not listed without a token)."""
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
CHECKSUMS = "SHA256SUMS.txt"
"""The release asset with the SHA-256 of every other file (see .github/workflows/release.yml)."""

_VERSION = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?"
    r"(?:[-.]?(?P<pre>a|b|rc|alpha|beta)\.?(?P<pre_n>\d+))?$",
    re.IGNORECASE,
)
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2}
_RELEASE_RANK = 3
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@total_ordering
@dataclass(frozen=True)
class Version:
    """A Curvis version: ``1.2.0``, or a pre-release such as ``1.2.0rc1``.

    Tags are spelled ``v1.2.0`` or ``v1.2.0-rc1``; both read the same. A pre-release
    sorts before its release.
    """

    major: int
    minor: int
    patch: int = 0
    pre: str | None = None
    """"a", "b" or "rc" for a pre-release, None for a release."""
    pre_number: int = 0

    @classmethod
    def parse(cls, text: str) -> Version:
        match = _VERSION.match(text.strip())
        if match is None:
            raise ValueError(f"Not a version: {text!r}")
        pre = match["pre"]
        short = {"alpha": "a", "beta": "b"}.get(pre.lower(), pre.lower()) if pre else None
        return cls(
            int(match["major"]),
            int(match["minor"]),
            int(match["patch"] or 0),
            short,
            int(match["pre_n"] or 0),
        )

    @property
    def is_prerelease(self) -> bool:
        return self.pre is not None

    def _key(self) -> tuple[int, int, int, int, int]:
        rank = _RELEASE_RANK if self.pre is None else _PRE_RANK[self.pre]
        return (self.major, self.minor, self.patch, rank, self.pre_number)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if self.pre is None else f"{base}{self.pre}{self.pre_number}"


@dataclass(frozen=True)
class Asset:
    """A file of a release."""

    name: str
    url: str
    """Where to download it (``browser_download_url``)."""
    size: int
    sha256: str | None = None
    """The SHA-256 GitHub computed for the upload (its ``digest``), if it says."""


@dataclass(frozen=True)
class Release:
    """A published release of Curvis."""

    version: Version
    tag: str
    title: str
    notes: str
    """The release notes, in Markdown."""
    page: str
    """The release's web page."""
    prerelease: bool
    assets: tuple[Asset, ...]

    def asset(self, name: str) -> Asset | None:
        return next((a for a in self.assets if a.name == name), None)


def parse_releases(data: Any) -> list[Release]:
    """The releases in GitHub's JSON, newest version first. Drafts, entries whose tag is
    not a version and files not fully uploaded are left out."""
    if not isinstance(data, list):
        raise ValueError("The list of releases is not a JSON array.")
    releases: list[Release] = []
    for item in data:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        try:
            tag = str(item["tag_name"])
            version = Version.parse(tag)
        except (KeyError, ValueError):
            continue
        assets = tuple(
            asset for raw in item.get("assets") or () if (asset := _asset(raw)) is not None
        )
        releases.append(
            Release(
                version=version,
                tag=tag,
                title=str(item.get("name") or f"Curvis {version}"),
                notes=str(item.get("body") or ""),
                page=str(item.get("html_url") or f"{RELEASES_PAGE}/tag/{tag}"),
                prerelease=bool(item.get("prerelease")) or version.is_prerelease,
                assets=assets,
            )
        )
    releases.sort(key=lambda r: r.version, reverse=True)
    return releases


def _asset(raw: Any) -> Asset | None:
    if not isinstance(raw, dict) or raw.get("state", "uploaded") != "uploaded":
        return None
    try:
        name = str(raw["name"])
        url = str(raw["browser_download_url"])
        size = int(raw.get("size") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if not url.startswith("https://"):
        return None
    digest = raw.get("digest")
    sha256 = None
    if isinstance(digest, str) and digest.lower().startswith("sha256:"):
        value = digest[len("sha256:") :].lower()
        sha256 = value if _HEX64.match(value) else None
    return Asset(name, url, size, sha256)


def newest_update(releases: list[Release], current: Version) -> Release | None:
    """The newest release that is newer than ``current``, or None.

    Pre-releases count only when ``current`` is one: someone on 1.1.0rc1 is offered
    1.1.0rc2 and 1.1.0, someone on 1.0.0 only 1.1.0.
    """
    candidates = [
        r for r in releases if r.version > current and (current.is_prerelease or not r.prerelease)
    ]
    return max(candidates, key=lambda r: r.version, default=None)


def parse_checksums(text: str) -> dict[str, str]:
    """``sha256sum`` output (``<hex>  <name>``, or ``<hex> *<name>``) as name -> hex."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].lower(), parts[1].strip()
        if name.startswith("*"):
            name = name[1:]
        if _HEX64.match(digest) and name:
            sums[name] = digest
    return sums
