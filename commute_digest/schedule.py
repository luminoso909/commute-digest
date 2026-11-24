"""Beijing publication times shared by the mailer and AutoDL scheduler."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")
MORNING_TIME = time(5)
EVENING_TIME = time(17)
SLOT_INTERVAL = timedelta(hours=12)


def latest_slot(now: datetime) -> datetime:
    """Return the most recent publication time, including an exact boundary."""
    if now.utcoffset() is None:
        raise ValueError("Publication times must include a timezone.")
    local_now = now.astimezone(BEIJING)
    evening = datetime.combine(local_now.date(), EVENING_TIME, tzinfo=BEIJING)
    if local_now >= evening:
        return evening
    morning = datetime.combine(local_now.date(), MORNING_TIME, tzinfo=BEIJING)
    return morning if local_now >= morning else evening - timedelta(days=1)


def next_slot(now: datetime) -> datetime:
    """Return the first publication time strictly after now."""
    return latest_slot(now) + SLOT_INTERVAL


def is_slot(slot: datetime) -> bool:
    return slot.utcoffset() is not None and slot == latest_slot(slot)


def slot_title(slot: datetime, language: str) -> str:
    evening = slot.astimezone(BEIJING).time() == EVENING_TIME
    if language == "en":
        return "Commute Evening Digest" if evening else "Commute Morning Digest"
    return "通勤晚报" if evening else "通勤晨报"
