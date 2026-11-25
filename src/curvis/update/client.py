"""Ask GitHub for the releases and download an update, with Qt's networking.

Both run on the event loop without blocking it: the replies arrive as signals, and the
download is hashed piece by piece as it comes in, so the window stays responsive. Only
a plain HTTPS request goes out, with ``User-Agent: Curvis/<version>``: no data about
the user or their projects.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import IO

from PySide6.QtCore import QByteArray, QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from curvis import __version__
from curvis.update.releases import (
    CHECKSUMS,
    RELEASES_API,
    Asset,
    Release,
    Version,
    newest_update,
    parse_checksums,
    parse_releases,
)

TIMEOUT_MS = 30_000
"""A request with no progress for this long fails (a download keeps going while data
arrives)."""
MAX_LIST_BYTES = 5_000_000
"""The list of releases and SHA256SUMS.txt are small; more is not what we asked for."""

OFFLINE = "Could not reach GitHub. Check your internet connection and try again."
NO_CHECKSUMS = (
    "This release has no checksums, so the download cannot be checked. "
    "Download it from the release page."
)
BAD_CHECKSUM = "The download did not match its checksum and was deleted. Try again."
CANNOT_WRITE = "Could not save the download: {error}"


def _request(url: str, accept: str = "application/octet-stream") -> QNetworkRequest:
    request = QNetworkRequest(QUrl(url))
    request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f"Curvis/{__version__}")
    request.setRawHeader(QByteArray(b"Accept"), QByteArray(accept.encode()))
    # GitHub sends downloads from another https host; never follow a redirect to http.
    request.setAttribute(
        QNetworkRequest.Attribute.RedirectPolicyAttribute,
        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
    )
    request.setTransferTimeout(TIMEOUT_MS)
    return request


def _failed(reply: QNetworkReply) -> bool:
    return reply.error() != QNetworkReply.NetworkError.NoError


class UpdateChecker(QObject):
    """Asks GitHub for the releases; says whether one is newer than ``current``."""

    found = Signal(object)
    """A :class:`Release` newer than this version."""
    up_to_date = Signal()
    failed = Signal(str)

    def __init__(
        self,
        current: str = __version__,
        parent: QObject | None = None,
        *,
        manager: QNetworkAccessManager | None = None,
        url: str = RELEASES_API,
    ) -> None:
        super().__init__(parent)
        self.current = Version.parse(current)
        self._manager = manager if manager is not None else QNetworkAccessManager(self)
        self._url = url
        self._reply: QNetworkReply | None = None

    @property
    def busy(self) -> bool:
        return self._reply is not None

    def check(self) -> None:
        """Start a check (does nothing while one is running)."""
        if self._reply is not None:
            return
        reply = self._manager.get(_request(self._url, "application/vnd.github+json"))
        self._reply = reply
        reply.finished.connect(lambda: self._finished(reply))

    def _finished(self, reply: QNetworkReply) -> None:
        self._reply = None
        reply.deleteLater()
        if _failed(reply):
            self.failed.emit(OFFLINE)
            return
        body = bytes(reply.readAll().data())
        try:
            if len(body) > MAX_LIST_BYTES:
                raise ValueError("too long")
            releases = parse_releases(json.loads(body))
        except ValueError:
            self.failed.emit("GitHub sent a list of releases Curvis cannot read.")
            return
        update = newest_update(releases, self.current)
        if update is None:
            self.up_to_date.emit()
        else:
            self.found.emit(update)


class Downloader(QObject):
    """Downloads one file of a release into ``folder`` and checks its SHA-256 against
    the release's SHA256SUMS.txt (and GitHub's own digest, if given).

    ``finished`` sends the path of the checked file; a file that does not match is
    deleted and ``failed`` says so.
    """

    progress = Signal(int, int)
    """Bytes received, bytes in all (0 when not known)."""
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        release: Release,
        asset: Asset,
        folder: Path,
        parent: QObject | None = None,
        *,
        manager: QNetworkAccessManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.release = release
        self.asset = asset
        self.folder = folder
        self.path = folder / asset.name
        self._part = folder / f".{asset.name}.part"
        self._manager = manager if manager is not None else QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._file: IO[bytes] | None = None
        self._hash = hashlib.sha256()
        self._expected: str | None = None

    @property
    def busy(self) -> bool:
        return self._reply is not None

    def start(self) -> None:
        checksums = self.release.asset(CHECKSUMS)
        if checksums is None:
            self.failed.emit(NO_CHECKSUMS)
            return
        reply = self._manager.get(_request(checksums.url))
        self._reply = reply
        reply.finished.connect(lambda: self._checksums_finished(reply))

    def cancel(self) -> None:
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        self._discard()

    def _checksums_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return  # cancelled
        self._reply = None
        reply.deleteLater()
        if _failed(reply):
            self.failed.emit(OFFLINE)
            return
        text = bytes(reply.readAll().data()[:MAX_LIST_BYTES]).decode("utf-8", "replace")
        expected = parse_checksums(text).get(self.asset.name)
        if expected is None:
            self.failed.emit(NO_CHECKSUMS)
            return
        if self.asset.sha256 is not None and self.asset.sha256 != expected:
            self.failed.emit(BAD_CHECKSUM)
            return
        self._expected = expected
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self._remove_leftovers()
            self._file = self._part.open("wb")
        except OSError as error:
            self.failed.emit(CANNOT_WRITE.format(error=error.strerror or error))
            return
        self._hash = hashlib.sha256()
        download = self._manager.get(_request(self.asset.url))
        self._reply = download
        download.readyRead.connect(lambda: self._read(download))
        download.downloadProgress.connect(self._progress)
        download.finished.connect(lambda: self._download_finished(download))

    def _remove_leftovers(self) -> None:
        """Earlier updates' files in the updates folder (not next to an AppImage)."""
        if self.folder.name != "updates":
            self._part.unlink(missing_ok=True)
            return
        for old in self.folder.iterdir():
            if old.is_file():
                old.unlink(missing_ok=True)

    def _progress(self, received: int, total: int) -> None:
        self.progress.emit(int(received), int(max(total, self.asset.size, 0)))

    def _read(self, reply: QNetworkReply) -> None:
        if reply is not self._reply or self._file is None:
            return
        chunk = bytes(reply.readAll().data())
        self._hash.update(chunk)
        try:
            self._file.write(chunk)
        except OSError as error:
            self.cancel()
            self.failed.emit(CANNOT_WRITE.format(error=error.strerror or error))

    def _download_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return  # cancelled
        self._read(reply)
        self._reply = None
        reply.deleteLater()
        if _failed(reply):
            self._discard()
            self.failed.emit(OFFLINE)
            return
        assert self._file is not None
        self._file.close()
        self._file = None
        if self._hash.hexdigest() != self._expected:
            self._discard()
            self.failed.emit(BAD_CHECKSUM)
            return
        try:
            self._part.replace(self.path)
        except OSError as error:
            self._discard()
            self.failed.emit(CANNOT_WRITE.format(error=error.strerror or error))
            return
        self.finished.emit(self.path)

    def _discard(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        self._part.unlink(missing_ok=True)
