"""Small shared helpers."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(moment: datetime | None = None) -> str:
    moment = moment or utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception as error:
        raise ValueError(f"Unknown timezone: {name}") from error


def detect_timezone() -> str:
    """Pick an IANA timezone from the Windows display name when possible."""
    current = datetime.now().astimezone()
    windows_name = current.tzname() or ""
    mapping = {
        "Bangladesh Standard Time": "Asia/Dhaka",
        "India Standard Time": "Asia/Kolkata",
        "Pakistan Standard Time": "Asia/Karachi",
        "GMT Standard Time": "Europe/London",
        "Greenwich Standard Time": "Atlantic/Reykjavik",
        "Eastern Standard Time": "America/New_York",
        "Pacific Standard Time": "America/Los_Angeles",
        "Central Standard Time": "America/Chicago",
        "UTC": "UTC",
    }
    if windows_name in mapping:
        return mapping[windows_name]
    key = getattr(current.tzinfo, "key", None)
    if isinstance(key, str) and key:
        return key
    return "UTC"


def contains_bangla(text: str) -> bool:
    return any("\u0980" <= character <= "\u09ff" for character in text)


def clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))
