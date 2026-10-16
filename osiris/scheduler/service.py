"""Schedules stored in SQLite. They run while Osiris is open and catch up on startup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from osiris.errors import ExecutionError
from osiris.util import new_id, parse_iso, utc_iso, utcnow, zone


class SchedulerService:
    def __init__(self, db, execute, timezone_name: str) -> None:
        self.db = db
        self.execute = execute
        self.timezone_name = timezone_name

    def add(
        self,
        *,
        automation_id: str,
        name: str,
        cadence: str,
        time_of_day: str | None = None,
        days: list[int] | None = None,
        interval_minutes: int | None = None,
        timezone_name: str | None = None,
        retry_limit: int = 2,
        enabled: bool = True,
        parameters: dict | None = None,
        now: datetime | None = None,
    ) -> dict:
        if cadence not in {"daily", "weekly", "interval"}:
            raise ValueError("Schedule type must be daily, weekly, or interval.")
        tz_name = timezone_name or self.timezone_name
        zone(tz_name)
        if cadence == "interval":
            interval_minutes = max(5, int(interval_minutes or 60))
            time_of_day = None
        else:
            time_of_day = _valid_time(time_of_day or "08:00")
            interval_minutes = None
        if cadence == "weekly" and not days:
            raise ValueError("A weekly schedule needs at least one day.")
        moment = now or utcnow()
        schedule_id = new_id()
        record = {
            "id": schedule_id,
            "automation_id": automation_id,
            "name": name.strip() or "Scheduled automation",
            "cadence": cadence,
            "time_of_day": time_of_day,
            "days": list(days or []),
            "interval_minutes": interval_minutes,
            "parameters": parameters or {},
            "timezone": tz_name,
            "enabled": enabled,
            "next_run_at": utc_iso(compute_next(cadence, after=moment, time_of_day=time_of_day, days=days, interval_minutes=interval_minutes, timezone_name=tz_name)),
            "last_run_at": None,
            "last_status": None,
            "retry_limit": max(0, min(int(retry_limit), 5)),
            "retry_count": 0,
            "created_at": utc_iso(moment),
        }
        self.db.insert_schedule(record)
        self.db.add_audit("scheduler", "create", schedule_id, "saved", name)
        return self.db.get_schedule(schedule_id)

    def set_enabled(self, schedule_id: str, enabled: bool) -> None:
        self.db.set_schedule_enabled(schedule_id, enabled)
        self.db.add_audit("scheduler", "set_enabled", schedule_id, "enabled" if enabled else "disabled", "")

    def tick(self, now: datetime | None = None) -> list[dict]:
        if self.db.get_flag("emergency_stop") == "1":
            return []
        moment = now or utcnow()
        results = []
        for schedule in self.db.due_schedules(utc_iso(moment)):
            outcome = self._run_one(schedule, moment)
            if outcome is not None:
                results.append(outcome)
        return results

    def _run_one(self, schedule: dict, moment: datetime) -> dict | None:
        token = new_id()
        stale_before = utc_iso(moment - timedelta(minutes=10))
        if not self.db.claim_schedule(schedule["id"], token, utc_iso(moment), stale_before):
            return None
        record = self.db.get_automation(schedule["automation_id"])
        if record is None or not record["enabled"] or record["status"] != "approved":
            self._release_regular(schedule, moment, "skipped")
            self.db.add_notification(
                "warning",
                schedule["name"],
                "Skipped because the automation is disabled, archived, or not approved.",
            )
            return {"id": schedule["id"], "status": "skipped"}
        if self.db.has_running(schedule["automation_id"]):
            self.db.release_schedule(schedule["id"])
            self.db.add_audit("scheduler", "overlap", schedule["id"], "skipped", "A run is already in progress.")
            return {"id": schedule["id"], "status": "overlap"}
        try:
            outcome = self.execute(schedule)
            status = outcome.get("status", "failed") if isinstance(outcome, dict) else "failed"
            summary = outcome.get("summary", "") if isinstance(outcome, dict) else ""
        except ExecutionError as error:
            if "already running" in str(error):
                self.db.release_schedule(schedule["id"])
                self.db.add_audit("scheduler", "overlap", schedule["id"], "skipped", str(error))
                return {"id": schedule["id"], "status": "overlap"}
            status = "failed"
            summary = str(error)
        except Exception as error:
            status = "failed"
            summary = str(error)

        if status == "completed":
            self._release_regular(schedule, moment, "completed")
            return {"id": schedule["id"], "status": "completed", "summary": summary}

        retry_count = int(schedule["retry_count"]) + 1
        if retry_count <= int(schedule["retry_limit"]):
            retry_at = moment + timedelta(minutes=5)
            self.db.release_schedule(
                schedule["id"],
                next_run_at=utc_iso(retry_at),
                last_run_at=utc_iso(moment),
                last_status="failed",
                retry_count=retry_count,
            )
            self.db.add_notification(
                "error",
                schedule["name"],
                f"Run failed. Retry {retry_count} of {schedule['retry_limit']} is scheduled.",
            )
            return {"id": schedule["id"], "status": "retry", "summary": summary}

        self._release_regular(schedule, moment, "failed", reset_retry=True)
        self.db.add_notification(
            "error",
            schedule["name"],
            "Run failed and the retry limit was reached.",
        )
        return {"id": schedule["id"], "status": "failed", "summary": summary}

    def _release_regular(self, schedule: dict, moment: datetime, status: str, reset_retry: bool = True) -> None:
        nxt = compute_next(
            schedule["cadence"],
            after=moment,
            time_of_day=schedule.get("time_of_day"),
            days=schedule.get("days"),
            interval_minutes=schedule.get("interval_minutes"),
            timezone_name=schedule["timezone"],
        )
        self.db.release_schedule(
            schedule["id"],
            next_run_at=utc_iso(nxt),
            last_run_at=utc_iso(moment),
            last_status=status,
            retry_count=0 if reset_retry else schedule["retry_count"],
        )


def compute_next(
    cadence: str,
    *,
    after: datetime,
    time_of_day: str | None,
    days: list[int] | None,
    interval_minutes: int | None,
    timezone_name: str,
) -> datetime:
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    if cadence == "interval":
        minutes = max(5, int(interval_minutes or 60))
        return after + timedelta(minutes=minutes)
    tz = zone(timezone_name)
    local = after.astimezone(tz)
    hour, minute = _parse_hhmm(time_of_day or "08:00")
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    if cadence == "weekly":
        wanted = set(days or [])
        if not wanted:
            raise ValueError("A weekly schedule needs at least one day.")
        for _ in range(8):
            if candidate.weekday() in wanted and candidate > local:
                break
            candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("Time must look like HH:MM.")
    hour, minute = int(parts[0]), int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Time must look like HH:MM.")
    return hour, minute


def _valid_time(value: str) -> str:
    hour, minute = _parse_hhmm(value.strip())
    return f"{hour:02d}:{minute:02d}"


def describe_next(schedule: dict) -> str:
    raw = schedule.get("next_run_at")
    if not raw:
        return "Not scheduled"
    try:
        moment = parse_iso(raw).astimezone(zone(schedule["timezone"]))
    except Exception:
        return raw
    return moment.strftime("%Y-%m-%d %H:%M ") + schedule["timezone"]
