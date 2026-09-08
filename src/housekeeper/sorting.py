"""Inventory ordering and validation of optional package timestamps."""

from datetime import datetime

from housekeeper.models import AppRecord


def package_timestamp(value):
    """Accept package timestamps, never infer installation from file times."""
    if isinstance(value, str):
        if value.isascii() and value.isdecimal():
            if len(value) > 12:
                return None
            value = int(value)
        else:
            try:
                date = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if date.tzinfo is None:
                    return None
                value = int(date.timestamp())
            except (ValueError, OverflowError, OSError):
                return None
    return value if type(value) is int and 0 < value < 253402300800 else None


def sort_key(app: AppRecord, mode: str, name_key: str | None = None) -> tuple:
    """Largest/newest first, unknown last, with deterministic alphabetical ties."""
    name = (app.name.casefold() if name_key is None else name_key, app.key)
    if mode == "size":
        value = app.software_size
    elif mode == "installed":
        value = app.updated_at
    else:
        return name
    return (value is None, -value if value is not None else 0, *name)
