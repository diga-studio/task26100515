"""性能场景：20000 计划 + 100000 调整 + 100 年窗口。

作为独立子进程运行，输出 JSON：耗时、峰值内存、磁盘占用、查询结果。
用法: python3 tests/perf_scenario.py <工作目录> <结果.json>
"""
import json
import os
import resource
import sys
import time
from datetime import date, datetime, time as dtime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evergreen_calendar import EvergreenCalendar

UTC = timezone.utc
N_PLANS = 20000
N_ADJUSTMENTS = 100000
WINDOW_START = datetime(2024, 1, 1, tzinfo=UTC)
WINDOW_END = datetime(2124, 1, 1, tzinfo=UTC)  # 100 年


def build(db_path):
    cal = EvergreenCalendar(db_path)
    starts = []
    for i in range(N_PLANS):
        start = date(2024, 1, 1) + timedelta(days=i % 365)
        interval = (7, 14, 30)[i % 3]
        end = (start + timedelta(days=365 * 3)) if i % 5 == 0 else None
        cal.register_plan(
            f"plan-{i:05d}", start, dtime(i % 24, (i * 7) % 60), interval, end
        )
        starts.append((f"plan-{i:05d}", start, interval))
    for j in range(N_ADJUSTMENTS):
        plan_id, start, interval = starts[j % N_PLANS]
        original = start + timedelta(days=((j * 13) % 50) * interval)
        if j % 2 == 0:
            cal.cancel_publish(plan_id, original)
        else:
            moved = WINDOW_START + timedelta(hours=(j * 7919) % (100 * 366 * 24))
            cal.reschedule_publish(plan_id, original, moved)
    return cal


def main():
    workdir, result_path = sys.argv[1], sys.argv[2]
    db_path = os.path.join(workdir, "perf.sqlite")

    t0 = time.perf_counter()
    cal = build(db_path)
    build_seconds = time.perf_counter() - t0

    t0 = time.perf_counter()
    res1 = cal.query(WINDOW_START, WINDOW_END)
    query_seconds = time.perf_counter() - t0
    cal.close()

    cal2 = EvergreenCalendar(db_path)
    t0 = time.perf_counter()
    res2 = cal2.query(WINDOW_START, WINDOW_END)
    reopen_query_seconds = time.perf_counter() - t0
    cal2.close()

    items1 = [(i.plan_id, i.original_date.isoformat(), i.publish_at.isoformat())
              for i in res1.items]
    items2 = [(i.plan_id, i.original_date.isoformat(), i.publish_at.isoformat())
              for i in res2.items]

    report = {
        "build_seconds": build_seconds,
        "query_seconds": query_seconds,
        "reopen_query_seconds": reopen_query_seconds,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "db_bytes": os.path.getsize(db_path),
        "total": res1.total,
        "reopen_total": res2.total,
        "items": items1,
        "reopen_items": items2,
    }
    with open(result_path, "w") as f:
        json.dump(report, f)
    print(json.dumps({k: v for k, v in report.items() if not k.endswith("items")},
                     indent=2))


if __name__ == "__main__":
    main()
