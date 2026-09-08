"""Inventory orchestration and serialized management operations."""

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from housekeeper.discovery import application_roots, scan_entries
from housekeeper.identity import classify, merge_records
from housekeeper.models import (
    Action,
    ManagementError,
    OperationCancelled,
    OperationResult,
    Outcome,
    Source,
)
from housekeeper.updates import assign_update_action

LOG = logging.getLogger(__name__)


def collect(partial=None, roots=None):
    entries, warnings = scan_entries(roots)
    records = [classify(entry) for entry in entries]
    if partial:
        partial(merge_records(records))
    from housekeeper.providers.flatpak import FlatpakIndex, FlatpakProvider
    from housekeeper.providers.rpm import RpmIndex, RpmProvider

    flatpaks, rpms = FlatpakIndex(), RpmIndex()
    if roots is None:
        extra_roots = [p for p in flatpaks.roots if p not in application_roots()]
        extra_entries, extra_warnings = scan_entries(extra_roots)
        known_ids = {e.desktop_id for e in entries}
        records.extend(classify(e) for e in extra_entries if e.desktop_id not in known_ids)
        warnings.extend(extra_warnings)
    warnings.extend(flatpaks.warnings)
    rpm_capability = RpmProvider().capabilities()
    output = []
    for record in records:
        if record.source == Source.OTHER:
            matched = flatpaks.associate(record)
            if matched is not None:
                output.append(matched)
                continue
        try:
            rpms.enrich(record)
        except Exception as error:
            LOG.debug("RPM lookup failed", exc_info=True)
            warnings.append(f"Could not identify a system package: {error}")
        if record.source == Source.RPM and not rpm_capability.execute:
            record.action = Action.NONE
            record.metadata["management_reason"] = rpm_capability.reason
        if record.scope == "Unknown" and record.entries:
            record.metadata["entry_scope"] = (
                "User" if record.entries[0].path.is_relative_to(Path.home()) else "System"
            )
            if record.source == Source.APPIMAGE and record.location:
                record.scope = (
                    "User" if Path(record.location).is_relative_to(Path.home()) else "Unknown"
                )
        if record.source in {Source.WEB, Source.STEAM} and not record.entries[0].executable:
            record.action = Action.INSTRUCTIONS
            record.metadata["management_reason"] = "The application's manager is unavailable."
        output.append(record)
    output.extend(flatpaks.apps)
    output = merge_records(output)
    from housekeeper.providers.appimage import AppImageProvider

    image_provider = AppImageProvider()
    update_capabilities = {"rpm": rpm_capability, "flatpak": FlatpakProvider().capabilities()}
    for record in output:
        assign_update_action(record, update_capabilities)
        if record.source == Source.APPIMAGE:
            try:
                image_provider.prepare(record, output)
                record.action = Action.TRASH
            except (ManagementError, OSError, RuntimeError) as error:
                record.metadata["management_reason"] = str(error)
        elif record.source == Source.OTHER:
            record.metadata["management_reason"] = (
                "No supported package manager could establish this application's ownership. "
                "Review its file location or the publisher's uninstall instructions."
            )
    monitors = list(
        dict.fromkeys(
            [
                *(application_roots() if roots is None else roots),
                *flatpaks.roots,
                *(entry.path.parent for app in output for entry in app.entries),
            ]
        )
    )
    return output, list(dict.fromkeys(warnings)), monitors, flatpaks.installations


class InventoryService:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="housekeeper")
        self.inventory = []
        self.busy = False
        self.scanning = False
        self.active_provider = None
        self.closed = False

    def scan(self, partial, completed, failed):
        if self.closed or self.scanning or self.busy:
            return False
        self.scanning = True
        try:
            future = self.executor.submit(collect, lambda apps: self.dispatch(partial, apps))
        except Exception as error:
            self.scanning = False
            self.dispatch(failed, str(error))
            return False

        def done(result):
            self.scanning = False
            try:
                apps, warnings, roots, installations = result.result()
                self.inventory = apps
                self.dispatch(completed, apps, warnings, roots, installations)
            except Exception as error:
                LOG.debug("Inventory scan failed", exc_info=True)
                self.dispatch(failed, str(error))

        future.add_done_callback(done)
        return True

    def _provider(self, app):
        if app.provider == "rpm":
            from housekeeper.providers.rpm import RpmProvider

            return RpmProvider()
        if app.provider == "flatpak":
            from housekeeper.providers.flatpak import FlatpakProvider

            return FlatpakProvider()
        if app.provider == "appimage":
            from housekeeper.providers.appimage import AppImageProvider

            return AppImageProvider()
        raise ManagementError("This application is managed by another tool.")

    def prepare(self, app, completed, failed):
        inventory = list(self.inventory)
        self._submit(app, lambda provider: provider.prepare(app, inventory), completed, failed)

    def prepare_update(self, app, progress, completed, failed):
        inventory = list(self.inventory)
        self._submit(
            app,
            lambda provider: provider.prepare_update(
                app, inventory, lambda *args: self.dispatch(progress, *args)
            ),
            completed,
            failed,
            preserve_error=True,
        )

    def execute_update(self, app, plan, progress, completed):
        self._submit(
            app,
            lambda provider: provider.execute_update(
                app, plan, lambda *args: self.dispatch(progress, *args)
            ),
            completed,
            lambda error: completed(self._failure(error)),
            preserve_error=True,
        )

    def check_updates(self, progress, completed, failed):
        from housekeeper.batch_updates import UpdateBatch

        batch = UpdateBatch(self._provider, list(self.inventory))
        self._submit(
            None,
            lambda worker: worker.check(lambda *args: self.dispatch(progress, *args)),
            completed,
            failed,
            preserve_error=True,
            worker=batch,
        )

    def execute_updates(self, items, progress, completed):
        from housekeeper.batch_updates import UpdateBatch

        batch = UpdateBatch(self._provider, list(self.inventory))
        self._submit(
            None,
            lambda worker: worker.execute(items, lambda *args: self.dispatch(progress, *args)),
            completed,
            lambda error: completed(self._failure(error)),
            preserve_error=True,
            worker=batch,
        )

    def execute(self, app, plan, progress, completed):
        def run(provider):
            # Repeat ownership and shared-file checks against a fresh inventory before file removal.
            if app.provider == "appimage":
                current, *_ = collect()
                match = next((item for item in current if item.key == app.key), None)
                if match is None:
                    raise ManagementError(
                        "The application changed. Refresh and review a new preview."
                    )
                fresh = provider.prepare(match, current)
                if fresh.fingerprint != plan.fingerprint:
                    raise ManagementError("The files changed. Review a new preview.")
            return provider.execute(app, plan, lambda *args: self.dispatch(progress, *args))

        self._submit(
            app, run, completed, lambda error: completed(self._failure(error)), preserve_error=True
        )

    def change_icon(self, app, entry, image, completed, failed):
        from housekeeper.appearance import save_icon

        self._submit(
            app, lambda _worker: save_icon(entry, image), completed, failed, worker=object()
        )

    @staticmethod
    def _failure(error):
        outcome = Outcome.CANCELLED if isinstance(error, OperationCancelled) else Outcome.FAILED
        return OperationResult(outcome, str(error))

    def _submit(self, app, run, completed, failed, preserve_error=False, worker=None):
        def failure(error):
            self.dispatch(failed, error if preserve_error else str(error))

        if self.closed or self.busy or self.scanning:
            failure(ManagementError("Wait for the current scan or operation to finish."))
            return
        self.busy = True
        try:
            self.active_provider = worker if worker is not None else self._provider(app)
            future = self.executor.submit(run, self.active_provider)
        except Exception as error:
            self.busy, self.active_provider = False, None
            failure(error)
            return

        def done(result):
            self.busy, self.active_provider = False, None
            try:
                outcome = result.result()
            except Exception as error:
                LOG.warning(
                    "Management operation failed for %s",
                    app.identity if app else "update batch",
                    exc_info=True,
                )
                failure(error)
                return
            if isinstance(outcome, OperationResult):
                LOG.log(
                    logging.INFO if outcome.outcome == Outcome.SUCCESS else logging.WARNING,
                    "Operation result for %s: %s; %s; completed=%s; errors=%s",
                    app.identity if app else "update batch",
                    outcome.outcome.value,
                    outcome.message,
                    outcome.completed,
                    outcome.errors,
                )
            self.dispatch(completed, outcome)

        future.add_done_callback(done)

    def cancel(self):
        if self.active_provider and hasattr(self.active_provider, "request_cancel"):
            self.active_provider.request_cancel()

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
