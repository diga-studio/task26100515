"""America/Chicago 当地时刻 -> UTC 的解析（含夏令时两种边界）。

为保证大规模查询下的性能，预先扫描出时区的 UTC 偏移跳变表，
之后每次解析只做二分查找与少量日期运算，不再经过 zoneinfo。
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CHICAGO_TZ_NAME = "America/Chicago"
_TZ = ZoneInfo(CHICAGO_TZ_NAME)
_EPOCH = datetime(1970, 1, 1)

# 覆盖 1970-01-01 至 2200-01-01 的跳变表：
# _TRANSITIONS[i] 为跳变发生的 UTC 时刻（naive，按 UTC 计），
# _OFFSETS[i] 为该时刻之后生效的偏移；_OFFSETS[0] 为首个跳变之前的偏移。
_TRANSITIONS: list[datetime] = []
_OFFSETS: list[timedelta] = []


def _build_transition_table() -> None:
    start = datetime(1970, 1, 1)
    end = datetime(2200, 1, 1)
    aware_start = start.replace(tzinfo=timezone.utc)
    offsets: list[timedelta] = []
    transitions: list[datetime] = []
    prev_offset = aware_start.astimezone(_TZ).utcoffset()
    offsets.append(prev_offset)
    step = timedelta(hours=24)
    t = start
    while t < end:
        nxt = t + step
        off_t = t.replace(tzinfo=timezone.utc).astimezone(_TZ).utcoffset()
        off_nxt = nxt.replace(tzinfo=timezone.utc).astimezone(_TZ).utcoffset()
        if off_t != off_nxt:
            # 在 (t, nxt] 内二分定位跳变时刻（秒级精度）
            lo, hi = t, nxt
            while (hi - lo) > timedelta(seconds=1):
                mid = lo + (hi - lo) / 2
                off_mid = mid.replace(tzinfo=timezone.utc).astimezone(_TZ).utcoffset()
                if off_mid == off_t:
                    lo = mid
                else:
                    hi = mid
            transitions.append(hi)
            offsets.append(off_nxt)
        t = nxt
    _TRANSITIONS.extend(transitions)
    _OFFSETS.extend(offsets)


_build_transition_table()


def _offset_at_utc(naive_utc: datetime) -> timedelta:
    """某一 UTC 时刻（naive 表示）对应的芝加哥当地偏移。"""
    return _OFFSETS[bisect_right(_TRANSITIONS, naive_utc)]


def resolve_local(naive_local: datetime) -> datetime:
    """把芝加哥当地（naive）时刻解析为 UTC aware 时刻。

    春季跳时不存在的时刻：顺延跳过的时长；
    秋季回拨出现两次的时刻：取较早的 UTC 时刻。
    返回 tzinfo=timezone.utc 的 aware datetime。
    """
    off0 = _offset_at_utc(naive_local)
    utc1 = naive_local - off0
    off1 = _offset_at_utc(utc1)
    if utc1 + off1 == naive_local:
        # 存在；对回拨重叠时刻，此路径给出较早的 UTC 时刻
        return utc1.replace(tzinfo=timezone.utc)
    utc2 = naive_local - off1
    if utc2 + _offset_at_utc(utc2) == naive_local:
        return utc2.replace(tzinfo=timezone.utc)
    # 不存在的时刻（春季跳时）：顺延跳过的时长后重新解析
    gap = off1 - off0
    return resolve_local(naive_local + gap)
