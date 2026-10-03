"""Public API: register plans, adjust occurrences, query the calendar."""

from __future__ import annotations

import heapq
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterator, List, Optional, Tuple, Union

from .models import VALID_INTERVAL_DAYS, Adjustment, QueryResult, RotationPlan, ScheduledPost
from .storage import Storage
from .timeutil import local_to_utc

# Chicago UTC offsets stay within [-6h, -5h] for any modern date, so a
# publication with UTC instant < t always has a local naive datetime inside
# [t - 6h, t - 5h).  That band is one hour wide while occurrences of a plan
# are spaced >= 7 days apart, so at most one occurrence per plan can fall
# inside the band; everything below it is counted with pure arithmetic.
_OFFSET_SPAN_LOW = timedelta(hours=6)
_OFFSET_SPAN_HIGH = timedelta(hours=5)


def _ceil_div_delta(delta: timedelta, step: timedelta) -> int:
    """Number of k >= 0 with k * step < delta (delta may be <= 0)."""
    if delta <= timedelta(0):
        return 0
    q, r = divmod(delta, step)
    return q + (1 if r else 0)


class _PlanState:
    """Per-plan precomputed data for one query."""

    __slots__ = ("plan", "base_naive", "step", "k_max", "first_k")

    def __init__(self, plan: RotationPlan) -> None:
        self.plan = plan
        self.base_naive = datetime.combine(plan.start_date, plan.publish_time)
        self.step = timedelta(days=plan.interval_days)
        if plan.end_date is not None:
            span = (plan.end_date - plan.start_date).days
            self.k_max = -(-span // plan.interval_days) if span > 0 else 0
        else:
            self.k_max = None  # type: ignore[assignment]

    def utc_at(self, k: int) -> datetime:
        naive = self.base_naive + k * self.step
        return local_to_utc(naive.date(), naive.time())

    def count_utc_before(self, t: datetime) -> int:
        """Number of this plan's occurrences with UTC instant < t."""
        k_max = self.k_max
        if k_max == 0:
            return 0
        t_naive = t.replace(tzinfo=None)
        low = _ceil_div_delta(t_naive - _OFFSET_SPAN_LOW - self.base_naive, self.step)
        if k_max is not None and low > k_max:
            low = k_max
        k_cand = low
        if k_max is not None and k_cand >= k_max:
            return low
        naive_cand = self.base_naive + k_cand * self.step
        if naive_cand < t_naive - _OFFSET_SPAN_HIGH:
            if local_to_utc(naive_cand.date(), naive_cand.time()) < t:
                return low + 1
        return low


class EvergreenCalendar:
    """Evergreen content rotation calendar backed by SQLite."""

    def __init__(self, db_path: Union[str, "object"]) -> None:
        self._storage = Storage(str(db_path))

    def register_plan(
        self,
        plan_id: str,
        start_date: date,
        publish_time: time,
        interval_days: int,
        end_date: Optional[date] = None,
    ) -> None:
        if not isinstance(plan_id, str) or not plan_id:
            raise ValueError("plan_id must be a non-empty string")
        if interval_days not in VALID_INTERVAL_DAYS:
            raise ValueError(
                f"interval_days must be one of {VALID_INTERVAL_DAYS},"
                f" got {interval_days!r}"
            )
        if not isinstance(publish_time, time):
            raise ValueError("publish_time must be a datetime.time")
        if end_date is not None and end_date <= start_date:
            raise ValueError("end_date must be after start_date")
        plan = RotationPlan(
            plan_id=plan_id,
            start_date=start_date,
            publish_time=publish_time,
            interval_days=interval_days,
            end_date=end_date,
        )
        try:
            self._storage.insert_plan(plan)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"plan_id already registered: {plan_id!r}") from exc

    def cancel_occurrence(self, plan_id: str, orig_date: date) -> None:
        self._storage.get_plan(plan_id)  # raises KeyError for unknown plans
        self._storage.upsert_adjustment(
            Adjustment(plan_id=plan_id, orig_date=orig_date, action="cancel")
        )

    def reschedule_occurrence(
        self, plan_id: str, orig_date: date, target_utc: datetime
    ) -> None:
        self._storage.get_plan(plan_id)  # raises KeyError for unknown plans
        if target_utc.tzinfo is None:
            raise ValueError("target_utc must be timezone-aware")
        self._storage.upsert_adjustment(
            Adjustment(
                plan_id=plan_id,
                orig_date=orig_date,
                action="move",
                target_utc=target_utc.astimezone(timezone.utc),
            )
        )

    def query(self, window_start_utc: datetime, window_end_utc: datetime) -> QueryResult:
        if window_start_utc.tzinfo is None or window_end_utc.tzinfo is None:
            raise ValueError("window bounds must be timezone-aware")
        ws = window_start_utc.astimezone(timezone.utc)
        we = window_end_utc.astimezone(timezone.utc)
        if not ws < we:
            raise ValueError("window_start_utc must be before window_end_utc")

        self._storage.flush()
        plans = self._storage.all_plans()
        adjustments = self._storage.all_adjustments()

        plans_by_id: Dict[str, RotationPlan] = {p.plan_id: p for p in plans}
        adj_by_plan: Dict[str, Dict[date, Adjustment]] = {}
        for adj in adjustments:
            adj_by_plan.setdefault(adj.plan_id, {})[adj.orig_date] = adj

        states: List[_PlanState] = []
        total = 0
        for plan in plans:
            state = _PlanState(plan)
            state.first_k = state.count_utc_before(ws)
            total += state.count_utc_before(we) - state.first_k
            states.append(state)

        moved: List[Tuple[datetime, str, date]] = []
        for adj in adjustments:
            plan = plans_by_id.get(adj.plan_id)
            if plan is None:
                continue
            if not _original_exists(plan, adj.orig_date):
                continue
            orig_utc = local_to_utc(adj.orig_date, plan.publish_time)
            if ws <= orig_utc < we:
                total -= 1
            if adj.action == "move" and adj.target_utc is not None:
                target = adj.target_utc
                if ws <= target < we:
                    total += 1
                    moved.append((target, adj.plan_id, adj.orig_date))

        items = self._first_items(states, adj_by_plan, moved, ws, we)
        return QueryResult(total_count=total, items=tuple(items))

    @staticmethod
    def _first_items(
        states: List[_PlanState],
        adj_by_plan: Dict[str, Dict[date, Adjustment]],
        moved: List[Tuple[datetime, str, date]],
        ws: datetime,
        we: datetime,
        limit: int = 100,
    ) -> List[ScheduledPost]:
        heap: List[Tuple[datetime, str, date, Iterator[Tuple[datetime, str, date]]]] = []

        def push(it: Iterator[Tuple[datetime, str, date]]) -> None:
            entry = next(it, None)
            if entry is not None:
                heapq.heappush(heap, (entry[0], entry[1], entry[2], it))

        for state in states:
            if state.k_max is not None and state.first_k >= state.k_max:
                continue
            first_utc = state.utc_at(state.first_k)
            if first_utc >= we:
                continue
            push(_plan_occurrences(state, adj_by_plan.get(state.plan.plan_id), we))
        if moved:
            push(iter(sorted(moved)))

        out: List[ScheduledPost] = []
        while heap and len(out) < limit:
            utc, plan_id, orig_date, it = heapq.heappop(heap)
            out.append(ScheduledPost(plan_id=plan_id, orig_date=orig_date, publish_utc=utc))
            push(it)
        return out

    def close(self) -> None:
        self._storage.close()

    def __enter__(self) -> "EvergreenCalendar":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _original_exists(plan: RotationPlan, orig_date: date) -> bool:
    """Whether orig_date is a scheduled occurrence date of the plan."""
    delta = (orig_date - plan.start_date).days
    if delta < 0 or delta % plan.interval_days != 0:
        return False
    return plan.end_date is None or orig_date < plan.end_date


def _plan_occurrences(
    state: _PlanState,
    adj_set: Optional[Dict[date, Adjustment]],
    we: datetime,
) -> Iterator[Tuple[datetime, str, date]]:
    """Yield (utc, plan_id, orig_date) for unadjusted occurrences before we."""
    plan = state.plan
    k = state.first_k
    while state.k_max is None or k < state.k_max:
        utc = state.utc_at(k)
        if utc >= we:
            return
        orig_date = (state.base_naive + k * state.step).date()
        if adj_set is None or orig_date not in adj_set:
            yield (utc, plan.plan_id, orig_date)
        k += 1
