"""Install requests and local desktop integration, without executing imported files."""

import hashlib
import os
import re
import shutil
import stat
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from gi.repository import Gio, GLib

from housekeeper.appearance import (
    HAD_ICON,
    ORIGINAL,
    data_home,
    load_icon_image,
    staged_write,
    store_icon_image,
)
from housekeeper.appimage_format import appimage_format
from housekeeper.i18n import _
from housekeeper.models import ManagementError, OperationCancelled, OperationResult, Outcome
from housekeeper.web_identity import (
    checked_http_url,
    launch_args,
    legacy_identifier,
    window_identity,
)
from housekeeper.website_icons import website_icon


@dataclass(frozen=True)
class InstallCandidate:
    source: str
    name: str
    target: str
    description: str = ""
    context: str = ""
    remote: str = ""
    # Display name and cached icon file from the AppStream catalogue, if it lists the app.
    title: str = ""
    icon: str = ""


@dataclass(frozen=True)
class InstallRequest:
    source: str
    value: str = ""
    name: str = ""
    browser: str = "chrome"
    candidate: InstallCandidate | None = None
    icon: str = ""


def search_term(query):
    """The longest word of a phrase, for searches that can only match names."""
    return max(query.split(), key=len)


def ranked_matches(query, candidates, limit=12):
    """Prefer exact names and substrings; tolerate small spelling mistakes."""
    query = query.strip().casefold()
    ranked = []
    for candidate in dict.fromkeys(candidates):
        name = candidate.name.casefold()
        short = name.rsplit(".", 1)[-1]
        if query in {name, short}:
            score = 4
        elif name.startswith(query) or short.startswith(query):
            score = 3
        elif query in name:
            score = 2
        else:
            score = max(SequenceMatcher(None, query, n).ratio() for n in (name, short))
            if score < 0.6:
                continue
        ranked.append(
            (-score, name, candidate.context, candidate.remote, candidate.target, candidate)
        )
    return tuple(row[-1] for row in sorted(ranked, key=lambda row: row[:-1])[:limit])


def normalize_url(value):
    value = value.strip()
    if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ManagementError(_("Enter a valid website URL."))
    # Only a leading scheme counts; "://" may legitimately appear in a path or query.
    if not re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "https://" + value
    try:
        url, _host = checked_http_url(value)
    except ValueError:
        raise ManagementError(
            _("Use an http or https URL without a username or password.")
        ) from None
    return urlunsplit((url.scheme, url.netloc, url.path or "/", url.query, url.fragment))


def appimage_name(path):
    name = re.sub(r"(?i)\.appimage$", "", Path(path).name)
    name = re.sub(r"(?i)[-_](x86_64|aarch64|amd64|i686|arm64)$", "", name)
    return name.replace("_", " ")


def desktop_exec(argv):
    # Exec has its own quoting rules, followed by KeyFile string escaping. Never
    # shell-quote this field; percent signs are desktop field codes, even in quotes.
    return " ".join(
        '"' + re.sub(r'([\\"`$])', r"\\\1", arg.replace("%", "%%")) + '"' for arg in argv
    )


def create_launcher(
    identifier, name, argv, icon, *, custom_icon="", desktop_id="", wm_class="", created=None
):
    name = name.strip()
    if not name or any(ord(c) < 32 for c in name):
        raise ManagementError(_("Enter an application name on a single line."))
    keyfile = GLib.KeyFile()
    for key, value in {
        "Type": "Application",
        "Name": name,
        "Exec": desktop_exec(argv),
        "Icon": icon,
        "Terminal": "false",
        "X-Housekeeper-Created": "true",
    }.items():
        keyfile.set_string("Desktop Entry", key, value)
    if wm_class:
        keyfile.set_string("Desktop Entry", "StartupWMClass", wm_class)
    if custom_icon:
        icon_path = store_icon_image(custom_icon, created)
        keyfile.set_string("Desktop Entry", HAD_ICON, "true")
        keyfile.set_string("Desktop Entry", ORIGINAL, icon)
        keyfile.set_string("Desktop Entry", "Icon", str(icon_path))
    directory = data_home() / "applications"
    target = directory / ((desktop_id or "housekeeper-" + identifier) + ".desktop")

    def publish(staged):
        # Publish only complete files, without replacing any existing launcher.
        try:
            os.link(staged, target)
        except FileExistsError:
            raise ManagementError(_("This application already has a launcher.")) from None
        return target

    # Desktop entries are UTF-8 by specification, whatever the session locale is.
    data = keyfile.to_data()[0].encode()
    return staged_write(directory, lambda stream: stream.write(data), publish)


class Installer:
    def __init__(self):
        self.cancel = Gio.Cancellable()

    def request_cancel(self):
        self.cancel.cancel()

    def check_cancelled(self):
        if self.cancel.is_cancelled():
            raise OperationCancelled(_("Installation was cancelled."))

    def search(self, source, query):
        from housekeeper.providers.install import search_flatpak, search_packages, search_snaps

        self.check_cancelled()
        # AppStream matches every word of a phrase such as "video editor".
        query = " ".join(query.split())
        if not 2 <= len(query) <= 128:
            raise ManagementError(_("Enter at least two characters of an app name."))
        if not all(re.fullmatch(r"[\w.+:-]+", word) for word in query.split()):
            raise ManagementError(
                _("Search with letters, numbers and the characters . + : - only.")
            )
        if source == "flatpak":
            return search_flatpak(self, query)
        if source == "snap":
            return search_snaps(self, query)
        return search_packages(self, source, query)

    def install(self, request, progress):
        from housekeeper.providers.install import install_flatpak, install_package

        self.check_cancelled()
        if request.source not in {"web", "appimage"}:
            candidate = request.candidate
            if candidate is None or candidate.source != request.source:
                raise ManagementError(_("Search for and select a package first."))
            if request.source == "flatpak":
                return install_flatpak(self, candidate, progress)
            return install_package(self, candidate, progress)
        created = []
        try:
            add = self._web if request.source == "web" else self._appimage
            target = add(request, progress, created)
        except BaseException:
            # Remove only icons this attempt added; an identical one may serve another app.
            for icon in created:
                icon.unlink(missing_ok=True)
            raise
        return OperationResult(
            Outcome.SUCCESS, _("The application was added to your app menu."), (str(target),)
        )

    def _web(self, request, progress, created):
        # Report at once, like every other source, so Cancel is offered while this runs.
        progress(_("Adding Website"), None, True)
        url = normalize_url(request.value)
        choices = {
            "chrome": ("google-chrome-stable", "google-chrome", "chrome"),
            "chromium": ("chromium", "chromium-browser"),
        }.get(request.browser, ())
        browser = next((path for name in choices if (path := shutil.which(name))), None)
        if browser is None:
            raise ManagementError(
                _("The selected browser is not installed. Install Chrome or choose Chromium.")
            )
        name = request.name.strip() or urlsplit(url).hostname
        desktop_id, wm_class = window_identity(url)
        identifier = legacy_identifier(browser, url).removeprefix("housekeeper-")
        if any(
            (data_home() / "applications" / (name + ".desktop")).exists()
            for name in (desktop_id, "housekeeper-" + identifier)
        ):
            raise ManagementError(_("This application already has a launcher."))
        if request.icon:
            load_icon_image(request.icon)
        progress(_("Getting Website Icon"), None, True)
        icon = website_icon(url, self.check_cancelled, created)
        self.check_cancelled()
        return create_launcher(
            identifier,
            name,
            launch_args(browser, url),
            icon,
            custom_icon=request.icon,
            desktop_id=desktop_id,
            wm_class=wm_class,
            created=created,
        )

    def _appimage(self, request, progress, created):
        source = Path(request.value)
        name = request.name.strip() or appimage_name(source)
        directory = Path.home() / "AppImages"
        digest = hashlib.sha256()
        progress(_("Copying AppImage"), None, True)

        def copy(output):
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ManagementError(_("Choose a regular AppImage file."))
                copied = 0
                while chunk := stream.read(1024 * 1024):
                    self.check_cancelled()
                    output.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                    progress(_("Copying AppImage"), min(copied / max(info.st_size, 1), 1), True)

        def publish(staged):
            if not appimage_format(staged):
                raise ManagementError(_("The selected file is not a supported AppImage."))
            self.check_cancelled()
            identifier = "appimage-" + digest.hexdigest()[:24]
            destination = directory / (identifier + ".AppImage")
            try:
                os.link(staged, destination)
            except FileExistsError:
                raise ManagementError(_("This AppImage has already been added.")) from None
            try:
                # Ownership of the installed copy then belongs to its launcher.
                return create_launcher(
                    identifier,
                    name,
                    (str(destination),),
                    "application-x-executable",
                    custom_icon=request.icon,
                    created=created,
                )
            except BaseException:
                destination.unlink()
                raise

        return staged_write(directory, copy, publish, 0o755)
