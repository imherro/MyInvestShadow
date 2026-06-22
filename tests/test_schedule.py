from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from shadow_app.main import _schedule_slot_key


SHANGHAI = ZoneInfo("Asia/Shanghai")
SLOTS = ("21:10", "21:40", "22:10")


def test_schedule_waits_until_first_evening_slot() -> None:
    now = datetime(2026, 6, 22, 21, 9, tzinfo=SHANGHAI)

    assert _schedule_slot_key(now, None, set(), SLOTS) is None


def test_schedule_uses_latest_due_slot_once() -> None:
    now = datetime(2026, 6, 22, 21, 41, tzinfo=SHANGHAI)
    attempted = {"2026-06-22:21:10"}

    assert _schedule_slot_key(now, None, attempted, SLOTS) == "2026-06-22:21:40"


def test_schedule_stops_after_successful_day() -> None:
    now = datetime(2026, 6, 22, 22, 11, tzinfo=SHANGHAI)

    assert _schedule_slot_key(now, "2026-06-22", set(), SLOTS) is None


def test_schedule_ignores_attempted_slot() -> None:
    now = datetime(2026, 6, 22, 22, 12, tzinfo=SHANGHAI)
    attempted = {"2026-06-22:22:10"}

    assert _schedule_slot_key(now, None, attempted, SLOTS) is None
