"""UI-independent inventory and operation contracts."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol


class Source(str, Enum):
    RPM = "rpm"
    FLATPAK = "flatpak"
    WEB = "web"
    APPIMAGE = "appimage"
    STEAM = "steam"
    OTHER = "other"


class Action(str, Enum):
    NONE = "none"
    UNINSTALL = "uninstall"
    TRASH = "trash"
    CHROME = "chrome"
    STEAM = "steam"
    INSTRUCTIONS = "instructions"


@dataclass(frozen=True)
class DesktopEntry:
    desktop_id: str
    path: Path
    name: str
    command: str = ""
    argv: tuple[str, ...] = ()
    icon: str = "application-x-executable"
    visible: bool = True
    reason: str = ""
    executable: str = ""
    resolved_executable: str = ""
    flatpak_id: str = ""
    dbus_activatable: bool = False
    root: Path = Path("/")


@dataclass(frozen=True)
class ProviderCapabilities:
    query: bool = True
    preview: bool = False
    execute: bool = False
    reason: str = ""


@dataclass
class AppRecord:
    key: str
    name: str
    source: Source = Source.OTHER
    provider: str = "other"
    entries: list[DesktopEntry] = field(default_factory=list)
    icon: str = "application-x-executable"
    version: str = ""
    scope: str = "Unknown"
    identity: str = ""
    location: str = ""
    origin: str = ""
    visible: bool = True
    status: str = ""
    action: Action = Action.NONE
    management: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def search_text(self) -> str:
        parts = [self.name, self.identity, self.location, self.source.value, self.scope]
        parts.extend(
            self.metadata.get(key, "")
            for key in ("name", "app_id", "installation", "user_data_dir", "profile")
        )
        for entry in self.entries:
            parts.extend(
                (entry.desktop_id, str(entry.path), entry.executable, entry.resolved_executable)
            )
        return " ".join(parts).casefold()


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    mode: int
    uid: int


@dataclass(frozen=True)
class RemovalPlan:
    app_key: str
    provider: str
    target: str
    affected: tuple[str, ...]
    message: str
    fingerprint: str
    files: tuple[FileSnapshot, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()


class Outcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


@dataclass(frozen=True)
class OperationResult:
    outcome: Outcome
    message: str
    completed: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class Progress(Protocol):
    def __call__(self, message: str, fraction: float | None, cancellable: bool) -> None: ...


class Provider(Protocol):
    """Private provider interface, not a third-party plugin API."""

    def capabilities(self) -> ProviderCapabilities: ...
    def prepare(self, app: AppRecord, inventory: list[AppRecord]) -> RemovalPlan: ...
    def execute(self, app: AppRecord, plan: RemovalPlan, progress: Progress) -> OperationResult: ...


class ManagementError(Exception):
    """An actionable error that is safe to display without a traceback."""
