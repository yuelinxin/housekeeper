"""How this running copy of Housekeeper was installed, and updating it through that source.

Detection examines the code that is running, not the inventory: a development checkout
may run beside an installed package, and only the running copy can be restarted.
"""

import configparser
import functools
import logging
import shlex
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock

import housekeeper
from housekeeper import APP_ID
from housekeeper.i18n import _
from housekeeper.models import ManagementError, OperationCancelled, UpdateState
from housekeeper.updates import has_application_update

LOG = logging.getLogger(__name__)
FLATPAK_INFO = Path("/.flatpak-info")
SNAP_ROOTS = (Path("/snap"), Path("/var/lib/snapd/snap"))


class InstallMethod(str, Enum):
    RPM = "rpm"
    DEB = "deb"
    PACMAN = "pacman"
    APK = "apk"
    SNAP = "snap"
    FLATPAK = "flatpak"
    SOURCE = "source"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SelfInstallation:
    method: InstallMethod
    package: str = ""
    arch: str = ""
    location: str = ""
    # Whether Housekeeper can check for and install its own updates in the app.
    updatable: bool = False
    # Why it cannot, and what the person can run instead.
    reason: str = ""
    command: str = ""


def is_housekeeper(app):
    return app.metadata.get("name") == "housekeeper" or app.metadata.get("app_id") == APP_ID


def _flatpak_installation(info):
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(info.read_text())
    except (OSError, UnicodeError, configparser.Error) as error:
        LOG.debug("Could not read %s: %s", info, error)
    app_id = parser.get("Application", "name", fallback="") or APP_ID
    location = parser.get("Instance", "app-path", fallback="")
    return SelfInstallation(
        InstallMethod.FLATPAK,
        app_id,
        parser.get("Instance", "arch", fallback=""),
        location,
        reason=_(
            "Housekeeper is running as a Flatpak. Flatpak updates apps from outside their "
            "sandbox, so update Housekeeper in your software manager or with this command, "
            "then reopen it."
        ),
        command="flatpak update " + shlex.quote(app_id),
    )


def _checkout_root(module):
    """The source tree a development launcher runs from, if this is one."""
    root = module.parent.parent.parent
    if module.parent.parent.name == "src" and (root / "meson.build").is_file():
        return root
    return None


def _owners(path):
    from housekeeper.ownership import FileOwnershipIndex

    index = FileOwnershipIndex()
    # Only the package databases a copy of Housekeeper can come from; Flatpak and Snap
    # copies are recognized by where they run.
    index.queries = {name: getattr(index, name) for name in ("rpm", "deb", "pacman", "apk")}
    return index.query(path)


def _rpm_installation(package, arch, location):
    from housekeeper.providers.rpm import RpmProvider

    capability = RpmProvider().capabilities()
    command = "sudo dnf upgrade -- " + shlex.quote(package) if shutil.which("dnf") else ""
    if capability.update_preview and capability.update_execute:
        return SelfInstallation(InstallMethod.RPM, package, arch, location, True, command=command)
    return SelfInstallation(
        InstallMethod.RPM,
        package,
        arch,
        location,
        reason=" ".join(
            part
            for part in (
                capability.update_reason,
                _("Update Housekeeper using your system package manager, then reopen it."),
            )
            if part
        ),
        command=command,
    )


def detect_installation(module=None, *, flatpak_info=FLATPAK_INFO, owners=_owners):
    """Name the source this copy of Housekeeper was installed from."""
    from housekeeper.models import FileOwnershipState

    module = Path(module if module is not None else housekeeper.__file__).absolute()
    location = str(module.parent)
    # Environment variables are inherited from whatever launched Housekeeper, so only
    # the sandbox's own file and the code's location identify these.
    if flatpak_info.exists():
        return _flatpak_installation(flatpak_info)
    snap = next((root for root in SNAP_ROOTS if module.is_relative_to(root)), None)
    if snap is not None:
        name = module.relative_to(snap).parts[0]
        return SelfInstallation(
            InstallMethod.SNAP,
            name,
            location=location,
            reason=_(
                "Housekeeper was installed as a Snap. Snaps update automatically; "
                "to update now, run this command and reopen Housekeeper."
            ),
            command="snap refresh " + shlex.quote(name),
        )
    root = _checkout_root(module)
    if root is not None:
        return SelfInstallation(
            InstallMethod.SOURCE,
            location=str(root),
            reason=_(
                "Housekeeper is running from a source checkout. Pull the latest changes and "
                "restart it; the development launcher rebuilds its resources."
            ),
            command="git -C " + shlex.quote(str(root)) + " pull"
            if (root / ".git").exists()
            else "",
        )
    result = owners(module)
    if result.state == FileOwnershipState.OWNED and len(result.owners) == 1:
        kind, _separator, rest = result.owners[0].partition(":")
        if kind == "rpm":
            package, _separator, arch = rest.partition(":")
            return _rpm_installation(package, arch, location)
        package = rest.rsplit(":", 1)[-1]
        commands = {
            "deb": "sudo apt install --only-upgrade -- " + shlex.quote(package),
            "pacman": "sudo pacman -Syu",
            "apk": "sudo apk upgrade -- " + shlex.quote(package),
        }
        if kind in commands:
            return SelfInstallation(
                InstallMethod(kind),
                package,
                location=location,
                reason=_(
                    "Housekeeper was installed by your system package manager, which "
                    "Housekeeper cannot use to update packages on this system. Update it "
                    "there, then reopen Housekeeper."
                ),
                command=commands[kind],
            )
    reason = _(
        "Housekeeper could not tell how this copy was installed (for example, with "
        "meson install). Install a newer release the same way, then reopen Housekeeper."
    )
    if result.state == FileOwnershipState.OWNED:
        reason = _("Housekeeper's files belong to more than one package.") + " " + reason
    elif result.state == FileOwnershipState.ERROR and result.reason:
        reason += "\n\n" + result.reason
    return SelfInstallation(InstallMethod.UNKNOWN, location=location, reason=reason)


@functools.cache
def current_installation():
    """Detect once per process; the running copy cannot change without a restart."""
    return detect_installation()


def installation_record(installation, inventory):
    """The inventory record Housekeeper's own package management acts on."""
    if installation.method != InstallMethod.RPM or not installation.updatable:
        return None
    from housekeeper.providers.rpm import manages_context

    matches = [
        app
        for app in inventory
        if app.provider == "rpm"
        and app.metadata.get("name") == installation.package
        and app.metadata.get("arch") == installation.arch
        and manages_context(app)
    ]
    # Prefer the visible launcher; auxiliary entries of the same package also qualify.
    return next((app for app in matches if app.visible), matches[0] if matches else None)


class SelfUpdater:
    """One service task checking or installing Housekeeper's own update.

    The checks and transactions are the RPM provider's, with Housekeeper's own
    package allowed as the subject; nothing else about the preview is relaxed.
    """

    def __init__(self, factory, inventory=(), installation=None):
        self.factory = factory
        self.inventory = inventory
        self.installation = installation
        self.cancelled = False
        self.provider = None
        self.lock = Lock()

    def request_cancel(self):
        with self.lock:
            self.cancelled = True
            if self.provider:
                self.provider.request_cancel()

    def _activate(self, app, provider=None):
        with self.lock:
            if self.cancelled:
                raise OperationCancelled(_("The update operation was cancelled."))
            self.provider = provider if provider is not None else self.factory(app)
            return self.provider

    def record(self):
        installation = self.installation or current_installation()
        if not installation.updatable:
            raise ManagementError(installation.reason)
        app = installation_record(installation, self.inventory)
        if app is None:
            raise ManagementError(
                _(
                    "Housekeeper's own package is not in the app list yet. "
                    "Refresh the app list and try again."
                )
            )
        return app

    def check(self, progress, provider=None, **kwargs):
        """Return Housekeeper's update item, or None when it is current."""
        from housekeeper.batch_updates import UpdateItem

        app = self.record()
        provider = self._activate(app, provider)
        result = provider.prepare_update(app, self.inventory, progress, allow_self=True, **kwargs)
        if result.state != UpdateState.AVAILABLE:
            return None
        if result.plan is None or not result.plan.changes:
            raise ManagementError(_("The manager did not provide an update preview."))
        if not has_application_update(app, result.plan):
            return None
        return UpdateItem(app, result.plan, (app.name,))

    def execute(self, item, progress):
        provider = self._activate(item.app)
        return provider.execute_update(item.app, item.plan, progress, allow_self=True)


def updated_self(item, result):
    """Whether a finished update changed the package that is running now."""
    return any(
        change.identity in result.completed
        for change in item.plan.changes
        if change.target == item.plan.target
    )


def restart_command(orig_argv, executable):
    """Run the same launcher again, as a normal instance rather than a D-Bus service."""
    return executable, [arg for arg in orig_argv if arg != "--gapplication-service"]
