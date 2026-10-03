"""核心类型定义：轮转计划、单次调整、查询结果。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

VALID_INTERVAL_DAYS = (7, 14, 30)


@dataclass(frozen=True)
class Plan:
    """常青内容轮转计划。登记后不可修改。"""

    plan_id: str
    start_date: date          # 起始当地日期（包含）
    publish_time: time        # 当地发布钟点
    interval_days: int        # 只能是 7 / 14 / 30
    end_date: date | None     # 结束当地日期（不包含），None 表示持续轮转


@dataclass(frozen=True)
class Adjustment:
    """单次发布调整。canceled 与 moved_to 互斥；最后一次调整生效。"""

    plan_id: str
    original_date: date       # 原计划当地日期
    canceled: bool
    moved_to: datetime | None  # 改期到的 UTC 时刻（aware）


@dataclass(frozen=True)
class ScheduledPublish:
    """窗口内一条实际发布。"""

    plan_id: str
    original_date: date       # 原计划当地日期（发布身份的一部分）
    publish_at: datetime      # 实际发布 UTC 时刻（aware）


@dataclass(frozen=True)
class QueryResult:
    """查询结果：窗口内准确总数 + 最早的前若干条。"""

    total: int
    items: tuple[ScheduledPublish, ...]
