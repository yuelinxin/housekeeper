"""What Housekeeper is doing now and what it did recently in this session."""

import time
from dataclasses import dataclass, field, replace
from itertools import count

from housekeeper.i18n import _, ngettext
from housekeeper.models import Outcome

RECENT_LIMIT = 10


@dataclass(frozen=True)
class Activity:
    id: int
    kind: str
    # Matches the operation to the page it was started from, such as an app's key.
    key: str
    # What it acts on, such as an app name; empty for whole-system work.
    subject: str
    # The running title, such as "Installing Firefox", then the result's heading.
    title: str
    detail: str = ""
    fraction: float | None = None
    # Whether a Cancel request would be accepted now.
    cancellable: bool = False
    started: float = 0.0
    finished: float | None = None
    outcome: Outcome | None = None


@dataclass
class ActivityLog:
    """Running activities and the most recent finished ones, newest first.

    Listeners are called with the log after every change; `structural` is False when
    only a running activity's progress changed, so a view can update rows in place.
    """

    clock: object = time.time
    running: list[Activity] = field(default_factory=list)
    recent: list[Activity] = field(default_factory=list)
    listeners: list = field(default_factory=list)
    _ids: object = field(default_factory=lambda: count(1))

    def connect(self, listener):
        self.listeners.append(listener)
        return listener

    def disconnect(self, listener):
        if listener in self.listeners:
            self.listeners.remove(listener)

    def _changed(self, structural=True):
        for listener in list(self.listeners):
            listener(self, structural)

    def start(self, kind, title, subject="", key=""):
        activity = Activity(
            next(self._ids), kind, key or kind, subject, title, started=self.clock()
        )
        self.running.insert(0, activity)
        self._changed()
        return activity.id

    def get(self, activity_id):
        return next((a for a in self.running if a.id == activity_id), None)

    def update(self, activity_id, **changes):
        activity = self.get(activity_id)
        if activity is None:
            return
        updated = replace(activity, **changes)
        if updated != activity:
            self.running[self.running.index(activity)] = updated
            self._changed(structural=False)

    def finish(self, activity_id, outcome=None, title="", detail=""):
        """Move an activity to the recent list, or drop it when it has no outcome."""
        activity = self.get(activity_id)
        if activity is None:
            return
        self.running.remove(activity)
        if outcome is not None:
            done = replace(
                activity,
                title=title or activity.title,
                detail=detail,
                fraction=None,
                cancellable=False,
                finished=self.clock(),
                outcome=outcome,
            )
            self.recent = [done, *self.recent][:RECENT_LIMIT]
        self._changed()

    def clear(self):
        if self.recent:
            self.recent = []
            self._changed()


def elapsed(finished, now):
    """When something finished, relative to now while recent."""
    minutes = int(max(0, now - finished) // 60)
    if minutes < 1:
        return _("Just now")
    if minutes < 60:
        return ngettext("%d minute ago", "%d minutes ago", minutes) % minutes
    hours = minutes // 60
    if hours < 24:
        return ngettext("%d hour ago", "%d hours ago", hours) % hours
    return time.strftime("%x", time.localtime(finished))
