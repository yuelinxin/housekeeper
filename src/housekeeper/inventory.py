"""Private discovery registry and centralized presentation capabilities."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from housekeeper.attribution import AttributionIndex, candidate, project, resolve
from housekeeper.models import (
    Action,
    AttributionCandidate,
    AttributionState,
    DesktopEntry,
    ProviderCapabilities,
    Source,
)
from housekeeper.updates import assign_update_action


@dataclass(frozen=True)
class DiscoverySnapshot:
    name: str
    contexts: tuple[str, ...]
    roots: tuple[Path, ...]
    capabilities: ProviderCapabilities
    index: AttributionIndex = field(repr=False, compare=False)
    ownership_backends: tuple[str, ...] = ()

    def candidates(self, entry: DesktopEntry) -> tuple[AttributionCandidate, ...]:
        return self.index.candidates(entry)

    @property
    def attribution_errors(self):
        return getattr(self.index, "attribution_errors", ())

    def file_owners(self, path, ownership=None):
        from housekeeper.models import FileOwnershipResult, FileOwnershipState
        from housekeeper.ownership import FileOwnershipIndex, combine

        ownership = FileOwnershipIndex() if ownership is None else ownership
        results = []
        for name in self.ownership_backends:
            try:
                results.append(ownership.queries[name](path))
            except Exception as error:
                results.append(
                    FileOwnershipResult(FileOwnershipState.ERROR, reason=f"{name}: {error}")
                )
        if results and all(result.state == FileOwnershipState.NOT_APPLICABLE for result in results):
            return FileOwnershipResult(FileOwnershipState.NOT_APPLICABLE)
        return combine(results)


def discovery_indexes() -> tuple[AttributionIndex, ...]:
    from housekeeper.providers.deb import DebIndex
    from housekeeper.providers.flatpak import FlatpakIndex
    from housekeeper.providers.packages import PackageIndex
    from housekeeper.providers.rpm import RpmIndex

    return (FlatpakIndex(), RpmIndex(), DebIndex(), PackageIndex())


def discover(index):
    """Normalize optional backend discovery without coupling the scanner to paths."""
    name = type(index).__name__
    backends = {
        "FlatpakIndex": ("flatpak",),
        "RpmIndex": ("rpm",),
        "DebIndex": ("deb",),
        "PackageIndex": ("pacman", "apk", "snap"),
    }.get(name, ())
    capability = ProviderCapabilities(query=getattr(index, "available", True))
    if name == "FlatpakIndex":
        from housekeeper.providers.flatpak import FlatpakProvider

        capability = FlatpakProvider().capabilities()
    elif name == "RpmIndex":
        from housekeeper.providers.rpm import RpmProvider

        capability = RpmProvider().capabilities()
    contexts = tuple(getattr(index, "contexts", ()))
    if hasattr(index, "installations"):
        contexts = tuple(index.installations)
    elif hasattr(index, "context"):
        contexts = (index.context,)
    return DiscoverySnapshot(
        name,
        contexts,
        tuple(getattr(index, "roots", ())),
        capability,
        index,
        backends,
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
    projected.component = None
    return projected


def assign_actions(app, capabilities):
    from housekeeper.providers.rpm import host_support, manages_context

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
            if (
                not supported
                or not manages_context(app)
                or os.geteuid() == 0
                or app.metadata.get("name") == "housekeeper"
            ):
                app.action = Action.NONE
                app.metadata["management_reason"] = reason or "Use your system package manager."
    assign_update_action(app, capabilities)
    if app.attribution and not verified and app.source not in {Source.WEB, Source.STEAM}:
        app.action = Action.NONE
