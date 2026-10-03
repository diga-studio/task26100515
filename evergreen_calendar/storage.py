"""SQLite persistence for plans and single-occurrence adjustments."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timezone
from typing import List

from .models import Adjustment, RotationPlan

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    start_date TEXT NOT NULL,
    publish_time TEXT NOT NULL,
    interval_days INTEGER NOT NULL CHECK (interval_days IN (7, 14, 30)),
    end_date TEXT
);
CREATE TABLE IF NOT EXISTS adjustments (
    plan_id TEXT NOT NULL,
    orig_date TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('cancel', 'move')),
    target_utc TEXT,
    PRIMARY KEY (plan_id, orig_date)
);
"""


class Storage:
    """Owns the SQLite connection and schema."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._pending = 0

    def _mark_dirty(self) -> None:
        self._pending += 1
        if self._pending >= 1000:
            self.flush()

    def flush(self) -> None:
        if self._pending:
            self._conn.commit()
            self._pending = 0

    def insert_plan(self, plan: RotationPlan) -> None:
        self._conn.execute(
            "INSERT INTO plans (plan_id, start_date, publish_time,"
            " interval_days, end_date) VALUES (?, ?, ?, ?, ?)",
            (
                plan.plan_id,
                plan.start_date.isoformat(),
                plan.publish_time.isoformat(),
                plan.interval_days,
                plan.end_date.isoformat() if plan.end_date else None,
            ),
        )
        self._mark_dirty()

    def all_plans(self) -> List[RotationPlan]:
        rows = self._conn.execute(
            "SELECT plan_id, start_date, publish_time, interval_days, end_date"
            " FROM plans"
        ).fetchall()
        return [
            RotationPlan(
                plan_id=row[0],
                start_date=date.fromisoformat(row[1]),
                publish_time=time.fromisoformat(row[2]),
                interval_days=row[3],
                end_date=date.fromisoformat(row[4]) if row[4] else None,
            )
            for row in rows
        ]

    def get_plan(self, plan_id: str) -> RotationPlan:
        row = self._conn.execute(
            "SELECT plan_id, start_date, publish_time, interval_days, end_date"
            " FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown plan_id: {plan_id!r}")
        return RotationPlan(
            plan_id=row[0],
            start_date=date.fromisoformat(row[1]),
            publish_time=time.fromisoformat(row[2]),
            interval_days=row[3],
            end_date=date.fromisoformat(row[4]) if row[4] else None,
        )

    def upsert_adjustment(self, adjustment: Adjustment) -> None:
        target = adjustment.target_utc
        if target is not None:
            target = target.astimezone(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO adjustments (plan_id, orig_date, action, target_utc)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (plan_id, orig_date)"
            " DO UPDATE SET action = excluded.action,"
            "               target_utc = excluded.target_utc",
            (
                adjustment.plan_id,
                adjustment.orig_date.isoformat(),
                adjustment.action,
                target,
            ),
        )
        self._mark_dirty()

    def all_adjustments(self) -> List[Adjustment]:
        rows = self._conn.execute(
            "SELECT plan_id, orig_date, action, target_utc FROM adjustments"
        ).fetchall()
        return [
            Adjustment(
                plan_id=row[0],
                orig_date=date.fromisoformat(row[1]),
                action=row[2],
                target_utc=(
                    datetime.fromisoformat(row[3]).astimezone(timezone.utc)
                    if row[3]
                    else None
                ),
            )
            for row in rows
        ]

    def close(self) -> None:
        self.flush()
        self._conn.close()
