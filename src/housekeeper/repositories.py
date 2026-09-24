"""Read configured repositories and apply explicit source changes through their managers."""

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from gi.repository import Gio, GLib

from housekeeper.i18n import _
from housekeeper.models import ManagementError, OperationCancelled
from housekeeper.platforms import native_package_source
from housekeeper.providers.flatpak import configured_installations, load_flatpak
from housekeeper.providers.install import checked_packages, package_client

MAX_REPO_SIZE = 1024 * 1024


@dataclass(frozen=True)
class SoftwareSource:
    provider: str
    identifier: str
    title: str
    enabled: bool
    context: str = ""
    url: str = ""
    verified: bool | None = None


@dataclass(frozen=True)
class SourceGroup:
    provider: str
    title: str
    context: str = ""
    sources: tuple[SoftwareSource, ...] = ()
    error: str = ""
    can_add: bool = False


@dataclass(frozen=True)
class FlatpakSourcePlan:
    context: str
    name: str
    title: str
    url: str
    verified: bool
    data: bytes


def _flatpak_source(installation, remote):
    return SoftwareSource(
        "flatpak",
        remote.get_name(),
        remote.get_title() or remote.get_name(),
        not remote.get_disabled(),
        installation.get_path().get_path(),
        remote.get_url() or "",
        remote.get_gpg_verify(),
    )


def _package_sources(client, pk, cancel):
    result = client.get_repo_list(0, cancel, lambda *_args: None, None)
    checked_packages(result, pk)
    return tuple(
        SoftwareSource(
            "native", repo.get_id(), repo.get_description() or repo.get_id(), repo.get_enabled()
        )
        for repo in result.get_repo_detail_array()
    )


def _remote_from_file(fp, name, data):
    remote = fp.Remote.new_from_file(name, GLib.Bytes.new(data))
    # Uncommitted libflatpak objects report False both for an unset verification
    # flag and for GPGVerify=false. Require verification unless the file explicitly
    # disables it, so the review shows exactly what will be saved.
    keyfile = GLib.KeyFile()
    try:
        keyfile.load_from_bytes(GLib.Bytes.new(data), GLib.KeyFileFlags.NONE)
        verify = keyfile.get_boolean("Flatpak Repo", "GPGVerify")
    except GLib.Error:
        verify = True
    remote.set_gpg_verify(verify)
    return remote


class _HttpsRedirects(HTTPRedirectHandler):
    """Follow a redirect only to HTTPS; a single plain-HTTP hop could substitute the file."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ManagementError(_("The source download redirected to an insecure URL."))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_repository_file(value):
    """Read one bounded .flatpakrepo file; repository metadata is never executed."""
    url = urlsplit(value)
    if url.scheme:
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ManagementError(_("Use an HTTPS .flatpakrepo URL or choose a local file."))
        with build_opener(_HttpsRedirects()).open(value, timeout=15) as response:
            data = response.read(MAX_REPO_SIZE + 1)
    else:
        with os.fdopen(os.open(value, os.O_RDONLY | os.O_NONBLOCK), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ManagementError(_("Choose a regular .flatpakrepo file."))
            data = stream.read(MAX_REPO_SIZE + 1)
    if not data or len(data) > MAX_REPO_SIZE:
        raise ManagementError(_("Choose a .flatpakrepo file smaller than 1 MB."))
    return data


class RepositoryManager:
    def __init__(self):
        self.cancel = Gio.Cancellable()

    def request_cancel(self):
        self.cancel.cancel()

    def _check_cancelled(self):
        if self.cancel.is_cancelled():
            raise OperationCancelled(_("The software source operation was cancelled."))

    def list_sources(self):
        groups = []
        native, title = native_package_source()
        try:
            client, pk = package_client(native)
            sources = _package_sources(client, pk, self.cancel)
            groups.append(SourceGroup("native", title, sources=sources))
        except Exception as error:
            self._check_cancelled()
            groups.append(SourceGroup("native", title, error=str(error)))
        try:
            fp = load_flatpak()
            installations, warnings = configured_installations(fp)
        except (ImportError, ValueError) as error:
            groups.append(
                SourceGroup(
                    "flatpak",
                    _("Flatpak"),
                    error=_("Flatpak support is not installed.") + "\n" + str(error),
                )
            )
            return tuple(groups)
        for installation in installations:
            self._check_cancelled()
            title = (
                _("Flatpak — User")
                if installation.get_is_user()
                else _("Flatpak — System (%s)") % (installation.get_id() or "default")
            )
            context = installation.get_path().get_path()
            try:
                sources = tuple(
                    _flatpak_source(installation, remote)
                    for remote in installation.list_remotes(self.cancel)
                )
                groups.append(SourceGroup("flatpak", title, context, sources, can_add=True))
            except Exception as error:
                self._check_cancelled()
                groups.append(SourceGroup("flatpak", title, context, error=str(error)))
        if warnings:
            groups.append(
                SourceGroup("flatpak", _("Other Flatpak Installations"), error="\n".join(warnings))
            )
        self._check_cancelled()
        return tuple(groups)

    def _installation(self, context):
        fp = load_flatpak()
        installations, _warnings = configured_installations(fp)
        installation = next((i for i in installations if i.get_path().get_path() == context), None)
        if installation is None:
            raise ManagementError(
                _("The Flatpak installation changed. Refresh software sources and try again.")
            )
        return fp, installation

    def set_enabled(self, source, enabled):
        self._check_cancelled()
        if source.provider == "native":
            client, pk = package_client(native_package_source()[0])
            current = next(
                (
                    r
                    for r in _package_sources(client, pk, self.cancel)
                    if r.identifier == source.identifier
                ),
                None,
            )
            if current != source:
                raise ManagementError(_("The software source changed. Refresh and try again."))
            result = client.repo_enable(
                source.identifier, enabled, self.cancel, lambda *_args: None, None
            )
            checked_packages(result, pk)
        elif source.provider == "flatpak":
            _fp, installation = self._installation(source.context)
            remote = next(
                (
                    r
                    for r in installation.list_remotes(self.cancel)
                    if r.get_name() == source.identifier
                ),
                None,
            )
            if remote is None or _flatpak_source(installation, remote) != source:
                raise ManagementError(_("The software source changed. Refresh and try again."))
            remote.set_disabled(not enabled)
            if not installation.modify_remote(remote, self.cancel):
                raise ManagementError(_("The Flatpak source could not be saved."))
        else:
            raise ManagementError(_("This software source cannot be changed here."))

    def prepare_flatpak_source(self, context, value, name=""):
        fp, _installation = self._installation(context)
        self._check_cancelled()
        data = _read_repository_file(value.strip())
        self._check_cancelled()
        if not name.strip():
            name = Path(urlsplit(value).path).name.removesuffix(".flatpakrepo")
            name = re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-") or "new-source"
        name = name.strip()
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", name):
            raise ManagementError(
                _("Use a source name with letters, numbers, hyphens or underscores.")
            )
        remote = _remote_from_file(fp, name, data)
        if not remote.get_url():
            raise ManagementError(_("The .flatpakrepo file does not contain a repository URL."))
        return FlatpakSourcePlan(
            context,
            name,
            remote.get_title() or name,
            remote.get_url(),
            remote.get_gpg_verify(),
            data,
        )

    def add_flatpak_source(self, plan):
        fp, installation = self._installation(plan.context)
        self._check_cancelled()
        if any(remote.get_name() == plan.name for remote in installation.list_remotes(self.cancel)):
            raise ManagementError(
                _("A source with this name already exists. Choose a different name.")
            )
        # Use exactly the bytes reviewed in the confirmation; do not fetch a URL again.
        remote = _remote_from_file(fp, plan.name, plan.data)
        if (remote.get_url(), remote.get_gpg_verify()) != (plan.url, plan.verified):
            raise ManagementError(_("The source details changed. Review the source again."))
        if not installation.add_remote(remote, False, self.cancel):
            raise ManagementError(_("The Flatpak source could not be added."))
