"""Window query semantics, validation, and brute-force cross-checks."""

import random
from datetime import date, datetime, time, timedelta, timezone

import pytest

from evergreen_calendar import EvergreenCalendar
from evergreen_calendar.timeutil import local_to_utc

UTC = timezone.utc


def test_register_plan_validation(tmp_path):
    cal = EvergreenCalendar(str(tmp_path / "t.db"))
    with pytest.raises(ValueError):
        cal.register_plan("bad", date(2024, 1, 1), time(9, 0), 8)
    with pytest.raises(ValueError):
        cal.register_plan("", date(2024, 1, 1), time(9, 0), 7)
    with pytest.raises(ValueError):
        cal.register_plan(
            "bad-end", date(2024, 1, 10), time(9, 0), 7, end_date=date(2024, 1, 1)
        )
    cal.register_plan("ok", date(2024, 1, 1), time(9, 0), 30)
    with pytest.raises(ValueError):
        cal.register_plan("ok", date(2024, 2, 1), time(9, 0), 7)
    cal.close()


def test_query_window_validation(tmp_path):
    cal = EvergreenCalendar(str(tmp_path / "t.db"))
    with pytest.raises(ValueError):
        cal.query(
            datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)
        )
    with pytest.raises(ValueError):
        cal.query(datetime(2024, 1, 1), datetime(2024, 2, 1))
    cal.close()


def test_window_is_half_open(tmp_path):
    cal = EvergreenCalendar(str(tmp_path / "t.db"))
    cal.register_plan("p", date(2024, 1, 1), time(9, 0), 7)
    # Jan 1 09:00 CST == 15:00 UTC exactly.
    edge = datetime(2024, 1, 1, 15, 0, tzinfo=UTC)
    # Left-closed: the occurrence exactly at the start is included.
    r = cal.query(edge, datetime(2024, 1, 8, 15, 0, tzinfo=UTC))
    assert [it.orig_date for it in r.items] == [date(2024, 1, 1)]
    # Right-open: the occurrence exactly at the end is excluded.
    r2 = cal.query(edge - timedelta(seconds=1), edge)
    assert r2.total_count == 0
    cal.close()


def test_items_capped_at_100(tmp_path):
    cal = EvergreenCalendar(str(tmp_path / "t.db"))
    for i in range(30):
        cal.register_plan(f"p{i:02d}", date(2024, 1, 1), time(9, 0), 7)
    r = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))
    assert len(r.items) == 100
    assert r.total_count > 100
    keys = [(it.publish_utc, it.plan_id, it.orig_date) for it in r.items]
    assert keys == sorted(keys)
    cal.close()


def _brute_force(plans, adjustments, ws, we):
    """Reference implementation: enumerate every occurrence."""
    adjusted = {(a[0], a[1]): a for a in adjustments}
    out = []
    for plan_id, start, wall, interval, end in plans:
        d = start
        while end is None or d < end:
            utc = local_to_utc(d, wall)
            if utc >= we:
                break
            if (plan_id, d) not in adjusted and ws <= utc < we:
                out.append((utc, plan_id, d))
            d += timedelta(days=interval)
    # Moved-in items may originate beyond the enumeration horizon above.
    by_id = {p[0]: p for p in plans}
    for plan_id, orig, action, target in adjustments:
        if action != "move":
            continue
        _, start, _, interval, end = by_id[plan_id]
        delta = (orig - start).days
        if delta < 0 or delta % interval != 0:
            continue
        if end is not None and orig >= end:
            continue
        if ws <= target < we:
            out.append((target, plan_id, orig))
    out.sort(key=lambda x: (x[0], x[1], x[2]))
    return out


def test_randomized_against_brute_force(tmp_path):
    rng = random.Random(20241005)
    cal = EvergreenCalendar(str(tmp_path / "t.db"))

    plans = []
    for i in range(40):
        plan_id = f"plan-{i:02d}"
        start = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 60))
        wall = time(rng.randrange(0, 24), rng.choice([0, 15, 30, 45]))
        interval = rng.choice([7, 14, 30])
        end = None
        if rng.random() < 0.4:
            end = start + timedelta(days=rng.randrange(30, 400))
        cal.register_plan(plan_id, start, wall, interval, end)
        plans.append((plan_id, start, wall, interval, end))

    adjustments = []
    for _ in range(120):
        plan_id, start, wall, interval, end = rng.choice(plans)
        k = rng.randrange(0, 20)
        orig = start + timedelta(days=k * interval)
        if rng.random() < 0.5:
            cal.cancel_occurrence(plan_id, orig)
            adjustments.append((plan_id, orig, "cancel", None))
        else:
            target = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(
                minutes=rng.randrange(0, 400 * 24 * 60)
            )
            cal.reschedule_occurrence(plan_id, orig, target)
            adjustments.append((plan_id, orig, "move", target))

    # Final state per occurrence is what the library sees (last write wins).
    final = {}
    for a in adjustments:
        final[(a[0], a[1])] = a

    for _ in range(15):
        ws = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(
            hours=rng.randrange(0, 24 * 300)
        )
        we = ws + timedelta(hours=rng.randrange(1, 24 * 120))
        expected = _brute_force(plans, list(final.values()), ws, we)
        result = cal.query(ws, we)
        assert result.total_count == len(expected)
        got = [(it.publish_utc, it.plan_id, it.orig_date) for it in result.items]
        assert got == expected[:100]
    cal.close()


def test_persistence_roundtrip_same_results(tmp_path):
    path = str(tmp_path / "t.db")
    cal = EvergreenCalendar(path)
    cal.register_plan("a", date(2024, 1, 1), time(2, 30), 7)
    cal.register_plan("b", date(2024, 1, 3), time(1, 30), 14, end_date=date(2025, 1, 1))
    cal.cancel_occurrence("a", date(2024, 3, 10))
    cal.reschedule_occurrence(
        "b", date(2024, 1, 17), datetime(2024, 3, 10, 8, 30, tzinfo=UTC)
    )
    ws = datetime(2024, 1, 1, tzinfo=UTC)
    we = datetime(2025, 6, 1, tzinfo=UTC)
    before = cal.query(ws, we)
    cal.close()

    reopened = EvergreenCalendar(path)
    after = reopened.query(ws, we)
    reopened.close()
    assert before == after
    assert before.total_count == after.total_count
