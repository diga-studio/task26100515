"""常青内容轮转日历：登记计划、调整单次发布、查询日历。

查询性能要点：
- 同一计划内各次发布的 UTC 时刻随序号 k 严格递增（间隔 >= 7 天，
  夏令时偏移最多 1 小时），因此窗口计数对每个计划做二分查找，
  无需展开全部日历。
- 前 100 条用堆归并各计划窗口内的有序序列，并混入被改入窗口的
  单次发布；被取消或改出的发布在归并时跳过。
"""
from __future__ import annotations

import heapq
from datetime import date, datetime, time, timedelta, timezone

from .store import Store
from .types import (
    VALID_INTERVAL_DAYS,
    Adjustment,
    Plan,
    QueryResult,
    ScheduledPublish,
)
from .tzutil import resolve_local

MAX_ITEMS = 100
# 芝加哥偏移最大为 UTC-6，用于由 UTC 窗口推算当地日期的保守上界
_MAX_OFFSET = timedelta(hours=6)


def _as_utc(dt: datetime, name: str) -> datetime:
    if dt.tzinfo is None:
        raise ValueError(f"{name} 必须是带时区的 datetime")
    return dt.astimezone(timezone.utc)


class EvergreenCalendar:
    """上层直接调用的入口。所有计划使用 America/Chicago 时区。"""

    def __init__(self, db_path: str) -> None:
        self._store = Store(db_path)
        self._plans: dict[str, Plan] = self._store.load_plans()
        self._adjustments: dict[tuple[str, date], Adjustment] = (
            self._store.load_adjustments()
        )
        self._adj_utc_cache: dict[tuple[str, date], datetime] = {}

    # ---- 登记轮转计划 ----

    def register_plan(
        self,
        plan_id: str,
        start_date: date,
        publish_time: time,
        interval_days: int,
        end_date: date | None = None,
    ) -> None:
        if not plan_id:
            raise ValueError("plan_id 不能为空")
        if plan_id in self._plans:
            raise ValueError(f"计划已存在: {plan_id!r}")
        if interval_days not in VALID_INTERVAL_DAYS:
            raise ValueError(f"interval_days 只能是 {VALID_INTERVAL_DAYS}")
        if end_date is not None and end_date <= start_date:
            raise ValueError("end_date 必须晚于 start_date")
        plan = Plan(plan_id, start_date, publish_time, interval_days, end_date)
        self._store.save_plan(plan)
        self._plans[plan_id] = plan

    # ---- 调整单次发布 ----

    def cancel_publish(self, plan_id: str, original_date: date) -> None:
        self._put_adjustment(Adjustment(plan_id, original_date, True, None))

    def reschedule_publish(
        self, plan_id: str, original_date: date, moved_to: datetime
    ) -> None:
        moved_to_utc = _as_utc(moved_to, "moved_to")
        self._put_adjustment(
            Adjustment(plan_id, original_date, False, moved_to_utc)
        )

    def _put_adjustment(self, adj: Adjustment) -> None:
        if adj.plan_id not in self._plans:
            raise ValueError(f"未知计划: {adj.plan_id!r}")
        self._store.save_adjustment(adj)
        key = (adj.plan_id, adj.original_date)
        self._adjustments[key] = adj
        self._adj_utc_cache.pop(key, None)

    # ---- 查询日历 ----

    def query(self, window_start: datetime, window_end: datetime) -> QueryResult:
        """UTC 窗口 [window_start, window_end)，返回准确总数与最早前 100 条。"""
        w0 = _as_utc(window_start, "window_start")
        w1 = _as_utc(window_end, "window_end")
        if not w0 < w1:
            raise ValueError("window_start 必须早于 window_end")

        total = 0
        heap: list[tuple] = []
        for plan in self._plans.values():
            lo, hi = self._plan_window_ks(plan, w0, w1)
            total += hi - lo
            k = self._next_unadjusted(plan, lo, hi)
            if k is not None:
                heap.append(self._heap_entry(plan, k, hi))

        for key, adj in self._adjustments.items():
            plan = self._plans.get(adj.plan_id)
            if plan is None or not self._occurrence_exists(plan, adj.original_date):
                continue  # 原计划中不存在这次发布，调整无效果
            t0 = self._original_utc(plan, adj)
            if adj.canceled:
                if w0 <= t0 < w1:
                    total -= 1
            else:
                t1 = adj.moved_to
                in0 = w0 <= t0 < w1
                in1 = w0 <= t1 < w1
                total += int(in1) - int(in0)
                if in1:
                    heap.append((t1, adj.plan_id, adj.original_date, -1, 0))

        heapq.heapify(heap)
        items: list[ScheduledPublish] = []
        while heap and len(items) < MAX_ITEMS:
            utc, plan_id, orig_date, k, hi = heapq.heappop(heap)
            items.append(ScheduledPublish(plan_id, orig_date, utc))
            if k >= 0:
                plan = self._plans[plan_id]
                nk = self._next_unadjusted(plan, k + 1, hi)
                if nk is not None:
                    heapq.heappush(heap, self._heap_entry(plan, nk, hi))
        return QueryResult(total, tuple(items))

    def close(self) -> None:
        self._store.close()

    # ---- 内部：计划序列 ----

    def _utc_of(self, plan: Plan, k: int) -> datetime:
        local_date = plan.start_date + timedelta(days=k * plan.interval_days)
        naive = datetime.combine(local_date, plan.publish_time)
        return resolve_local(naive)

    def _plan_window_ks(self, plan: Plan, w0: datetime, w1: datetime) -> tuple[int, int]:
        """窗口 [w0, w1) 覆盖的序号范围 [lo, hi)。"""
        if plan.end_date is not None:
            span = (plan.end_date - plan.start_date).days
            k_hi = -(-span // plan.interval_days)  # ceil，结束日不包含
        else:
            last_local_date = (w1 + _MAX_OFFSET).date()
            days = (last_local_date - plan.start_date).days
            k_hi = days // plan.interval_days + 2
        if k_hi <= 0:
            return 0, 0
        lo = self._first_k_ge(plan, 0, k_hi, w0)
        hi = self._first_k_ge(plan, lo, k_hi, w1)
        return lo, hi

    def _first_k_ge(self, plan: Plan, k_lo: int, k_hi: int, threshold: datetime) -> int:
        """[k_lo, k_hi) 内首个满足 utc(k) >= threshold 的 k；不存在则返回 k_hi。"""
        lo, hi = k_lo, k_hi
        while lo < hi:
            mid = (lo + hi) // 2
            if self._utc_of(plan, mid) >= threshold:
                hi = mid
            else:
                lo = mid + 1
        return lo

    def _next_unadjusted(self, plan: Plan, k: int, k_hi: int) -> int | None:
        """从 k 起第一个没有调整的序号；窗口内没有则返回 None。"""
        while k < k_hi:
            local_date = plan.start_date + timedelta(days=k * plan.interval_days)
            if (plan.plan_id, local_date) not in self._adjustments:
                return k
            k += 1
        return None

    def _heap_entry(self, plan: Plan, k: int, k_hi: int) -> tuple:
        local_date = plan.start_date + timedelta(days=k * plan.interval_days)
        return (self._utc_of(plan, k), plan.plan_id, local_date, k, k_hi)

    # ---- 内部：单次调整 ----

    @staticmethod
    def _occurrence_exists(plan: Plan, original_date: date) -> bool:
        """原计划中是否存在这次发布（结束日期只影响这里）。"""
        if original_date < plan.start_date:
            return False
        if (original_date - plan.start_date).days % plan.interval_days != 0:
            return False
        if plan.end_date is not None and original_date >= plan.end_date:
            return False
        return True

    def _original_utc(self, plan: Plan, adj: Adjustment) -> datetime:
        key = (adj.plan_id, adj.original_date)
        cached = self._adj_utc_cache.get(key)
        if cached is None:
            k = (adj.original_date - plan.start_date).days // plan.interval_days
            cached = self._utc_of(plan, k)
            self._adj_utc_cache[key] = cached
        return cached
