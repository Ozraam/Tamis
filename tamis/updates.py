"""Telling the user when a newer version of Tamis has been released on GitHub.

At most once a day (and when asked from About), the latest release is asked
of GitHub's API in the background. Nothing but the usual headers is sent, with a
generic `User-Agent: Tamis/<version>`; being offline or GitHub failing is not
an error worth showing at launch. Drafts and pre-releases are never offered.
"""
import json
import logging
import re
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QUrl, pyqtSignal

log = logging.getLogger(__name__)

REPOSITORY = "Ozraam/Tamis"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECK_INTERVAL = 24 * 3600  # seconds between automatic checks
TIMEOUT_MS = 10_000

_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class Release:
    version: str  # "0.2.0"
    url: str  # its page on GitHub


@dataclass(frozen=True)
class CheckResult:
    newer: Release | None = None  # a release newer than this version
    latest: str | None = None  # the latest version found, newer or not
    error: str | None = None  # why it couldn't be known


def parse_version(text):
    """(0, 10, 0) for "0.10.0" or "v0.10.0"; None for anything else, pre-releases ("0.2.0b1", "0.2.0-rc1") included."""
    if not isinstance(text, str):
        return None
    match = _VERSION.fullmatch(text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate, current):
    """Whether version `candidate` comes after `current`, numerically (0.10.0 > 0.9.0, 0.2 == 0.2.0)."""
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def latest_release(data):
    """The Release in GitHub's answer for the latest release, None if it isn't one that can be offered."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    version = parse_version(tag)
    if version is None:
        return None
    url = data.get("html_url")
    if not isinstance(url, str) or not url.startswith("https://github.com/"):
        url = RELEASES_PAGE
    return Release(".".join(map(str, version)), url)


def result_of(body, current):
    """The CheckResult for the bytes GitHub answered (None when the request failed)."""
    if body is None:
        return CheckResult(error="GitHub could not be reached.")
    try:
        release = latest_release(json.loads(body))
    except (ValueError, UnicodeDecodeError):
        release = None
    if release is None:
        return CheckResult(error="GitHub's answer could not be understood.")
    return CheckResult(release if is_newer(release.version, current) else None, release.version)


class UpdateChecker(QObject):
    """Asks GitHub for the latest release; `checked(CheckResult)` tells what it found."""

    checked = pyqtSignal(object)

    def __init__(self, current_version, parent=None):
        super().__init__(parent)
        self.current_version = current_version
        self.busy = False
        self._manager = None

    def can_check(self):
        """Whether this version can be compared: not in a checkout that was never installed."""
        return parse_version(self.current_version) is not None

    def check(self):
        if self.busy:
            return
        if not self.can_check():
            self.checked.emit(CheckResult(error="This copy of Tamis has no version to compare."))
            return
        self.busy = True
        self._get(LATEST_URL, self._on_answer)

    def _on_answer(self, body):
        self.busy = False
        self.checked.emit(result_of(body, self.current_version))

    def _get(self, url, done):
        """GET `url` in the background, then `done(body bytes, or None on any failure)`. Replaced in tests."""
        # Imported here: QtNetwork is only needed once a check is made.
        from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

        if self._manager is None:
            self._manager = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", f"Tamis/{self.current_version}".encode())
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setTransferTimeout(TIMEOUT_MS)
        reply = self._manager.get(request)

        def finished():
            try:
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    body = bytes(reply.readAll())
                else:
                    log.info("Checking for updates failed: %s", reply.errorString())
                    body = None
            finally:
                reply.deleteLater()
            done(body)

        reply.finished.connect(finished)
