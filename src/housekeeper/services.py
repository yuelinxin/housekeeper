"""Inventory orchestration and serialized management operations."""

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from housekeeper.discovery import application_roots, scan_entries
from housekeeper.identity import classify, merge_records
from housekeeper.models import Action, ManagementError, OperationResult, Outcome, Source

LOG = logging.getLogger(__name__)


def collect(partial=None, roots=None):
    entries, warnings = scan_entries(roots)
    records = [classify(entry) for entry in entries]
    if partial:
        partial(merge_records(records))
    from housekeeper.providers.flatpak import FlatpakIndex
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
    for record in output:
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

    def scan(self, partial, completed, failed):
        if self.scanning or self.busy:
            return False
        self.scanning = True
        future = self.executor.submit(collect, lambda apps: self.dispatch(partial, apps))

        def done(result):
            try:
                apps, warnings, roots, installations = result.result()
                self.inventory = apps
                self.dispatch(completed, apps, warnings, roots, installations)
            except Exception as error:
                LOG.debug("Inventory scan failed", exc_info=True)
                self.dispatch(failed, str(error))
            finally:
                self.scanning = False

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
        if self.busy or self.scanning:
            failed("Wait for the current scan or operation to finish.")
            return
        self.busy = True
        provider = self._provider(app)
        inventory = list(self.inventory)
        future = self.executor.submit(provider.prepare, app, inventory)

        def done(result):
            self.busy = False
            try:
                self.dispatch(completed, result.result())
            except Exception as error:
                LOG.debug("Removal preview failed", exc_info=True)
                self.dispatch(failed, str(error))

        future.add_done_callback(done)

    def execute(self, app, plan, progress, completed):
        if self.busy or self.scanning:
            completed(OperationResult(Outcome.FAILED, "Wait for the current task to finish."))
            return
        self.busy = True
        self.active_provider = self._provider(app)
        provider = self.active_provider

        def run():
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

        future = self.executor.submit(run)

        def done(result):
            self.busy, self.active_provider = False, None
            try:
                outcome = result.result()
            except Exception as error:
                LOG.debug("Management operation failed", exc_info=True)
                outcome = OperationResult(Outcome.FAILED, str(error))
            self.dispatch(completed, outcome)

        future.add_done_callback(done)

    def cancel(self):
        if self.active_provider and hasattr(self.active_provider, "request_cancel"):
            self.active_provider.request_cancel()

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
