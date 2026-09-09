"""Local update previews; providers must still revalidate every plan before execution."""

import json
import logging
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from housekeeper import VERSION
from housekeeper.batch_updates import UPDATE_PROVIDERS, UpdateItem, UpdateReport
from housekeeper.models import UpdateChange, UpdatePlan

LOG = logging.getLogger(__name__)
MAX_BYTES = 4 * 1024 * 1024
UPDATE_CACHE_TTL = 24 * 60 * 60


def cache_expired(checked_at, now=None, *, ttl=UPDATE_CACHE_TTL):
    return (time.time() if now is None else now) - checked_at >= ttl


def cache_path():
    root = Path(os.environ.get("XDG_CACHE_HOME", ""))
    if not root.is_absolute():
        root = Path.home() / ".cache"
    return root / "housekeeper/updates.json"


def snapshot(app):
    return json.loads(json.dumps(asdict(app), default=str))


@dataclass(frozen=True)
class CachedUpdates:
    report: UpdateReport
    checked_at: float
    stale: bool
    inventory_snapshot: dict


def decode_plan(data):
    data = dict(data)
    changes = []
    for change in data.pop("changes"):
        if not all(isinstance(change.get(f.name), str) for f in fields(UpdateChange)):
            raise ValueError("Invalid cached update change")
        changes.append(UpdateChange(**change))
    size = data.get("download_size")
    if size is not None and (type(size) is not int or size < 0):
        raise ValueError("Invalid cached download size")
    for field in fields(UpdatePlan):
        if field.name not in {"changes", "download_size"} and not isinstance(
            data.get(field.name), str
        ):
            raise ValueError("Invalid cached update plan")
    if not changes:
        raise ValueError("Empty cached update plan")
    return UpdatePlan(changes=tuple(changes), **data)


class UpdateCache:
    def __init__(self, path=None):
        self.path = path if path is not None else cache_path()

    def load(self, inventory, *, providers=UPDATE_PROVIDERS):
        try:
            with self.path.open("rb") as stream:
                payload = stream.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError("Update cache is too large")
            data = json.loads(payload)
            if data["schema"] != 2 or data["version"] != VERSION:
                return None
            if data.get("providers", sorted(UPDATE_PROVIDERS)) != sorted(providers):
                return None
            checked_at = data["checked_at"]
            if (
                type(checked_at) not in (int, float)
                or not math.isfinite(checked_at)
                or checked_at <= 0
                or checked_at > time.time() + 300
            ):
                raise ValueError("Invalid cache timestamp")
            current = {app.key: app for app in inventory}
            saved = data["inventory"]
            if not isinstance(saved, dict):
                raise ValueError("Invalid cached inventory")
            items, seen = [], set()
            for entry in data["items"]:
                key = entry["key"]
                app = current.get(key)
                if app is None or saved.get(key) != snapshot(app):
                    continue
                plan = decode_plan(entry["plan"])
                if plan.provider not in providers:
                    raise ValueError("Cached update belongs to a disabled provider")
                names = entry["names"]
                if (
                    key in seen
                    or plan.app_key != key
                    or plan.provider != app.provider
                    or not isinstance(names, list)
                    or not names
                    or not all(isinstance(name, str) for name in names)
                ):
                    raise ValueError("Invalid cached update item")
                seen.add(key)
                items.append(UpdateItem(app, plan, tuple(names)))
            unsupported = data["unsupported"]
            if type(unsupported) is not int or unsupported < 0:
                raise ValueError("Invalid unsupported count")
            stale = saved != {app.key: snapshot(app) for app in inventory}
            return CachedUpdates(
                UpdateReport(tuple(items), unsupported=unsupported), checked_at, stale, saved
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
            LOG.warning("Could not load update cache: %s", error)
            return None

    def save(self, report, inventory, checked_at, *, providers=UPDATE_PROVIDERS):
        self._save(
            report, {app.key: snapshot(app) for app in inventory}, checked_at, providers=providers
        )

    def reconcile(
        self, inventory, *, completed_keys=(), updated_keys=(), providers=UPDATE_PROVIDERS
    ):
        cached = self.load(inventory, providers=providers)
        if cached is None:
            return
        report = replace(
            cached.report,
            items=tuple(item for item in cached.report.items if item.app.key not in completed_keys),
        )
        saved = dict(cached.inventory_snapshot)
        current = {app.key: app for app in inventory}
        # Rebase only known updates; unrelated changes must still mark the cache stale.
        for key in updated_keys:
            if key in current:
                saved[key] = snapshot(current[key])
            else:
                saved.pop(key, None)
        self._save(report, saved, cached.checked_at, providers=providers)

    def _save(self, report, inventory_snapshot, checked_at, *, providers):
        if report.errors or report.cancelled:
            return
        temporary = None
        try:
            payload = json.dumps(
                {
                    "schema": 2,
                    "version": VERSION,
                    "checked_at": checked_at,
                    "providers": sorted(providers),
                    "inventory": inventory_snapshot,
                    "items": [
                        {"key": item.app.key, "plan": asdict(item.plan), "names": item.names}
                        for item in report.items
                    ],
                    "unsupported": report.unsupported,
                }
            ).encode("utf-8")
            if len(payload) > MAX_BYTES:
                self.clear()
                return
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            temporary.replace(self.path)
        except OSError as error:
            LOG.warning("Could not save update cache: %s", error)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as error:
                    LOG.warning("Could not remove temporary update cache: %s", error)

    def clear(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            LOG.warning("Could not clear update cache: %s", error)
