"""America/Chicago local-time to UTC conversion with DST edge rules."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
UTC = timezone.utc


def local_to_utc(day: date, wall: time) -> datetime:
    """Convert a Chicago local date + wall time to a UTC datetime.

    Spring-forward gap: nonexistent wall times are shifted forward by the
    skipped duration. Fall-back overlap: ambiguous wall times resolve to the
    earlier UTC instant.
    """
    naive = datetime.combine(day, wall)
    # fold=0 gives exactly the required semantics for both edges:
    #  - nonexistent wall time: interpreted with the pre-gap offset, which is
    #    equivalent to shifting the wall time forward by the skipped duration;
    #  - ambiguous wall time: interpreted as the first (earlier-UTC) occurrence.
    return naive.replace(tzinfo=CHICAGO, fold=0).astimezone(UTC)
