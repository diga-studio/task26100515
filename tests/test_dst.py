"""DST edge cases: spring-forward gap, fall-back overlap, no drift."""

from datetime import date, datetime, time, timezone

from evergreen_calendar import EvergreenCalendar
from evergreen_calendar.timeutil import local_to_utc


def test_spring_forward_nonexistent_time_shifts_by_gap():
    # 2024-03-10 02:30 does not exist in America/Chicago (02:00 -> 03:00).
    # It must map to 08:30 UTC (shifted forward by the skipped hour).
    assert local_to_utc(date(2024, 3, 10), time(2, 30)) == datetime(
        2024, 3, 10, 8, 30, tzinfo=timezone.utc
    )


def test_fall_back_ambiguous_time_picks_earlier_utc():
    # 2024-11-03 01:30 happens twice; the earlier UTC instant is 06:30.
    assert local_to_utc(date(2024, 11, 3), time(1, 30)) == datetime(
        2024, 11, 3, 6, 30, tzinfo=timezone.utc
    )


def test_spring_rotation_keeps_local_dates_and_clock(tmp_path):
    cal = EvergreenCalendar(str(tmp_path / "t.db"))
    cal.register_plan("spring", date(2024, 3, 3), time(2, 30), 7)
    cal.close()

    cal = EvergreenCalendar(str(tmp_path / "t.db"))
    result = cal.query(
        datetime(2024, 3, 1, tzinfo=timezone.utc),
        datetime(2024, 4, 1, tzinfo=timezone.utc),
    )
    cal.close()

    got = [(it.orig_date, it.publish_utc) for it in result.items]
    assert got == [
        (date(2024, 3, 3), datetime(2024, 3, 3, 8, 30, tzinfo=timezone.utc)),
        (date(2024, 3, 10), datetime(2024, 3, 10, 8, 30, tzinfo=timezone.utc)),
        (date(2024, 3, 17), datetime(2024, 3, 17, 7, 30, tzinfo=timezone.utc)),
        (date(2024, 3, 24), datetime(2024, 3, 24, 7, 30, tzinfo=timezone.utc)),
        (date(2024, 3, 31), datetime(2024, 3, 31, 7, 30, tzinfo=timezone.utc)),
    ]
    assert result.total_count == 5


def test_fall_rotation_keeps_local_dates_and_clock(tmp_path):
    cal = EvergreenCalendar(str(tmp_path / "t.db"))
    cal.register_plan("fall", date(2024, 10, 27), time(1, 30), 7)
    result = cal.query(
        datetime(2024, 10, 1, tzinfo=timezone.utc),
        datetime(2024, 12, 1, tzinfo=timezone.utc),
    )
    cal.close()

    got = [(it.orig_date, it.publish_utc) for it in result.items]
    assert got == [
        (date(2024, 10, 27), datetime(2024, 10, 27, 6, 30, tzinfo=timezone.utc)),
        (date(2024, 11, 3), datetime(2024, 11, 3, 6, 30, tzinfo=timezone.utc)),
        (date(2024, 11, 10), datetime(2024, 11, 10, 7, 30, tzinfo=timezone.utc)),
        (date(2024, 11, 17), datetime(2024, 11, 17, 7, 30, tzinfo=timezone.utc)),
        (date(2024, 11, 24), datetime(2024, 11, 24, 7, 30, tzinfo=timezone.utc)),
    ]
    assert result.total_count == 5


def test_thirty_day_interval_is_not_a_month(tmp_path):
    cal = EvergreenCalendar(str(tmp_path / "t.db"))
    cal.register_plan("p", date(2024, 1, 31), time(9, 0), 30)
    result = cal.query(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 5, 1, tzinfo=timezone.utc),
    )
    cal.close()
    assert [it.orig_date for it in result.items] == [
        date(2024, 1, 31),
        date(2024, 3, 1),
        date(2024, 3, 31),
        date(2024, 4, 30),
    ]
