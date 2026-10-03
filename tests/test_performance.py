"""Scale and performance tests: 20k plans, 100k adjustments, 100-year window."""

import os
import random
import resource
import sys
import time as time_mod
import tracemalloc
from datetime import date, datetime, time, timedelta, timezone

from evergreen_calendar import EvergreenCalendar
from evergreen_calendar.timeutil import local_to_utc

UTC = timezone.utc

NUM_PLANS = 20_000
NUM_ADJUSTMENTS = 100_000
WINDOW_START = datetime(2024, 1, 1, tzinfo=UTC)
WINDOW_END = datetime(2124, 1, 1, tzinfo=UTC)  # 100 years
MAX_QUERY_SECONDS = 3.0
MAX_MEMORY_BYTES = 256 * 1024 * 1024
MAX_DISK_BYTES = 128 * 1024 * 1024


def _generate(db_path, num_plans, num_adjustments, seed):
    """Fill a calendar with deterministic scale data. Returns (plans, adjs)."""
    rng = random.Random(seed)
    cal = EvergreenCalendar(db_path)
    plans = []
    for i in range(num_plans):
        plan_id = f"plan-{i:05d}"
        start = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 366))
        wall = time(rng.randrange(0, 24), rng.randrange(0, 60))
        interval = (7, 14, 30)[i % 3]
        end = None
        if rng.random() < 0.3:
            end = start + timedelta(days=rng.randrange(60, 3650))
        cal.register_plan(plan_id, start, wall, interval, end)
        plans.append((plan_id, start, wall, interval, end))

    window_days = (WINDOW_END.date() - WINDOW_START.date()).days
    adjustments = []
    for _ in range(num_adjustments):
        plan_id, start, wall, interval, end = plans[rng.randrange(len(plans))]
        span_days = window_days if end is None else (end - start).days
        k = rng.randrange(0, max(1, span_days // interval))
        orig = start + timedelta(days=k * interval)
        if rng.random() < 0.4:
            cal.cancel_occurrence(plan_id, orig)
            adjustments.append((plan_id, orig, "cancel", None))
        else:
            target = WINDOW_START + timedelta(
                seconds=rng.randrange(0, window_days * 86400)
            )
            cal.reschedule_occurrence(plan_id, orig, target)
            adjustments.append((plan_id, orig, "move", target))
    cal.close()

    final = {}
    for a in adjustments:
        final[(a[0], a[1])] = a
    return plans, list(final.values())


def _brute_force(plans, adjustments, ws, we):
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


def _db_size(path):
    total = 0
    for suffix in ("", "-wal", "-shm", "-journal"):
        if os.path.exists(path + suffix):
            total += os.path.getsize(path + suffix)
    return total


def test_performance_at_scale(tmp_path):
    db_path = str(tmp_path / "perf.db")
    plans, adjustments = _generate(db_path, NUM_PLANS, NUM_ADJUSTMENTS, seed=7)

    cal = EvergreenCalendar(db_path)
    tracemalloc.start()
    t0 = time_mod.perf_counter()
    result = cal.query(WINDOW_START, WINDOW_END)
    elapsed = time_mod.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, KiB on Linux.
    rss_bytes = rss_raw if sys.platform == "darwin" else rss_raw * 1024
    disk = _db_size(db_path)

    print(f"\nquery time: {elapsed:.3f}s (limit {MAX_QUERY_SECONDS}s)")
    print(f"query peak python memory: {peak / 2**20:.1f} MiB (limit 256 MiB)")
    print(f"process max RSS: {rss_bytes / 2**20:.1f} MiB")
    print(f"database size: {disk / 2**20:.1f} MiB (limit 128 MiB)")
    print(f"total publications in 100-year window: {result.total_count}")

    assert elapsed < MAX_QUERY_SECONDS
    assert peak < MAX_MEMORY_BYTES
    assert disk < MAX_DISK_BYTES

    # Shape invariants on the top-100.
    assert len(result.items) == 100
    keys = [(it.publish_utc, it.plan_id, it.orig_date) for it in result.items]
    assert keys == sorted(keys)
    assert len({(it.plan_id, it.orig_date) for it in result.items}) == 100
    assert all(WINDOW_START <= it.publish_utc < WINDOW_END for it in result.items)
    cal.close()

    # Reopen: identical count, identities and order.
    cal2 = EvergreenCalendar(db_path)
    again = cal2.query(WINDOW_START, WINDOW_END)
    cal2.close()
    assert again == result

    # Exact check on a random sample of plans via brute force.
    rng = random.Random(99)
    sample = rng.sample(plans, 100)
    sample_ids = {p[0] for p in sample}
    sample_adjs = [a for a in adjustments if a[0] in sample_ids]
    sample_db = str(tmp_path / "sample.db")
    cal3 = EvergreenCalendar(sample_db)
    for plan_id, start, wall, interval, end in sample:
        cal3.register_plan(plan_id, start, wall, interval, end)
    for plan_id, orig, action, target in sample_adjs:
        if action == "cancel":
            cal3.cancel_occurrence(plan_id, orig)
        else:
            cal3.reschedule_occurrence(plan_id, orig, target)
    got = cal3.query(WINDOW_START, WINDOW_END)
    cal3.close()
    expected = _brute_force(sample, sample_adjs, WINDOW_START, WINDOW_END)
    assert got.total_count == len(expected)
    assert [(it.publish_utc, it.plan_id, it.orig_date) for it in got.items] == (
        expected[:100]
    )


def test_exact_full_verification_moderate_scale(tmp_path):
    """Full brute-force verification of total and top-100 at moderate scale."""
    db_path = str(tmp_path / "moderate.db")
    plans, adjustments = _generate(db_path, 300, 2_000, seed=13)
    cal = EvergreenCalendar(db_path)
    result = cal.query(WINDOW_START, WINDOW_END)
    cal.close()

    expected = _brute_force(plans, adjustments, WINDOW_START, WINDOW_END)
    assert result.total_count == len(expected)
    assert [(it.publish_utc, it.plan_id, it.orig_date) for it in result.items] == (
        expected[:100]
    )
