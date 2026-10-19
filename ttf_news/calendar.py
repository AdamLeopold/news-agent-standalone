"""Endex settlement windows; timestamps are never inferred from date-only news."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from dateutil.easter import easter
import re

EUROPE = ZoneInfo("Europe/Amsterdam")
SINGAPORE = ZoneInfo("Asia/Singapore")
UTC = timezone.utc
CALENDAR_SOURCE = "https://www.ice.com/publicdocs/ICE_Endex_Trading_Schedule.pdf"


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).astimezone(UTC).isoformat()


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = re.sub(r"([+-]\d{2})(\d{2})$",r"\1:\2",value.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include a timezone; date-only news has no exact timestamp")
    return dt.astimezone(UTC)


def trading_day(day: date) -> bool:
    spring = easter(day.year)
    closed = {date(day.year, 1, 1), date(day.year, 12, 25), spring - timedelta(days=2), spring + timedelta(days=1)}
    return day.weekday() < 5 and day not in closed


def previous_day(day: date) -> date:
    day -= timedelta(days=1)
    while not trading_day(day):
        day -= timedelta(days=1)
    return day


def next_day(day: date, include: bool = False) -> date:
    if not include:
        day += timedelta(days=1)
    while not trading_day(day):
        day += timedelta(days=1)
    return day


def cutoff(day: date) -> datetime:
    if not trading_day(day):
        raise ValueError("No settlement window on a closed day")
    early = (day.month == 12 and day.day in (24, 31))
    return datetime.combine(day, time(14, 0) if early else time(17, 15), EUROPE).astimezone(UTC)


def session_at(value: datetime | str | None = None) -> dict:
    moment = parse_time(value) if value is not None else utcnow()
    day = moment.astimezone(EUROPE).date()
    day = next_day(day, include=True)
    if moment >= cutoff(day):
        day = next_day(day)
    end = cutoff(day)
    start = cutoff(previous_day(day))
    return {
        "trade_date": day.isoformat(), "window_start": iso(start), "window_end": iso(end),
        "cutoff_utc": iso(end), "cutoff_local": end.astimezone(EUROPE).isoformat(),
        "cutoff_singapore": end.astimezone(SINGAPORE).isoformat(), "display_timezone": "Europe/Amsterdam",
        "seconds_to_close": max(0, int((end-moment).total_seconds())),
        "calendar_source": CALENDAR_SOURCE, "calendar_quality": "official_2026_2027" if day.year in (2026, 2027) else "historical_rule_proxy",
        "boundary": "[previous_settlement, current_settlement)",
    }


def session_for_day(day: date | str) -> dict:
    day = date.fromisoformat(day) if isinstance(day, str) else day
    return session_at(cutoff(day) - timedelta(microseconds=1))

