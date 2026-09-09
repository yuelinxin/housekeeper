"""Local update capability attribution and source-specific management guidance."""

import os
import shlex

from housekeeper import APP_ID
from housekeeper.i18n import _
from housekeeper.models import AttributionState, Source, UpdateAction


def assign_update_action(app, capabilities):
    app.update_action = UpdateAction.INSTRUCTIONS
    app.update_reason = ""
    if app.metadata.get("name") == "housekeeper" or app.metadata.get("app_id") == APP_ID:
        app.update_reason = _("Update Housekeeper itself using your system package manager.")
        return
    if app.attribution and app.attribution.state != AttributionState.CONFIRMED:
        app.update_reason = (
            app.attribution.reason or "The ownership of this application is not verified."
        )
        return
    capability = capabilities.get(app.provider)
    if capability is None:
        return
    if app.provider == "flatpak" and not app.metadata.get("installation"):
        app.update_reason = _("The Flatpak installation could not be identified uniquely.")
    elif app.provider == "rpm" and app.metadata.get("rpm_verified") != "true":
        app.update_reason = app.metadata.get("management_reason") or _(
            "The application's RPM launch target could not be verified."
        )
    elif os.geteuid() == 0:
        app.update_reason = _("Run Housekeeper as a regular desktop user.")
    elif capability.update_preview and capability.update_execute:
        app.update_action = UpdateAction.CHECK
    else:
        app.update_reason = capability.update_reason or _("Direct updates are unavailable.")


def update_instructions(app):
    if app.source in {Source.RPM, Source.DEB, Source.PACMAN, Source.APK, Source.SNAP}:
        text = _("Review updates for this package using your system package manager.")
        package = app.metadata.get("name")
        if package and app.source == Source.RPM and app.metadata.get("rpm_verified") == "true":
            arch = app.metadata.get("arch")
            text += "\n\nsudo dnf upgrade -- " + shlex.quote(package + ("." + arch if arch else ""))
    elif app.source == Source.FLATPAK:
        text = _("Open your software manager and select this Flatpak installation to update it.")
    elif app.source == Source.APPIMAGE:
        text = _(
            "Use the application's built-in updater or obtain a newer AppImage from its publisher. "
            "Housekeeper does not download or replace AppImage files."
        )
    elif app.source == Source.STEAM:
        text = _("Open Steam, select this game in your library, and review its Updates settings.")
    elif app.provider == "firefoxpwa":
        text = _(
            "Open the PWAsForFirefox manager to review updates for this web app and its runtime."
        )
    elif app.source == Source.WEB:
        text = _(
            "Open this web app in its original browser to receive website updates. "
            "Manage browser updates separately through the browser's installation source."
        )
    else:
        text = _("Use the application's own updater or follow its publisher's update instructions.")
    return "\n\n".join(part for part in (app.update_reason, text) if part)


def authorization_notice(app):
    if app.provider == "rpm" or (app.provider == "flatpak" and app.scope == "System"):
        return _(
            "If administrator authorization is required, approve the system authentication dialog "
            "using your password or configured fingerprint. Housekeeper does not collect your password."
        )
    return ""
