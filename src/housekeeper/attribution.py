"""Immutable ownership candidates and deterministic inventory projection."""

from copy import deepcopy
from dataclasses import replace
from typing import Protocol

from housekeeper.identity import classify, digest
from housekeeper.models import (
    Action,
    AppComponent,
    AppRecord,
    AttributionCandidate,
    AttributionEvidence,
    AttributionResult,
    AttributionState,
    DesktopEntry,
    InstallationInstance,
    ManagementTarget,
    Relationship,
    RemovalPlan,
    Source,
    UpdateAction,
    UpdatePlan,
)


class AttributionIndex(Protocol):
    def candidates(self, entry: DesktopEntry) -> tuple[AttributionCandidate, ...]: ...


def candidate(
    source: Source,
    context: str,
    native_id: str,
    version: str,
    identity: str,
    subject: str,
    *,
    kind: str = "desktop",
    verified: bool = False,
    metadata: dict[str, str] | None = None,
    reason: str = "",
    scope: str = "System",
    location: str = "",
    origin: str = "",
    size: int | None = None,
    updated_at: int | None = None,
) -> AttributionCandidate:
    instance = InstallationInstance(
        digest(source.value, context, native_id), source.value, context, native_id, version
    )
    target = ManagementTarget(
        instance.id,
        instance.id,
        "ref" if source == Source.FLATPAK else "file" if source == Source.APPIMAGE else "package",
        native_id,
    )
    return AttributionCandidate(
        instance,
        target,
        source,
        identity,
        (AttributionEvidence(kind, subject, verified),),
        AttributionState.CONFIRMED if verified else AttributionState.INSUFFICIENT,
        reason,
        metadata=tuple(sorted((metadata or {}).items())),
        scope=scope,
        location=location,
        origin=origin,
        software_size=size,
        updated_at=updated_at,
    )


def resolve(
    candidates: tuple[AttributionCandidate, ...], errors: tuple[str, ...] = ()
) -> AttributionResult:
    candidates = tuple(sorted(set(candidates), key=lambda c: (c.instance.id, str(c.evidence))))
    owners = [c for c in candidates if c.relationship == Relationship.OWNS]
    exact = [
        c
        for c in owners
        if any(e.kind in {"desktop", "export", "installation"} for e in c.evidence)
    ]
    eligible = exact or owners
    if len({(c.instance.id, c.instance.version) for c in eligible}) > 1:
        return AttributionResult(
            AttributionState.CONFLICT,
            candidates,
            reason="Multiple installations claim this launcher. Direct management is disabled.",
            errors=errors,
        )
    if not eligible:
        return AttributionResult(
            AttributionState.UNAVAILABLE if errors else AttributionState.INSUFFICIENT,
            candidates,
            reason="Ownership could not be established.",
            errors=errors,
        )
    if all(
        not c.instance.version
        and c.source in {Source.RPM, Source.DEB, Source.PACMAN, Source.APK, Source.SNAP}
        for c in eligible
    ):
        return AttributionResult(
            AttributionState.INSUFFICIENT,
            candidates,
            reason="The claimed package is not installed.",
            errors=errors,
        )
    selected = next((c for c in eligible if c.state == AttributionState.CONFIRMED), eligible[0])
    # An unavailable ownership backend may hide a conflicting owner.
    state = AttributionState.UNAVAILABLE if errors else selected.state
    reason = (
        "Some ownership checks failed. Direct management is disabled."
        if errors
        else selected.reason
    )
    return AttributionResult(state, candidates, selected, reason, errors)


def project(app: AppRecord, result: AttributionResult) -> AppRecord:
    app = deepcopy(app)
    app.attribution = result
    selected = result.selected
    if selected:
        app.source, app.provider = selected.source, selected.instance.provider
        app.installation, app.target = selected.instance, selected.target
        app.identity, app.version = selected.identity, selected.instance.version
        app.scope, app.location, app.origin = selected.scope, selected.location, selected.origin
        app.software_size, app.updated_at = selected.software_size, selected.updated_at
        app.metadata.update(dict(selected.metadata))
        signature = digest(tuple((e.launch or e.argv) for e in app.entries))
        if result.state == AttributionState.CONFIRMED:
            app.key = digest("component", selected.instance.id, signature)
        else:
            app.key = digest("desktop", tuple(e.desktop_id for e in app.entries))
    if result.state == AttributionState.CONFLICT:
        app.source, app.provider = Source.OTHER, "other"
        app.installation = None
        app.target = None
    if result.reason:
        app.metadata["management_reason"] = result.reason
    app.component = AppComponent(
        app.key,
        tuple(e.desktop_id for e in app.entries),
        digest(tuple(e.launch or e.argv for e in app.entries)),
    )
    app.action = Action.NONE
    app.update_action = UpdateAction.INSTRUCTIONS
    return app


def attribute(app: AppRecord, indexes: tuple[AttributionIndex, ...]) -> AppRecord:
    if not app.entries:
        return deepcopy(app)
    candidates: list[AttributionCandidate] = []
    errors: list[str] = []
    for index in indexes:
        try:
            candidates.extend(index.candidates(app.entries[0]))
            errors.extend(getattr(index, "attribution_errors", ()))
        except Exception as error:
            errors.append(f"{type(index).__name__}: {error}")
    if app.source in {Source.WEB, Source.STEAM}:
        app = deepcopy(app)
        app.component = AppComponent(
            app.key,
            tuple(e.desktop_id for e in app.entries),
            digest(tuple(e.launch or e.argv for e in app.entries)),
        )
        app.attribution = AttributionResult(
            AttributionState.UNSUPPORTED,
            tuple(replace(c, relationship=Relationship.HOSTS) for c in candidates),
            reason=app.metadata.get("management_reason", "Managed by the host application."),
            errors=tuple(sorted(errors)),
        )
        return app
    if not candidates and app.source == Source.APPIMAGE:
        app = deepcopy(app)
        if errors and app.attribution:
            app.attribution = replace(
                app.attribution,
                state=AttributionState.UNAVAILABLE,
                errors=tuple(sorted(errors)),
                reason="Some ownership checks failed. Direct management is disabled.",
            )
        return app
    result = resolve(tuple(candidates), tuple(sorted(errors)))
    if app.attribution and app.attribution.state == AttributionState.UNSUPPORTED:
        result = replace(result, state=AttributionState.UNSUPPORTED, reason=app.attribution.reason)
    return project(app, result)


def evidence_digest(app: AppRecord) -> str:
    # Presentation fields such as icons and localized names are not authorization.
    return digest(
        app.installation,
        app.target,
        tuple(
            (
                str(e.path),
                e.argv,
                e.executable,
                e.resolved_executable,
                e.flatpak_id,
                e.dbus_activatable,
                e.launch,
            )
            for e in app.entries
        ),
        app.metadata.get("flatpak_binding", ""),
        app.attribution.state if app.attribution else "",
    )


def plan_binding(app: AppRecord) -> dict[str, str]:
    return {
        "instance_id": app.installation.id if app.installation else "",
        "target_id": app.target.id if app.target else "",
        "evidence_digest": evidence_digest(app),
    }


def check_binding(app: AppRecord, plan: RemovalPlan | UpdatePlan | None) -> None:
    from housekeeper.models import ManagementError

    if plan is None:
        raise ManagementError("The preview is missing. Review a new preview.")
    if plan.app_key != app.key or plan.provider != app.provider:
        raise ManagementError("The preview belongs to another application.")
    for key, value in plan_binding(app).items():
        if getattr(plan, key) != value:
            raise ManagementError("The application's ownership changed. Review a new preview.")


def revalidate(app: AppRecord) -> None:
    """Use the same competing ownership checks for every direct management entrypoint."""
    from housekeeper.discovery import read_entry
    from housekeeper.inventory import discovery_indexes
    from housekeeper.models import ManagementError

    if app.attribution and app.attribution.state != AttributionState.CONFIRMED:
        raise ManagementError(
            app.attribution.reason or "The application's ownership is not verified."
        )
    if not app.entries:
        return  # Official installation records are validated by their backend.
    indexes = discovery_indexes()
    for entry in app.entries:
        try:
            fresh = attribute(classify(read_entry(entry.path, entry.root)), indexes)
        except Exception as error:
            raise ManagementError(
                "The launcher cannot be verified. Refresh the inventory."
            ) from error
        if (
            not fresh.attribution
            or fresh.attribution.state != AttributionState.CONFIRMED
            or fresh.installation != app.installation
            or fresh.target != app.target
            or evidence_digest(fresh) != evidence_digest(replace(app, entries=[entry]))
        ):
            raise ManagementError(
                "The launcher or its ownership changed. Refresh and review a new preview."
            )
