from datetime import datetime, time, timezone
from types import SimpleNamespace

from app.state import lock_windows


def _entry(dow, lock_at, unlock_at):
    return SimpleNamespace(day_of_week=dow, lock_at=lock_at, unlock_at=unlock_at)


def test_windows_cross_midnight_in_device_timezone():
    # Wednesday 2026-01-14 12:00 UTC; London is UTC+0 in January.
    now = datetime(2026, 1, 14, 12, tzinfo=timezone.utc)
    windows, until = lock_windows([_entry(2, time(21, 30), time(7, 0))], "Europe/London", now)
    assert windows[0] == {"start": "2026-01-14T21:30:00+00:00", "end": "2026-01-15T07:00:00+00:00"}
    assert len(windows) == 2  # this Wednesday and next
    assert until == datetime(2026, 1, 28, 12, tzinfo=timezone.utc)


def test_windows_follow_dst():
    # New York: UTC-5 in January, UTC-4 in July.
    entries = [_entry(d, time(22, 0), time(6, 0)) for d in range(7)]
    jan, _ = lock_windows(entries, "America/New_York", datetime(2026, 1, 10, 17, tzinfo=timezone.utc))
    jul, _ = lock_windows(entries, "America/New_York", datetime(2026, 7, 10, 17, tzinfo=timezone.utc))
    assert jan[0]["start"].endswith("T03:00:00+00:00")
    assert jul[0]["start"].endswith("T02:00:00+00:00")


def test_window_in_progress_is_included():
    # Tuesday 02:00 London time, inside Monday night's window.
    now = datetime(2026, 1, 13, 2, tzinfo=timezone.utc)
    windows, _ = lock_windows([_entry(0, time(21, 0), time(7, 0))], "Europe/London", now)
    assert windows[0] == {"start": "2026-01-12T21:00:00+00:00", "end": "2026-01-13T07:00:00+00:00"}


def test_same_day_window():
    now = datetime(2026, 1, 12, 9, tzinfo=timezone.utc)  # Monday
    windows, _ = lock_windows([_entry(0, time(13, 0), time(15, 0))], "UTC", now)
    assert windows[0] == {"start": "2026-01-12T13:00:00+00:00", "end": "2026-01-12T15:00:00+00:00"}
