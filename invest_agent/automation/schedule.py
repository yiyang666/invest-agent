"""Pure cadence evaluation for one multi-frequency maintenance runner."""

from __future__ import annotations

from datetime import datetime, time
import json
from pathlib import Path
from typing import Mapping, Sequence


WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def load_schedule_jobs(
    *, fund_config_path: str | Path, market_config_path: str | Path
) -> tuple[dict[str, object], ...]:
    fund = json.loads(Path(fund_config_path).read_text(encoding="utf-8"))
    market = json.loads(Path(market_config_path).read_text(encoding="utf-8"))
    recommendation = fund.get("schedule_recommendation")
    if not isinstance(recommendation, Mapping):
        raise ValueError("fund sync config requires schedule_recommendation")
    jobs: list[dict[str, object]] = [
        {
            "job_id": "fund_data_daily",
            "kind": "fund_data_sync",
            "cadence": "business_daily",
            "local_time": recommendation.get("local_time", "23:30"),
            "config_path": str(Path(fund_config_path)),
        }
    ]
    raw_jobs = market.get("jobs")
    if not isinstance(raw_jobs, list):
        raise ValueError("market sync config requires jobs")
    for raw in raw_jobs:
        if not isinstance(raw, Mapping):
            raise ValueError("market job must be one object")
        job = dict(raw)
        job["kind"] = "market_data_sync"
        job["config_path"] = str(Path(market_config_path))
        jobs.append(job)
    ids = [str(job.get("job_id", "")) for job in jobs]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("maintenance job IDs must be non-empty and unique")
    return tuple(jobs)


def _target_time(job: Mapping[str, object]) -> time:
    value = str(job.get("local_time", "00:00"))
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


def cadence_period(job: Mapping[str, object], as_of: datetime) -> str | None:
    cadence = job.get("cadence")
    current_time = as_of.timetz().replace(tzinfo=None)
    target = _target_time(job)
    if cadence == "business_daily":
        if as_of.weekday() >= 5 or current_time < target:
            return None
        return as_of.date().isoformat()
    if cadence == "weekly":
        target_weekday = WEEKDAYS.get(str(job.get("weekday")))
        if target_weekday is None:
            raise ValueError(f"Invalid weekly weekday: {job.get('weekday')}")
        if as_of.weekday() < target_weekday:
            return None
        if as_of.weekday() == target_weekday and current_time < target:
            return None
        iso = as_of.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if cadence == "monthly":
        day = int(job.get("calendar_day", 1))
        if as_of.day < day or (as_of.day == day and current_time < target):
            return None
        return f"{as_of.year}-{as_of.month:02d}"
    if cadence == "quarterly":
        months = tuple(int(value) for value in job.get("months", (1, 4, 7, 10)))
        quarter = (as_of.month - 1) // 3 + 1
        start_month = 1 + (quarter - 1) * 3
        if start_month not in months:
            return None
        day = int(job.get("calendar_day", 1))
        if as_of.month == start_month and (
            as_of.day < day or (as_of.day == day and current_time < target)
        ):
            return None
        return f"{as_of.year}-Q{quarter}"
    raise ValueError(f"Unsupported maintenance cadence: {cadence}")


def due_jobs(
    jobs: Sequence[Mapping[str, object]],
    state: Mapping[str, object],
    as_of: datetime,
) -> tuple[dict[str, object], ...]:
    raw_state_jobs = state.get("jobs", {})
    state_jobs = raw_state_jobs if isinstance(raw_state_jobs, Mapping) else {}
    due: list[dict[str, object]] = []
    for job in jobs:
        period = cadence_period(job, as_of)
        if period is None:
            continue
        prior = state_jobs.get(str(job["job_id"]), {})
        last_success = prior.get("last_success_period") if isinstance(prior, Mapping) else None
        if last_success == period:
            continue
        copy = dict(job)
        copy["due_period"] = period
        due.append(copy)
    return tuple(due)
