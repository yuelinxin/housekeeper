"""Conservative command classification; parsing never launches an application."""

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from housekeeper.launch import parse_launch
from housekeeper.models import (
    Action,
    AppRecord,
    AttributionResult,
    AttributionState,
    DesktopEntry,
    Source,
)

BROWSERS = {"google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"}


def digest(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def unwrap_env(argv: tuple[str, ...]) -> tuple[str, ...]:
    return parse_launch(argv).argv


def option(argv: tuple[str, ...], key: str) -> str:
    for index, arg in enumerate(argv):
        if arg.startswith(key + "="):
            return arg[len(key) + 1 :]
        if arg == key and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def classify(entry: DesktopEntry) -> AppRecord:
    app = AppRecord(
        key=digest("desktop", entry.desktop_id),
        name=entry.name,
        entries=[entry],
        icon=entry.icon,
        visible=entry.visible,
        status=entry.reason,
        identity=entry.desktop_id,
    )
    launch = entry.launch or parse_launch(entry.argv)
    argv = launch.argv
    if launch.reason:
        app.attribution = AttributionResult(AttributionState.UNSUPPORTED, reason=launch.reason)
        app.metadata["management_reason"] = launch.reason
        return app
    if not argv:
        return app
    binary = Path(argv[0]).name
    if binary == "flatpak" and (
        any(arg.startswith("steam://rungameid/") for arg in argv)
        or option(argv, "--command") == "firefoxpwa"
    ):
        is_game = any(arg.startswith("steam://rungameid/") for arg in argv)
        app.source = Source.STEAM if is_game else Source.WEB
        app.provider, app.action = "external-wrapper", Action.INSTRUCTIONS
        app.metadata["management_reason"] = (
            "Open the sandboxed manager that created this entry to manage the application. "
            "Its host Flatpak is a separate application."
        )
        return app
    app_id = option(argv, "--app-id")
    if (
        (binary in BROWSERS and (app_id or option(argv, "--app")))
        or (binary == "flatpak" and app_id)
    ) and not (binary in BROWSERS and re.fullmatch(r"[a-p]{32}", app_id)):
        app.source, app.provider, app.action = Source.WEB, "browser-wrapper", Action.INSTRUCTIONS
        app.metadata["management_reason"] = (
            "Open the browser that installed this web app and use its app management menu. "
            "This launcher cannot be mapped to a direct management link."
        )
        return app
    if binary in BROWSERS and re.fullmatch(r"[a-p]{32}", app_id):
        profile = option(argv, "--profile-directory")
        data_dir = option(argv, "--user-data-dir")
        app.source, app.provider, app.action = Source.WEB, "chrome", Action.CHROME
        app.identity = app_id
        app.metadata = {
            "browser": entry.executable or argv[0],
            "profile": profile,
            "user_data_dir": data_dir,
        }
        app.key = digest("chrome", entry.executable or argv[0], profile, data_dir, app_id)
        args = [entry.executable or argv[0]]
        if profile:
            args.append("--profile-directory=" + profile)
        if data_dir:
            args.append("--user-data-dir=" + data_dir)
        app.management = (*args, "chrome://apps")
    elif binary == "firefoxpwa" and len(argv) >= 4 and argv[1:3] == ("site", "launch"):
        app.source, app.provider, app.action = Source.WEB, "firefoxpwa", Action.INSTRUCTIONS
        app.identity = argv[3]
        app.key = digest("firefoxpwa", entry.executable, app.identity)
    elif binary == "steam" and any(re.fullmatch(r"steam://rungameid/[0-9]+", a) for a in argv):
        game = next(
            a.rsplit("/", 1)[1] for a in argv if re.fullmatch(r"steam://rungameid/[0-9]+", a)
        )
        app.source, app.provider, app.action = Source.STEAM, "steam", Action.STEAM
        app.identity = game
        app.management = ("steam://open/games",)
        app.key = digest("steam", game)
    elif binary.casefold().endswith(".appimage") and Path(argv[0]).is_absolute():
        app.source, app.provider = Source.APPIMAGE, "appimage"
        app.location = entry.resolved_executable or argv[0]
        app.key = digest("appimage", app.location, argv[1:])
    elif binary in {"steam", "firefoxpwa"} and len(argv) > 1 and argv[1] not in {"%u", "%U"}:
        app.provider, app.action = binary, Action.INSTRUCTIONS
        app.source = Source.STEAM if binary == "steam" else Source.WEB
        app.metadata["management_reason"] = (
            "Open the original application's manager to review this entry."
        )
    return app


def merge_records(records: list[AppRecord]) -> list[AppRecord]:
    grouped: dict[str, AppRecord] = {}
    for app in records:
        existing = grouped.get(app.key)
        if existing is None:
            grouped[app.key] = deepcopy(app)
            continue
        known = {e.path for e in existing.entries}
        existing.entries.extend(e for e in app.entries if e.path not in known)
        if app.visible and not existing.visible:
            existing.name, existing.icon, existing.status = app.name, app.icon, app.status
        existing.visible = existing.visible or app.visible
        if existing.component:
            existing.component = replace(
                existing.component,
                launcher_ids=tuple(sorted({e.desktop_id for e in existing.entries})),
            )
    return sorted(grouped.values(), key=lambda a: (a.name.casefold(), a.key))
