from housekeeper.activity import RECENT_LIMIT, ActivityLog, elapsed
from housekeeper.models import Outcome


def clock(times):
    values = iter(times)
    return lambda: next(values)


def test_running_activity_updates_then_moves_to_recent_with_its_outcome():
    log = ActivityLog(clock=clock([100, 160]))
    changes = []
    log.connect(lambda _log, structural: changes.append(structural))
    first = log.start("install", "Installing", "Firefox")
    log.update(first, title="Installing Firefox", detail="Downloading", fraction=0.5)
    # An unchanged update notifies nobody.
    log.update(first, detail="Downloading", fraction=0.5)
    assert log.running[0].title == "Installing Firefox" and log.running[0].fraction == 0.5
    log.finish(first, Outcome.SUCCESS, "Installation Complete", "The package was installed.")
    assert not log.running
    done = log.recent[0]
    assert (done.title, done.subject, done.outcome) == (
        "Installation Complete",
        "Firefox",
        Outcome.SUCCESS,
    )
    assert done.detail == "The package was installed." and done.fraction is None
    assert (done.started, done.finished) == (100, 160)
    assert changes == [True, False, True]


def test_work_without_an_outcome_is_not_kept_and_unknown_ids_are_ignored():
    log = ActivityLog(clock=lambda: 0)
    scan = log.start("scan", "Refreshing App List")
    removal = log.start("remove", "Preparing Removal", "Boxes")
    # Newest first.
    assert [a.id for a in log.running] == [removal, scan]
    log.finish(removal)
    log.finish(scan)
    log.finish(None)
    log.update(None, detail="ignored")
    assert log.running == [] and log.recent == []


def test_recent_keeps_the_newest_results_and_can_be_cleared():
    log = ActivityLog(clock=lambda: 0)
    for index in range(RECENT_LIMIT + 2):
        log.finish(log.start("update", str(index)), Outcome.FAILED)
    assert [a.title for a in log.recent][:2] == [str(RECENT_LIMIT + 1), str(RECENT_LIMIT)]
    assert len(log.recent) == RECENT_LIMIT
    changes = []
    log.connect(lambda _log, structural: changes.append(structural))
    log.clear()
    log.clear()
    assert log.recent == [] and changes == [True]


def test_elapsed_time_reads_relative_while_recent():
    assert elapsed(1000, 1030) == "Just now"
    assert elapsed(1000, 1000 + 60) == "1 minute ago"
    assert elapsed(1000, 1000 + 45 * 60) == "45 minutes ago"
    assert elapsed(1000, 1000 + 3 * 3600) == "3 hours ago"
    assert "ago" not in elapsed(1000, 1000 + 2 * 86400)


def test_activities_are_keyed_to_their_page_and_listeners_can_leave():
    log = ActivityLog(clock=lambda: 0)
    changes = []
    listener = log.connect(lambda _log, structural: changes.append(structural))
    app = log.start("remove", "Removing Boxes", "Boxes", "rpm:gnome-boxes")
    check = log.start("update", "Checking for Updates")
    assert (log.get(app).key, log.get(check).key) == ("rpm:gnome-boxes", "update")
    log.update(app, cancellable=True)
    assert log.get(app).cancellable
    log.disconnect(listener)
    log.disconnect(listener)
    log.finish(app, Outcome.CANCELLED)
    # A finished activity can no longer be cancelled.
    assert not log.recent[0].cancellable
    assert changes == [True, True, False]
