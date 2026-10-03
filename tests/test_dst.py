"""夏令时两种边界：春季跳时顺延、秋季回拨取较早 UTC，且后续轮转不漂移。"""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from evergreen_calendar import EvergreenCalendar

UTC = timezone.utc


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "cal.sqlite")


def test_spring_forward_nonexistent_time(db_path):
    # 2024-03-10 02:30 芝加哥不存在（02:00 跳到 03:00），顺延 1 小时
    cal = EvergreenCalendar(db_path)
    cal.register_plan("spring", date(2024, 3, 3), time(2, 30), 7)
    res = cal.query(datetime(2024, 3, 1, tzinfo=UTC), datetime(2024, 4, 1, tzinfo=UTC))
    got = {it.original_date: it.publish_at for it in res.items}
    assert res.total == 5
    # 跳时前：CST (UTC-6)
    assert got[date(2024, 3, 3)] == datetime(2024, 3, 3, 8, 30, tzinfo=UTC)
    # 跳时当天：02:30 不存在 -> 03:30 CDT = 08:30 UTC
    assert got[date(2024, 3, 10)] == datetime(2024, 3, 10, 8, 30, tzinfo=UTC)
    # 跳时后：CDT (UTC-5)，当地日期/钟点不漂移
    assert got[date(2024, 3, 17)] == datetime(2024, 3, 17, 7, 30, tzinfo=UTC)
    assert got[date(2024, 3, 24)] == datetime(2024, 3, 24, 7, 30, tzinfo=UTC)
    assert got[date(2024, 3, 31)] == datetime(2024, 3, 31, 7, 30, tzinfo=UTC)
    cal.close()


def test_fall_back_ambiguous_time(db_path):
    # 2024-11-03 01:30 芝加哥出现两次，取较早的 UTC 时刻 06:30
    cal = EvergreenCalendar(db_path)
    cal.register_plan("fall", date(2024, 10, 27), time(1, 30), 7)
    res = cal.query(datetime(2024, 10, 1, tzinfo=UTC), datetime(2024, 12, 1, tzinfo=UTC))
    got = {it.original_date: it.publish_at for it in res.items}
    assert res.total == 5
    # 回拨前：CDT (UTC-5)
    assert got[date(2024, 10, 27)] == datetime(2024, 10, 27, 6, 30, tzinfo=UTC)
    # 回拨当天：取较早 UTC = 06:30（CDT 那次）
    assert got[date(2024, 11, 3)] == datetime(2024, 11, 3, 6, 30, tzinfo=UTC)
    # 回拨后：CST (UTC-6)，当地日期/钟点不漂移
    assert got[date(2024, 11, 10)] == datetime(2024, 11, 10, 7, 30, tzinfo=UTC)
    assert got[date(2024, 11, 24)] == datetime(2024, 11, 24, 7, 30, tzinfo=UTC)
    cal.close()


def test_no_drift_across_full_year(db_path):
    # 跨越两个 DST 边界的全年轮转：当地日期严格按 7 天递增，钟点不变
    cal = EvergreenCalendar(db_path)
    cal.register_plan("year", date(2024, 1, 7), time(2, 30), 7)
    res = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))
    dates = [it.original_date for it in res.items]
    assert dates[0] == date(2024, 1, 7)
    assert all((b - a).days == 7 for a, b in zip(dates, dates[1:]))
    assert res.total == len(dates) == 52
    cal.close()
