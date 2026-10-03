"""SQLite 持久化：计划与单次调整的存取。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timezone

from .types import Adjustment, Plan

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    start_date TEXT NOT NULL,        -- ISO 当地日期
    publish_time TEXT NOT NULL,      -- ISO 当地钟点 HH:MM[:SS]
    interval_days INTEGER NOT NULL,
    end_date TEXT                    -- ISO 当地日期或 NULL
);
CREATE TABLE IF NOT EXISTS adjustments (
    plan_id TEXT NOT NULL,
    original_date TEXT NOT NULL,     -- ISO 当地日期
    canceled INTEGER NOT NULL,       -- 0/1
    moved_to TEXT,                   -- ISO UTC 时刻或 NULL
    seq INTEGER NOT NULL,            -- 写入顺序，便于调试
    PRIMARY KEY (plan_id, original_date)
);
"""


class Store:
    """封装 SQLite 连接，负责计划与调整的持久化与全量加载。

    写入先进入事务，累计一定数量或 close() 时提交；
    不要求处理运行中崩溃，已提交的数据在重开后保留。
    """

    _COMMIT_EVERY = 1000

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM adjustments"
        ).fetchone()
        self._seq = row[0]
        self._pending = 0
        self._conn.commit()

    def _written(self) -> None:
        self._pending += 1
        if self._pending >= self._COMMIT_EVERY:
            self._conn.commit()
            self._pending = 0

    def save_plan(self, plan: Plan) -> None:
        self._conn.execute(
            "INSERT INTO plans(plan_id, start_date, publish_time, interval_days,"
            " end_date) VALUES(?, ?, ?, ?, ?)",
            (
                plan.plan_id,
                plan.start_date.isoformat(),
                plan.publish_time.isoformat(),
                plan.interval_days,
                plan.end_date.isoformat() if plan.end_date else None,
            ),
        )
        self._written()

    def save_adjustment(self, adj: Adjustment) -> None:
        self._seq += 1
        self._conn.execute(
            "INSERT INTO adjustments(plan_id, original_date, canceled, moved_to, seq)"
            " VALUES(?, ?, ?, ?, ?)"
            " ON CONFLICT(plan_id, original_date) DO UPDATE SET"
            " canceled=excluded.canceled, moved_to=excluded.moved_to,"
            " seq=excluded.seq",
            (
                adj.plan_id,
                adj.original_date.isoformat(),
                1 if adj.canceled else 0,
                adj.moved_to.isoformat() if adj.moved_to else None,
                self._seq,
            ),
        )
        self._written()

    def load_plans(self) -> dict[str, Plan]:
        plans: dict[str, Plan] = {}
        for row in self._conn.execute(
            "SELECT plan_id, start_date, publish_time, interval_days, end_date"
            " FROM plans"
        ):
            plan = Plan(
                plan_id=row[0],
                start_date=date.fromisoformat(row[1]),
                publish_time=time.fromisoformat(row[2]),
                interval_days=row[3],
                end_date=date.fromisoformat(row[4]) if row[4] else None,
            )
            plans[plan.plan_id] = plan
        return plans

    def load_adjustments(self) -> dict[tuple[str, date], Adjustment]:
        adjustments: dict[tuple[str, date], Adjustment] = {}
        for row in self._conn.execute(
            "SELECT plan_id, original_date, canceled, moved_to FROM adjustments"
        ):
            moved_to = None
            if row[3]:
                parsed = datetime.fromisoformat(row[3])
                moved_to = parsed.astimezone(timezone.utc)
            adj = Adjustment(
                plan_id=row[0],
                original_date=date.fromisoformat(row[1]),
                canceled=bool(row[2]),
                moved_to=moved_to,
            )
            adjustments[(adj.plan_id, adj.original_date)] = adj
        return adjustments

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()
