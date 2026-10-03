"""规模与性能：20000 计划 + 100000 调整 + 100 年窗口。

- 子进程运行 tests/perf_scenario.py，校验耗时 / 峰值内存 / 磁盘占用；
- 用基于 zoneinfo 的独立参照实现，校验百年窗口的总数与前 100 条；
- 校验关闭重开后查询结果一致。
"""
import heapq
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from itertools import islice
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("America/Chicago")
UTC = timezone.utc
WINDOW_START = datetime(2024, 1, 1, tzinfo=UTC)
WINDOW_END = datetime(2124, 1, 1, tzinfo=UTC)
MAX_QUERY_SECONDS = 3.0
MAX_RSS_BYTES = 256 * 1024 * 1024
MAX_DB_BYTES = 128 * 1024 * 1024

SCENARIO = os.path.join(os.path.dirname(__file__), "perf_scenario.py")


# ---- 独立参照实现（不依赖被测库，直接基于 zoneinfo） ----

def ref_resolve(naive):
    """当地时刻 -> UTC。fold=0 优先（回拨取较早 UTC）；不存在则顺延。"""
    for fold in (0, 1):
        dt = naive.replace(tzinfo=TZ, fold=fold)
        if dt.astimezone(UTC).astimezone(TZ).replace(tzinfo=None) == naive:
            return dt.astimezone(UTC)
    off_before = (naive - timedelta(hours=3)).replace(tzinfo=TZ).utcoffset()
    off_after = (naive + timedelta(hours=3)).replace(tzinfo=TZ).utcoffset()
    return ref_resolve(naive + (off_after - off_before))


def ref_plan_utc(plan, k):
    start, publish_time, interval = plan[1], plan[2], plan[3]
    local_date = start + timedelta(days=k * interval)
    return ref_resolve(datetime.combine(local_date, publish_time))


def ref_occurrence_exists(plan, d):
    start, interval, end = plan[1], plan[3], plan[4]
    if d < start or (d - start).days % interval != 0:
        return False
    return end is None or d < end


def ref_window_ks(plan, w0, w1):
    start, interval, end = plan[1], plan[3], plan[4]
    if end is not None:
        span = (end - start).days
        k_hi = -(-span // interval)
    else:
        k_hi = ((w1 + timedelta(hours=6)).date() - start).days // interval + 2
    if k_hi <= 0:
        return 0, 0

    def first_ge(k_lo, threshold):
        lo, hi = k_lo, k_hi
        while lo < hi:
            mid = (lo + hi) // 2
            if ref_plan_utc(plan, mid) >= threshold:
                hi = mid
            else:
                lo = mid + 1
        return lo

    lo = first_ge(0, w0)
    return lo, first_ge(lo, w1)


def ref_query(db_path, w0, w1):
    conn = sqlite3.connect(db_path)
    plans = {}
    for pid, sd, pt, iv, ed in conn.execute(
        "SELECT plan_id, start_date, publish_time, interval_days, end_date FROM plans"
    ):
        plans[pid] = (
            pid,
            date.fromisoformat(sd),
            time.fromisoformat(pt),
            iv,
            date.fromisoformat(ed) if ed else None,
        )
    adjustments = {}
    for pid, od, canceled, moved_to in conn.execute(
        "SELECT plan_id, original_date, canceled, moved_to FROM adjustments"
    ):
        adjustments[(pid, date.fromisoformat(od))] = (
            canceled,
            datetime.fromisoformat(moved_to).astimezone(UTC) if moved_to else None,
        )
    conn.close()

    total = 0
    streams = []
    for plan in plans.values():
        lo, hi = ref_window_ks(plan, w0, w1)
        total += hi - lo

        def gen(plan=plan, lo=lo, hi=hi):
            pid, start, interval = plan[0], plan[1], plan[3]
            for k in range(lo, hi):
                d = start + timedelta(days=k * interval)
                if (pid, d) not in adjustments:
                    yield (ref_plan_utc(plan, k), pid, d)

        streams.append(gen())

    moved_in = []
    for (pid, d), (canceled, moved_to) in adjustments.items():
        plan = plans.get(pid)
        if plan is None or not ref_occurrence_exists(plan, d):
            continue
        k = (d - plan[1]).days // plan[3]
        t0 = ref_plan_utc(plan, k)
        if canceled:
            if w0 <= t0 < w1:
                total -= 1
        else:
            in0 = w0 <= t0 < w1
            in1 = w0 <= moved_to < w1
            total += int(in1) - int(in0)
            if in1:
                moved_in.append((moved_to, pid, d))
    moved_in.sort()

    first100 = list(islice(heapq.merge(*streams, iter(moved_in)), 100))
    return total, [(pid, d.isoformat(), utc.isoformat()) for utc, pid, d in first100]


# ---- 测试 ----

@pytest.fixture(scope="module")
def report(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("perf")
    result_path = os.path.join(workdir, "result.json")
    subprocess.run(
        [sys.executable, SCENARIO, str(workdir), result_path],
        check=True,
        capture_output=True,
        text=True,
    )
    with open(result_path) as f:
        data = json.load(f)
    data["db_path"] = os.path.join(workdir, "perf.sqlite")
    return data


def test_query_within_3_seconds(report):
    print(f"\n百年窗口查询耗时: {report['query_seconds']:.3f}s "
          f"(重开后 {report['reopen_query_seconds']:.3f}s)")
    assert report["query_seconds"] < MAX_QUERY_SECONDS
    assert report["reopen_query_seconds"] < MAX_QUERY_SECONDS


def test_peak_memory_within_256_mib(report):
    print(f"\n峰值内存: {report['peak_rss_bytes'] / 2**20:.1f} MiB")
    assert report["peak_rss_bytes"] < MAX_RSS_BYTES


def test_db_size_within_128_mib(report):
    print(f"\n数据库磁盘占用: {report['db_bytes'] / 2**20:.2f} MiB")
    assert report["db_bytes"] < MAX_DB_BYTES


def test_reopen_results_identical(report):
    assert report["total"] == report["reopen_total"]
    assert [tuple(i) for i in report["items"]] == [
        tuple(i) for i in report["reopen_items"]
    ]


def test_total_and_first100_match_reference(report):
    ref_total, ref_items = ref_query(report["db_path"], WINDOW_START, WINDOW_END)
    print(f"\n百年窗口总数: {report['total']} (参照 {ref_total})")
    assert report["total"] == ref_total
    assert [tuple(i) for i in report["items"]] == [tuple(i) for i in ref_items]
    assert len(report["items"]) == 100
