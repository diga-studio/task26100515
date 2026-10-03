"""Data types for the evergreen rotation calendar library."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional, Tuple

VALID_INTERVAL_DAYS = (7, 14, 30)


@dataclass(frozen=True)
class RotationPlan:
    """A registered rotation plan. Immutable once registered."""

    plan_id: str
    start_date: date
    publish_time: time
    interval_days: int
    end_date: Optional[date] = None


@dataclass(frozen=True)
class Adjustment:
    """Final state of a single-occurrence adjustment."""

    plan_id: str
    orig_date: date
    action: str  # "cancel" or "move"
    target_utc: Optional[datetime] = None  # set when action == "move"


@dataclass(frozen=True)
class ScheduledPost:
    """One concrete publication inside a query window."""

    plan_id: str
    orig_date: date
    publish_utc: datetime


@dataclass(frozen=True)
class QueryResult:
    """Result of a window query."""

    total_count: int
    items: Tuple[ScheduledPost, ...]
