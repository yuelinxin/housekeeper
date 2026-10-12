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
from housekeeper.appimage_identity import read_appimage_identity
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
    # AppStream ID shared by the same app in every source, and the app's one-line summary.
    app_id: str = ""
    summary: str = ""
    # Where this copy comes from, such as a repository or Flatpak remote and scope.
    origin: str = ""
    # Search lists installed copies too, marked so they are shown rather than offered.
    installed: bool = False


# Installable files by extension. A .flatpakrepo adds a source rather than an app.
FILE_KINDS = {
    ".appimage": "appimage",
    ".rpm": "rpm",
    ".deb": "deb",
    ".flatpakref": "flatpakref",
    ".flatpak": "flatpak-bundle",
    ".flatpakrepo": "flatpakrepo",
}
MAX_REF_SIZE = 1024 * 1024


def file_kind(path):
    return FILE_KINDS.get(Path(path).suffix.casefold())


def file_fingerprint(path):
    """Identifies the reviewed file, so a replaced or edited file is not installed."""
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode):
        raise ManagementError(_("Choose a regular file."))
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def read_small_file(path, limit=MAX_REF_SIZE):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ManagementError(_("Choose a regular file."))
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ManagementError(_("Choose a file smaller than 1 MB."))
    return data


@dataclass(frozen=True)
class LocalPackage:
    """What a package or Flatpak file installs, as read before the user confirms."""

    kind: str
    path: str
    title: str
    name: str
    summary: str = ""
    version: str = ""
    # The repository host a Flatpak file installs from, if it names one.
    origin: str = ""
    verified: bool = False
    # A Flatpak source the file may add, and a repository file it may add for runtimes.
    adds_remote: str = ""
    runtime_repo: str = ""
    icon: bytes = b""
    # Flatpak installations to choose from, as (path, label), the user's first.
    installations: tuple[tuple[str, str], ...] = ()
    fingerprint: tuple = ()
    # The exact reviewed bytes of a small file such as a .flatpakref.
    data: bytes = b""


@dataclass(frozen=True)
class InstallRequest:
    source: str
    value: str = ""
    name: str = ""
    browser: str = "chrome"
    candidate: InstallCandidate | None = None
    icon: str = ""
    package: LocalPackage | None = None
    # The Flatpak installation path for a Flatpak file.
    context: str = ""
    # Let PackageKit install a package file not signed by a trusted key.
    untrusted: bool = False


@dataclass(frozen=True)
class AppChoice:
    """One app in combined search results, with every source offering it, best first."""

    options: tuple[InstallCandidate, ...]

    @property
    def best(self):
        return self.options[0]

    @property
    def title(self):
        return next((o.title for o in self.options if o.title), self.best.name)

    @property
    def summary(self):
        return next((o.summary for o in self.options if o.summary), self.best.description)

    @property
    def icon(self):
        return next((o.icon for o in self.options if o.icon), "")

    @property
    def installed(self):
        return any(o.installed for o in self.options)

    @property
    def is_app(self):
        """Native packages outside the AppStream catalogue are packages, not apps."""
        return any(o.title or o.source in {"flatpak", "snap"} for o in self.options)


def source_order(preference, native):
    """Install sources, the preferred one first; "native" names the host's package type."""
    order = ["flatpak", native, "snap"]
    preferred = native if preference == "native" else preference
    return [preferred, *(source for source in order if source != preferred)]


def search_query(query):
    """The normalized query that every source accepts, or a ManagementError."""
    # AppStream matches every word of a phrase such as "video editor".
    query = " ".join(query.split())
    if not 2 <= len(query) <= 128:
        raise ManagementError(_("Enter at least two characters of an app name."))
    if not all(re.fullmatch(r"[\w.+:-]+", word) for word in query.split()):
        raise ManagementError(_("Search with letters, numbers and the characters . + : - only."))
    return query


def named_first(query, title, name):
    """AppStream weighs keyword and name matches alike; a named app sorts first."""
    words = query.casefold().split()
    return not any(
        value and all(word in value.casefold() for word in words) for value in (title, name)
    )


def app_key(candidate):
    """Copies of one app share an AppStream ID; anything else stays its own entry."""
    identifier = candidate.app_id or (candidate.name if candidate.source == "flatpak" else "")
    identifier = identifier.strip().casefold().removesuffix(".desktop")
    return identifier or (candidate.source, candidate.name)


def group_candidates(query, results, order):
    """Merge per-source results, each ranked best first, into one list of apps.

    `results` maps a source to its candidates; `order` ranks sources by preference.
    A named match sorts first, then the best rank any source gave the app. Within an
    app, installed copies come first, so its page opens on what is already there.
    """
    preference = {source: index for index, source in enumerate(order)}
    groups = {}
    for source, candidates in results.items():
        for rank, candidate in enumerate(candidates):
            groups.setdefault(app_key(candidate), []).append(
                (preference.get(source, len(order)), rank, candidate)
            )
    choices = []
    for members in groups.values():
        members.sort(key=lambda row: (not row[-1].installed, *row[:2]))
        choice = AppChoice(tuple(row[-1] for row in members))
        best_rank = min(row[1] for row in members)
        named = named_first(query, choice.title, choice.best.name)
        choices.append(((named, best_rank, members[0][0], choice.title.casefold()), choice))
    choices.sort(key=lambda row: row[0])
    return tuple(choice for _key, choice in choices)


def looks_like_website(text):
    """Whether text reads as a web address rather than an app name or ID."""
    text = text.strip()
    if not text or any(c.isspace() for c in text):
        return False
    if re.match(r"(?i)https?://", text) or text.casefold().startswith("www."):
        return True
    host = text.split("/", 1)[0]
    # Reverse-DNS app IDs such as org.gnome.Maps capitalize their last part.
    return bool(re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,24}(?::\d+)?", host))


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
        query = search_query(query)
        if source == "flatpak":
            return search_flatpak(self, query)
        if source == "snap":
            return search_snaps(self, query)
        return search_packages(self, source, query)

    def inspect(self, path):
        """Read what a package or Flatpak file would install, without installing it."""
        from housekeeper.providers.install import (
            inspect_flatpak_bundle,
            inspect_flatpakref,
            inspect_package_file,
        )

        self.check_cancelled()
        kind = file_kind(path)
        fingerprint = file_fingerprint(path)
        if kind in {"rpm", "deb"}:
            return inspect_package_file(self, kind, path, fingerprint)
        if kind == "flatpakref":
            return inspect_flatpakref(self, path, fingerprint)
        if kind == "flatpak-bundle":
            return inspect_flatpak_bundle(self, path, fingerprint)
        raise ManagementError(_("This type of file cannot be installed here."))

    def install(self, request, progress):
        from housekeeper.providers.install import (
            install_flatpak,
            install_flatpak_file,
            install_package,
            install_package_file,
        )

        self.check_cancelled()
        if request.source == "file":
            package = request.package
            try:
                unchanged = file_fingerprint(package.path) == package.fingerprint
            except OSError:
                unchanged = False
            if not unchanged:
                raise ManagementError(_("The file changed after it was opened. Open it again."))
            if package.kind in {"rpm", "deb"}:
                return install_package_file(self, request, progress)
            return install_flatpak_file(self, request, progress)
        if request.source not in {"web", "appimage"}:
            candidate = request.candidate
            if candidate is None or candidate.source != request.source:
                raise ManagementError(_("Search for and select a package first."))
            if candidate.installed:
                raise ManagementError(_("This app is already installed from this source."))
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
                identity = read_appimage_identity(destination)
                self.check_cancelled()
                if identity:
                    from housekeeper.discovery import application_roots

                    # Never shadow an installed launcher (e.g. an older import or a
                    # packaged build); keep the hashed ID and no shared window class.
                    if any((root / identity.desktop_id).exists() for root in application_roots()):
                        identity = None
                return create_launcher(
                    identifier,
                    name,
                    (str(destination),),
                    "application-x-executable",
                    custom_icon=request.icon,
                    desktop_id=identity.desktop_id.removesuffix(".desktop") if identity else "",
                    wm_class=identity.wm_class if identity else "",
                    created=created,
                )
            except BaseException:
                destination.unlink()
                raise

        return staged_write(directory, copy, publish, 0o755)
