"""Single-occurrence adjustments: cancel, move, re-adjust, collisions."""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from evergreen_calendar import EvergreenCalendar

UTC = timezone.utc
WEEK = timedelta(days=7)


def _occurrence_utc(start: date, k: int, wall: time) -> datetime:
    from evergreen_calendar.timeutil import local_to_utc

    return local_to_utc(start + k * WEEK, wall)


@pytest.fixture
def cal(tmp_path):
    c = EvergreenCalendar(str(tmp_path / "t.db"))
    # Weekly plan, Mondays 09:00 Chicago, no end date.
    c.register_plan("alpha", date(2024, 1, 1), time(9, 0), 7)
    # Weekly plan, Mondays 09:00 Chicago, ends 2024-03-01 (exclusive).
    c.register_plan("beta", date(2024, 1, 1), time(9, 0), 7, end_date=date(2024, 3, 1))
    yield c
    c.close()


def test_cancel_removes_occurrence(cal):
    cal.cancel_occurrence("alpha", date(2024, 1, 8))
    r = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC))
    dates = [it.orig_date for it in r.items if it.plan_id == "alpha"]
    assert date(2024, 1, 8) not in dates
    # alpha: Jan 1/8/15/22/29 -> 4 remain; beta: Jan 1/8/15/22/29 -> 5
    assert r.total_count == 4 + 5


def test_move_out_of_window_excludes(cal):
    # Move Jan 8 occurrence far into the future: window loses it.
    cal.reschedule_occurrence(
        "alpha", date(2024, 1, 8), datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    )
    r = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC))
    assert all(
        not (it.plan_id == "alpha" and it.orig_date == date(2024, 1, 8))
        for it in r.items
    )
    assert r.total_count == 4 + 5


def test_move_into_window_counts_once(cal):
    # Jan 8 occurrence moved to Feb 5: out of the January window,
    # into the February window. Each window sees it at most once.
    target = datetime(2024, 2, 5, 17, 0, tzinfo=UTC)
    cal.reschedule_occurrence("alpha", date(2024, 1, 8), target)

    r_jan = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC))
    assert r_jan.total_count == 4 + 5

    r_feb = cal.query(datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC))
    hits = [
        it for it in r_feb.items
        if it.plan_id == "alpha" and it.orig_date == date(2024, 1, 8)
    ]
    assert len(hits) == 1
    assert hits[0].publish_utc == target
    # alpha Feb originals: 5/12/19/26 -> 4, plus the moved-in one -> 5;
    # beta Feb originals: 5/12/19/26 -> 4.
    assert r_feb.total_count == 5 + 4


def test_move_beyond_plan_end_date_keeps_identity(cal):
    # beta ends 2024-03-01; move its Feb 5 occurrence to May.
    target = datetime(2024, 5, 10, 3, 0, tzinfo=UTC)
    cal.reschedule_occurrence("beta", date(2024, 2, 5), target)
    r = cal.query(datetime(2024, 5, 1, tzinfo=UTC), datetime(2024, 6, 1, tzinfo=UTC))
    hits = [it for it in r.items if it.plan_id == "beta"]
    assert len(hits) == 1
    assert hits[0].orig_date == date(2024, 2, 5)
    assert hits[0].publish_utc == target
    assert r.total_count == 1 + 4  # + alpha's May Mondays (6/13/20/27)


def test_last_adjustment_wins_cancel_then_reschedule(cal):
    cal.cancel_occurrence("alpha", date(2024, 1, 8))
    target = datetime(2024, 1, 20, 0, 0, tzinfo=UTC)
    cal.reschedule_occurrence("alpha", date(2024, 1, 8), target)
    r = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC))
    hits = [
        it for it in r.items
        if it.plan_id == "alpha" and it.orig_date == date(2024, 1, 8)
    ]
    assert len(hits) == 1
    assert hits[0].publish_utc == target
    assert r.total_count == 5 + 5


def test_last_adjustment_wins_reschedule_then_cancel(cal):
    cal.reschedule_occurrence(
        "alpha", date(2024, 1, 8), datetime(2024, 1, 20, 0, 0, tzinfo=UTC)
    )
    cal.cancel_occurrence("alpha", date(2024, 1, 8))
    r = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC))
    assert all(
        not (it.plan_id == "alpha" and it.orig_date == date(2024, 1, 8))
        for it in r.items
    )
    assert r.total_count == 4 + 5


def test_collision_keeps_both_and_stable_order(cal):
    # Move alpha's Jan 8 occurrence onto beta's Jan 15 occurrence instant.
    crash = _occurrence_utc(date(2024, 1, 1), 2, time(9, 0))  # Jan 15 09:00 CST
    cal.reschedule_occurrence("alpha", date(2024, 1, 8), crash)
    r = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC))
    at_crash = [it for it in r.items if it.publish_utc == crash]
    assert [(it.plan_id, it.orig_date) for it in at_crash] == [
        ("alpha", date(2024, 1, 8)),
        ("alpha", date(2024, 1, 15)),
        ("beta", date(2024, 1, 15)),
    ]
    # Nothing lost: alpha 5 (Jan 8 moved to Jan 15 instant), beta 5.
    assert r.total_count == 5 + 5


def test_adjustment_on_nonexistent_occurrence_is_inert(cal):
    # 2024-01-09 is not a scheduled date for alpha (weekly from Jan 1).
    cal.cancel_occurrence("alpha", date(2024, 1, 9))
    cal.reschedule_occurrence(
        "alpha", date(2024, 1, 10), datetime(2024, 1, 11, 0, 0, tzinfo=UTC)
    )
    # beta has no occurrence on/after its end date.
    cal.reschedule_occurrence(
        "beta", date(2024, 3, 4), datetime(2024, 3, 5, 0, 0, tzinfo=UTC)
    )
    r = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 4, 1, tzinfo=UTC))
    assert all(
        not (it.plan_id == "alpha" and it.orig_date == date(2024, 1, 10))
        for it in r.items
    )
    assert all(
        not (it.plan_id == "beta" and it.orig_date == date(2024, 3, 4))
        for it in r.items
    )


def test_unknown_plan_adjustment_raises(cal):
    with pytest.raises(KeyError):
        cal.cancel_occurrence("nope", date(2024, 1, 1))
    with pytest.raises(KeyError):
        cal.reschedule_occurrence(
            "nope", date(2024, 1, 1), datetime(2024, 1, 2, tzinfo=UTC)
        )
