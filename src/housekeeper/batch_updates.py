"""Explicit inventory-wide checks and serialized, confirmed update batches."""

import logging
from dataclasses import dataclass
from threading import Lock

from housekeeper.i18n import _
from housekeeper.models import (
    AppRecord,
    ManagementError,
    OperationCancelled,
    OperationResult,
    Outcome,
    UpdateAction,
    UpdatePlan,
    UpdateState,
)

LOG = logging.getLogger(__name__)
UPDATE_PROVIDERS = ("rpm", "flatpak")


@dataclass(frozen=True)
class UpdateItem:
    app: AppRecord
    plan: UpdatePlan
    names: tuple[str, ...]


@dataclass(frozen=True)
class UpdateReport:
    items: tuple[UpdateItem, ...]
    errors: tuple[str, ...] = ()
    cancelled: bool = False
    unsupported: int = 0


def installation_key(app):
    if app.installation:
        return (app.provider, app.installation.id)
    if app.provider == "rpm":
        return ("rpm", app.metadata.get("name", app.identity), app.metadata.get("arch", ""))
    return (app.provider, app.metadata.get("installation", ""), app.identity)


def change_key(plan, change):
    return (plan.provider, plan.installation, change.identity)


class UpdateBatch:
    """One service task; cancellation follows the current provider between transactions."""

    def __init__(self, factory, inventory):
        self.factory = factory
        self.inventory = inventory
        self.cancelled = False
        self.provider = None
        self.lock = Lock()

    def request_cancel(self):
        with self.lock:
            self.cancelled = True
            if self.provider:
                self.provider.request_cancel()

    def _activate(self, app):
        with self.lock:
            if self.cancelled:
                raise OperationCancelled(_("The update operation was cancelled."))
            self.provider = self.factory(app)
            return self.provider

    def check(self, progress, providers=UPDATE_PROVIDERS):
        groups, unsupported = {}, 0
        for app in self.inventory:
            if app.provider in UPDATE_PROVIDERS and app.provider not in providers:
                continue
            if app.update_action != UpdateAction.CHECK:
                unsupported += 1
                continue
            groups.setdefault(installation_key(app), []).append(app)
        items, errors = [], []
        refreshed = False
        total = len(groups)
        if self.cancelled:
            return UpdateReport((), (), True, unsupported)
        if not total:
            progress(_("No applications to check"), 1.0, False)
        for index, apps in enumerate(groups.values()):
            app = apps[0]
            label = _("Checking %(name)s (%(index)d of %(total)d)") % {
                "name": app.name,
                "index": index + 1,
                "total": total,
            }

            def checking_progress(message, _fraction, can_cancel, index=index, label=label):
                # Backend percentages restart for metadata, resolution and previews.
                # Count completed installations instead of presenting those phases as
                # the progress of the entire inventory (or switching to pulse mode).
                progress(f"{label}\n{message}" if message else label, index / total, can_cancel)

            try:
                provider = self._activate(app)
                progress(label, index / total, True)
                kwargs = {"refresh": not refreshed} if app.provider == "rpm" else {}
                check = provider.prepare_update(app, self.inventory, checking_progress, **kwargs)
                if app.provider == "rpm":
                    refreshed = True
                if check.state == UpdateState.AVAILABLE:
                    if check.plan is None or not check.plan.changes:
                        raise ManagementError(_("The manager did not provide an update preview."))
                    items.append(UpdateItem(app, check.plan, tuple(a.name for a in apps)))
            except OperationCancelled:
                return UpdateReport(tuple(items), tuple(errors), True, unsupported)
            except Exception as error:
                LOG.warning(
                    "Update check failed for %s (%s, %s): %s",
                    app.name,
                    app.provider,
                    app.identity,
                    error,
                    exc_info=True,
                )
                errors.append(f"{app.name}: {error}")
            if self.cancelled:
                return UpdateReport(tuple(items), tuple(errors), True, unsupported)
            progress(
                _("Checked %(done)d of %(total)d applications")
                % {"done": index + 1, "total": total},
                (index + 1) / total,
                index + 1 < total,
            )
        return UpdateReport(tuple(items), tuple(errors), False, unsupported)

    @staticmethod
    def _remaining_plan(original, fresh, verified):
        # A preceding successful transaction may have updated a shared dependency.
        # Accept only its exact disappearance, with the original source configuration.
        remaining = tuple(c for c in original.changes if verified.get(change_key(original, c)) != c)
        if fresh.fingerprint == original.fingerprint:
            return fresh
        if not (
            original.environment
            and fresh.environment == original.environment
            and (
                fresh.app_key,
                fresh.provider,
                fresh.installation,
                fresh.target,
                fresh.current_version,
            )
            == (
                original.app_key,
                original.provider,
                original.installation,
                original.target,
                original.current_version,
            )
            and fresh.changes == remaining
        ):
            raise ManagementError(
                _("The update plan changed. Check again and review a new preview.")
            )
        return fresh

    def execute(self, items, progress):
        completed, errors, hints, verified = [], [], [], {}
        completed_app_keys = []
        outcome = Outcome.SUCCESS
        for index, item in enumerate(items):
            app, original = item.app, item.plan
            try:
                provider = self._activate(app)
                progress(
                    _("Updating %(name)s (%(index)d of %(total)d)")
                    % {"name": app.name, "index": index + 1, "total": len(items)},
                    index / len(items),
                    True,
                )
                if all(verified.get(change_key(original, c)) == c for c in original.changes):
                    completed.extend(item.names)
                    completed_app_keys.extend(
                        a.key
                        for a in self.inventory
                        if installation_key(a) == installation_key(app)
                    )
                    continue
                kwargs = {"refresh": False} if app.provider == "rpm" else {}
                check = provider.prepare_update(app, self.inventory, progress, **kwargs)
                if check.state != UpdateState.AVAILABLE or check.plan is None:
                    raise ManagementError(
                        _("The update plan changed. Check again and review a new preview.")
                    )
                plan = self._remaining_plan(original, check.plan, verified)
                if self.cancelled:
                    raise OperationCancelled(_("The update operation was cancelled."))
                result = provider.execute_update(app, plan, progress)
                if result.restart_hint:
                    hints.append(result.restart_hint)
                if result.outcome != Outcome.SUCCESS:
                    LOG.warning(
                        "Update failed for %s (%s, %s): %s; %s",
                        app.name,
                        app.provider,
                        app.identity,
                        result.message,
                        "; ".join(result.errors),
                    )
                    completed.extend(f"{app.name}: {identity}" for identity in result.completed)
                    errors.extend((f"{app.name}: {result.message}", *result.errors))
                    outcome = result.outcome
                    break
                completed.extend(item.names)
                completed_app_keys.extend(
                    a.key for a in self.inventory if installation_key(a) == installation_key(app)
                )
                verified.update((change_key(plan, c), c) for c in plan.changes)
            except Exception as error:
                LOG.warning(
                    "Update execution failed for %s (%s, %s): %s",
                    app.name,
                    app.provider,
                    app.identity,
                    error,
                    exc_info=True,
                )
                outcome = (
                    Outcome.CANCELLED if isinstance(error, OperationCancelled) else Outcome.FAILED
                )
                errors.append(f"{app.name}: {error}")
                break
        if outcome != Outcome.SUCCESS and completed:
            outcome = Outcome.PARTIAL
        return OperationResult(
            outcome,
            _("All requested updates completed.")
            if outcome == Outcome.SUCCESS
            else _("The batch stopped. Check again to review the remaining updates."),
            tuple(dict.fromkeys(completed)),
            tuple(errors),
            "\n".join(dict.fromkeys(hints)),
            completed_app_keys=tuple(completed_app_keys),
        )
