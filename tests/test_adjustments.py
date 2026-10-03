"""单次调整：跨窗口改期、取消后重新改期、多次发布落在同一时刻。"""
from datetime import date, datetime, time, timezone

import pytest

from evergreen_calendar import EvergreenCalendar

UTC = timezone.utc
W0 = datetime(2024, 1, 1, tzinfo=UTC)
W1 = datetime(2024, 2, 1, tzinfo=UTC)


@pytest.fixture()
def cal(tmp_path):
    c = EvergreenCalendar(str(tmp_path / "cal.sqlite"))
    yield c
    c.close()


def test_move_out_of_window_excluded(cal):
    cal.register_plan("p", date(2024, 1, 1), time(9, 0), 7)  # 15:00 UTC
    assert cal.query(W0, W1).total == 5
    # 把 1/15 那次改到 3 月（窗口外）
    cal.reschedule_publish("p", date(2024, 1, 15), datetime(2024, 3, 5, 15, 0, tzinfo=UTC))
    res = cal.query(W0, W1)
    assert res.total == 4
    assert all(it.original_date != date(2024, 1, 15) for it in res.items)
    # 3 月窗口能看到被改入的这一次
    res2 = cal.query(datetime(2024, 3, 1, tzinfo=UTC), datetime(2024, 4, 1, tzinfo=UTC))
    moved = [it for it in res2.items if it.original_date == date(2024, 1, 15)]
    assert len(moved) == 1
    assert moved[0].publish_at == datetime(2024, 3, 5, 15, 0, tzinfo=UTC)


def test_move_into_window_counted_once(cal):
    cal.register_plan("p", date(2024, 3, 4), time(9, 0), 7)  # 原计划全在窗口外
    assert cal.query(W0, W1).total == 0
    cal.reschedule_publish("p", date(2024, 3, 4), datetime(2024, 1, 10, 12, 0, tzinfo=UTC))
    res = cal.query(W0, W1)
    assert res.total == 1
    assert len([it for it in res.items if it.original_date == date(2024, 3, 4)]) == 1


def test_cancel_then_reschedule_revives(cal):
    cal.register_plan("p", date(2024, 1, 1), time(9, 0), 7)
    cal.cancel_publish("p", date(2024, 1, 8))
    assert cal.query(W0, W1).total == 4
    # 已取消的发布可以重新改期，以最后一次调整为准
    cal.reschedule_publish("p", date(2024, 1, 8), datetime(2024, 1, 9, 18, 0, tzinfo=UTC))
    res = cal.query(W0, W1)
    assert res.total == 5
    hit = [it for it in res.items if it.original_date == date(2024, 1, 8)]
    assert len(hit) == 1 and hit[0].publish_at == datetime(2024, 1, 9, 18, 0, tzinfo=UTC)
    # 再次取消，仍以最后一次为准
    cal.cancel_publish("p", date(2024, 1, 8))
    assert cal.query(W0, W1).total == 4


def test_collision_kept_and_stable_order(cal):
    cal.register_plan("b-plan", date(2024, 1, 2), time(9, 0), 7)
    cal.register_plan("a-plan", date(2024, 1, 3), time(9, 0), 7)
    target = datetime(2024, 1, 5, 12, 0, tzinfo=UTC)
    # 三条发布改到同一时刻：b-plan 两条 + a-plan 一条
    cal.reschedule_publish("b-plan", date(2024, 1, 2), target)
    cal.reschedule_publish("b-plan", date(2024, 1, 9), target)
    cal.reschedule_publish("a-plan", date(2024, 1, 3), target)
    res = cal.query(W0, W1)
    at_target = [it for it in res.items if it.publish_at == target]
    # 碰撞不丢记录
    assert len(at_target) == 3
    # 时间相同时按计划标识字典序、原计划日期升序
    assert [(it.plan_id, it.original_date) for it in at_target] == [
        ("a-plan", date(2024, 1, 3)),
        ("b-plan", date(2024, 1, 2)),
        ("b-plan", date(2024, 1, 9)),
    ]
    # 总数：两计划窗口内原计划各 5 次（1/2..1/30、1/3..1/31 的 15:00 UTC）；
    # 被改出的 3 次从原位扣除、改入的 3 次计回，净变化为 0
    assert res.total == 5 + 5


def test_move_beyond_end_date_allowed(cal):
    cal.register_plan("p", date(2024, 1, 1), time(9, 0), 7, end_date=date(2024, 1, 20))
    assert cal.query(W0, W1).total == 3  # 1/1, 1/8, 1/15
    # 改期可以越过计划结束日期
    cal.reschedule_publish("p", date(2024, 1, 15), datetime(2024, 6, 1, 12, 0, tzinfo=UTC))
    assert cal.query(W0, W1).total == 2
    res = cal.query(datetime(2024, 6, 1, tzinfo=UTC), datetime(2024, 6, 2, tzinfo=UTC))
    assert res.total == 1 and res.items[0].original_date == date(2024, 1, 15)
    # 结束日期之外的"原计划"不存在，调整无效果
    cal.reschedule_publish("p", date(2024, 1, 22), datetime(2024, 1, 5, 12, 0, tzinfo=UTC))
    assert cal.query(W0, W1).total == 2
