"""Private discovery registry and centralized presentation capabilities."""

import os
from dataclasses import dataclass
from pathlib import Path

from housekeeper.attribution import AttributionIndex, candidate, project, resolve
from housekeeper.models import Action, AttributionState, ProviderCapabilities, Source
from housekeeper.updates import assign_update_action


@dataclass(frozen=True)
class DiscoverySnapshot:
    name: str
    contexts: tuple[str, ...]
    roots: tuple[Path, ...]
    capabilities: ProviderCapabilities


def discovery_indexes() -> tuple[AttributionIndex, ...]:
    from housekeeper.providers.deb import DebIndex
    from housekeeper.providers.flatpak import FlatpakIndex
    from housekeeper.providers.packages import PackageIndex
    from housekeeper.providers.rpm import RpmIndex

    return (FlatpakIndex(), RpmIndex(), DebIndex(), PackageIndex())


def discover(index):
    """Normalize optional backend discovery without coupling the scanner to paths."""
    return DiscoverySnapshot(
        type(index).__name__,
        tuple(getattr(index, "contexts", ())),
        tuple(getattr(index, "roots", ())),
        ProviderCapabilities(query=getattr(index, "available", True)),
    )


def installation_record(app):
    result = candidate(
        app.source,
        app.metadata["installation"],
        app.identity,
        app.version,
        app.identity,
        app.identity,
        kind="installation",
        verified=True,
        metadata=app.metadata,
        scope=app.scope,
        location=app.location,
        origin=app.origin,
        size=app.software_size,
        updated_at=app.updated_at,
    )
    projected = project(app, resolve((result,)))
    projected.key = app.key
    return projected


def assign_actions(app, capabilities):
    from housekeeper.providers.rpm import host_support

    app.action = Action.NONE if app.source not in {Source.WEB, Source.STEAM} else app.action
    verified = app.attribution is not None and app.attribution.state == AttributionState.CONFIRMED
    if verified and app.provider in {"rpm", "flatpak"}:
        capability = capabilities.get(app.provider, ProviderCapabilities(query=False))
        if capability.execute:
            app.action = Action.UNINSTALL
        elif capability.reason:
            app.metadata["management_reason"] = capability.reason
        if app.provider == "rpm":
            supported, reason = host_support()
            if not supported or os.geteuid() == 0 or app.metadata.get("name") == "housekeeper":
                app.action = Action.NONE
                app.metadata["management_reason"] = reason or "Use your system package manager."
    assign_update_action(app, capabilities)
    if app.attribution and not verified and app.source not in {Source.WEB, Source.STEAM}:
        app.action = Action.NONE
