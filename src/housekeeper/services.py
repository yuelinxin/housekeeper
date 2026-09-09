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
    UpdateAction,
)

LOG = logging.getLogger(__name__)


def collect(partial=None, roots=None, *, indexes=None):
    from copy import deepcopy

    from housekeeper.attribution import attribute
    from housekeeper.inventory import (
        assign_actions,
        discover,
        discovery_indexes,
        installation_record,
    )
    from housekeeper.providers.appimage import AppImageProvider
    from housekeeper.providers.flatpak import FlatpakProvider
    from housekeeper.providers.rpm import RpmProvider

    if partial:
        initial, _ = scan_entries(roots)
        provisional = [classify(entry) for entry in initial]
        for app in provisional:
            app.action = Action.NONE
            app.update_action = UpdateAction.INSTRUCTIONS
        partial(deepcopy(merge_records(provisional)))
    indexes = discovery_indexes() if indexes is None else tuple(indexes)
    snapshots = [discover(index) for index in indexes]
    scan_roots = (
        list(roots)
        if roots is not None
        else list(
            dict.fromkeys(
                [*application_roots(), *(root for snapshot in snapshots for root in snapshot.roots)]
            )
        )
    )
    entries, warnings = scan_entries(scan_roots)
    records = [attribute(classify(entry), indexes) for entry in entries]
    for index in indexes:
        # Apply XDG visibility separately from association and authorization.
        for app in getattr(index, "apps", ()):
            hidden = next(
                (
                    entry
                    for entry in entries
                    if "Hidden by a desktop entry override" in entry.reason
                    and entry.desktop_id == app.metadata.get("app_id", "") + ".desktop"
                ),
                None,
            )
            copy = deepcopy(app)
            if hidden:
                copy.visible, copy.status = False, hidden.reason
            bound = any(
                record.installation
                and record.installation.context == app.metadata.get("installation")
                and record.installation.identity == app.identity
                for record in records
            )
            if not bound:
                records.append(installation_record(copy))
        warnings.extend(getattr(index, "warnings", ()))
    output = merge_records(records)
    image_provider = AppImageProvider()
    capabilities = {
        "rpm": RpmProvider().capabilities(),
        "flatpak": FlatpakProvider().capabilities(),
    }
    for record in output:
        assign_actions(record, capabilities)
        if record.attribution:
            warnings.extend(record.attribution.errors)
        if record.scope == "Unknown" and record.entries:
            record.metadata["entry_scope"] = (
                "User" if record.entries[0].path.is_relative_to(Path.home()) else "System"
            )
        if record.source == Source.APPIMAGE:
            from housekeeper.storage import measure_storage

            record.software_size = measure_storage(record).software
            record.scope = (
                "User" if Path(record.location).is_relative_to(Path.home()) else "Unknown"
            )
            try:
                image_provider.prepare(record, output)
                record.action = Action.TRASH
            except (ManagementError, OSError, RuntimeError) as error:
                record.action = Action.NONE
                record.metadata["management_reason"] = str(error)
        elif record.source == Source.OTHER and not record.metadata.get("management_reason"):
            record.metadata["management_reason"] = (
                "No supported package manager could establish this application's ownership."
            )
    monitors = list(
        dict.fromkeys(
            [*scan_roots, *(entry.path.parent for app in output for entry in app.entries)]
        )
    )
    installations = {
        path: installation
        for index in indexes
        for path, installation in getattr(index, "installations", {}).items()
    }
    return output, list(dict.fromkeys(warnings)), monitors, installations


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

    def check_updates(self, progress, completed, failed, *, providers=None):
        from housekeeper.batch_updates import UPDATE_PROVIDERS, UpdateBatch

        providers = UPDATE_PROVIDERS if providers is None else tuple(providers)
        batch = UpdateBatch(self._provider, list(self.inventory))
        self._submit(
            None,
            lambda worker: worker.check(lambda *args: self.dispatch(progress, *args), providers),
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

    def measure_storage(self, app, completed):
        from housekeeper.storage import StorageUsage, measure_storage

        if self.closed:
            return
        try:
            future = self.executor.submit(measure_storage, app)
        except RuntimeError:
            self.dispatch(completed, StorageUsage())
            return

        def done(result):
            if self.closed:
                return
            try:
                usage = result.result()
            except Exception:
                LOG.debug("Storage measurement failed", exc_info=True)
                usage = StorageUsage()
            self.dispatch(completed, usage)

        future.add_done_callback(done)

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
