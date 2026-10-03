"""查询语义：窗口左闭右开、前 100 条、排序、结束日不包含、重开一致。"""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from evergreen_calendar import EvergreenCalendar

UTC = timezone.utc


@pytest.fixture()
def cal(tmp_path):
    c = EvergreenCalendar(str(tmp_path / "cal.sqlite"))
    yield c
    c.close()


def test_window_left_closed_right_open(cal):
    cal.register_plan("p", date(2024, 1, 1), time(9, 0), 7)  # 15:00 UTC
    t = datetime(2024, 1, 8, 15, 0, tzinfo=UTC)
    # 左闭：起点恰好等于发布时刻，计入
    assert cal.query(t, t + timedelta(days=1)).total == 1
    # 右开：终点恰好等于发布时刻，不计入
    assert cal.query(t - timedelta(days=1), t).total == 0


def test_end_date_exclusive(cal):
    cal.register_plan("p", date(2024, 1, 1), time(9, 0), 7, end_date=date(2024, 1, 15))
    res = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC))
    assert res.total == 2  # 1/1、1/8；1/15 不包含
    assert [it.original_date for it in res.items] == [date(2024, 1, 1), date(2024, 1, 8)]


def test_interval_validation(tmp_path):
    cal = EvergreenCalendar(str(tmp_path / "v.sqlite"))
    with pytest.raises(ValueError):
        cal.register_plan("bad", date(2024, 1, 1), time(9, 0), 10)
    with pytest.raises(ValueError):
        cal.register_plan("bad2", date(2024, 1, 1), time(9, 0), 7, end_date=date(2024, 1, 1))
    for iv in (7, 14, 30):
        cal.register_plan(f"ok{iv}", date(2024, 1, 1), time(9, 0), iv)
    with pytest.raises(ValueError):
        cal.register_plan("ok7", date(2024, 2, 1), time(9, 0), 7)  # 重复标识
    cal.close()


def test_first_100_limit_and_order(cal):
    # 3 个计划交错发布，窗口内总数远超 100
    for i, d in enumerate((date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3))):
        cal.register_plan(f"plan-{i}", d, time(9, 0), 7)
    res = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))
    assert res.total == 53 + 53 + 52  # 2024 为闰年
    assert len(res.items) == 100
    # 严格按 (时刻, 计划标识, 原计划日期) 升序
    keys = [(it.publish_at, it.plan_id, it.original_date) for it in res.items]
    assert keys == sorted(keys)
    # 前 100 条就是最早的一批
    assert res.items[0].publish_at == datetime(2024, 1, 1, 15, 0, tzinfo=UTC)


def test_30_day_interval_is_not_a_month(cal):
    cal.register_plan("p", date(2024, 1, 31), time(9, 0), 30)
    res = cal.query(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 5, 1, tzinfo=UTC))
    dates = [it.original_date for it in res.items]
    assert dates == [date(2024, 1, 31), date(2024, 3, 1), date(2024, 3, 31), date(2024, 4, 30)]


def test_persistence_across_reopen(tmp_path):
    path = str(tmp_path / "cal.sqlite")
    cal = EvergreenCalendar(path)
    cal.register_plan("p1", date(2024, 1, 1), time(9, 0), 7)
    cal.register_plan("p2", date(2024, 1, 2), time(2, 30), 14, end_date=date(2025, 1, 1))
    cal.cancel_publish("p1", date(2024, 1, 8))
    cal.reschedule_publish("p2", date(2024, 1, 16), datetime(2024, 1, 5, 10, 0, tzinfo=UTC))
    w0, w1 = datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 6, 1, tzinfo=UTC)
    before = cal.query(w0, w1)
    cal.close()

    cal2 = EvergreenCalendar(path)
    after = cal2.query(w0, w1)
    cal2.close()
    assert after.total == before.total
    assert [(i.plan_id, i.original_date, i.publish_at) for i in after.items] == [
        (i.plan_id, i.original_date, i.publish_at) for i in before.items
    ]


def test_query_requires_aware_datetimes(cal):
    with pytest.raises(ValueError):
        cal.query(datetime(2024, 1, 1), datetime(2024, 2, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        cal.query(datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC))
