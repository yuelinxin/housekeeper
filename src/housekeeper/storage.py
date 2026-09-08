"""Read local storage estimates without guessing ownership of shared data."""

import logging
import stat
from dataclasses import dataclass
from pathlib import Path

from housekeeper.models import Source

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageUsage:
    software: int | None = None


def _software_size(app):
    from housekeeper.providers.packages import PACKAGE_SOURCES, installed_size

    if app.source in PACKAGE_SOURCES:
        return installed_size(app)
    if app.source == Source.APPIMAGE and app.location:
        info = Path(app.location).stat()
        return info.st_size if stat.S_ISREG(info.st_mode) else None
    if app.source == Source.FLATPAK:
        from housekeeper.providers.flatpak import configured_installations, load_flatpak

        installations, _warnings = configured_installations(load_flatpak())
        for installation in installations:
            if installation.get_path().get_path() != app.metadata.get("installation"):
                continue
            for ref in installation.list_installed_refs(None):
                if ref.format_ref() == app.identity:
                    if app.metadata.get("commit") != ref.get_commit():
                        return None
                    return ref.get_installed_size()
    elif app.source == Source.DEB:
        from housekeeper.providers.deb import installed_size

        return installed_size(app)
    elif app.source == Source.RPM:
        from housekeeper.providers.rpm import RpmIndex

        index = RpmIndex()
        if not index.available or not app.metadata.get("name"):
            return None
        for header in index.ts.dbMatch("name", app.metadata["name"]):
            version = f"{header['version']}-{header['release']}"
            epoch = str(header["epoch"] or 0)
            if epoch != "0":
                version = epoch + ":" + version
            if header["arch"] == app.metadata.get("arch") and version == app.version:
                return header["size"]
    return None


def measure_storage(app):
    software = None
    try:
        value = _software_size(app)
        if isinstance(value, int) and value >= 0:
            software = value
    except Exception:
        LOG.debug("Software size unavailable for %s", app.key, exc_info=True)
    return StorageUsage(software)
