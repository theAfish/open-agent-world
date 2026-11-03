"""Timestamp-based timepieces. No worker is needed while a card is off screen."""
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import time

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_COUNT = 999_999_999


def city_zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("Choose a valid IANA time zone, for example Asia/Shanghai or America/New_York") from error


def display_zone(value):
    return city_zone(value["time_zone"]) if value.get("time_zone") else timezone(timedelta(minutes=value["offset_minutes"]))


class EmptyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    time_zone: str = Field(default="Asia/Shanghai", max_length=100)
    # Retained only for documents saved before city-based zones were introduced.
    offset_minutes: int = Field(default=480, ge=-720, le=840)
    offset_ms: int = Field(default=0, ge=-8_640_000_000_000, le=8_640_000_000_000)
    selected_date: str = Field(default="", max_length=10)
    duration_ms: int = Field(default=300_000, ge=1000, le=604_800_000)
    elapsed_ms: int = Field(default=0, ge=0, le=8_640_000_000_000)
    started_at_ms: int | None = Field(default=None, ge=0)
    updated_at_ms: int = Field(default=0, ge=0)
    count: int = Field(default=0, ge=-MAX_COUNT, le=MAX_COUNT)
    laps: list[int] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def preserve_saved_offsets(cls, value):
        if isinstance(value, dict) and "time_zone" not in value and "offset_minutes" in value:
            return {**value, "time_zone": "Asia/Shanghai" if value["offset_minutes"] == 480 else ""}
        return value

    @field_validator("time_zone")
    @classmethod
    def valid_zone(cls, value):
        if value:
            city_zone(value)
        return value

    @field_validator("selected_date")
    @classmethod
    def valid_date(cls, value):
        if value:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError("Use a calendar date in YYYY-MM-DD format")
        return value


class PomodoroDocument(TimeDocument):
    duration_ms: int = Field(default=1_500_000, ge=1000, le=604_800_000)


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class NoArguments(Arguments):
    pass


class Duration(Arguments):
    seconds: int = Field(ge=1, le=604800)


class Count(Arguments):
    value: int = Field(ge=-MAX_COUNT, le=MAX_COUNT)


class Step(Arguments):
    amount: int = Field(default=1, ge=1, le=MAX_COUNT)


class SetTime(Arguments):
    timestamp_ms: int | None = Field(default=None, ge=0, le=8_640_000_000_000)
    local_time: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    @model_validator(mode="after")
    def one_time(self):
        if (self.timestamp_ms is None) == (self.local_time is None):
            raise ValueError("Supply either timestamp_ms or local_time")
        return self


class Zone(Arguments):
    time_zone: str = Field(min_length=1, max_length=100)

    @field_validator("time_zone")
    @classmethod
    def valid_zone(cls, value):
        city_zone(value)
        return value


class SetDate(Arguments):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


def now_ms():
    return time.time_ns() // 1_000_000


def elapsed(value, now):
    return value["elapsed_ms"] + (max(0, now - value["started_at_ms"]) if value["started_at_ms"] is not None else 0)


def snapshot(kind, value, now):
    result = {**value, "server_now_ms": now}
    if kind in {"digital", "clock", "calendar"}:
        stamp = now + value["offset_ms"]
        local = datetime.fromtimestamp(stamp / 1000, display_zone(value))
        result.update(timestamp_ms=stamp, iso_time=local.isoformat(), today=local.date().isoformat(),
                      offset_minutes=int(local.utcoffset().total_seconds() / 60))
    if kind in {"countdown", "pomodoro", "stopwatch"}:
        total = elapsed(value, now)
        finished = kind != "stopwatch" and total >= value["duration_ms"]
        result.update(value_ms=total if kind == "stopwatch" else max(0, value["duration_ms"] - total),
                      finished=finished, running=value["started_at_ms"] is not None and not finished)
    return result


def handler(kind, operation):
    def apply(value, arguments):
        model = {"set_duration": Duration, "set_count": Count, "increment": Step, "decrement": Step,
                 "set_time": SetTime, "set_zone": Zone, "set_date": SetDate}.get(operation, NoArguments)
        args = model.model_validate(arguments).model_dump()
        now = now_ms()
        if operation == "read":
            return snapshot(kind, value, now)
        result = {**value, "updated_at_ms": now}
        if operation == "set_time":
            stamp = args["timestamp_ms"]
            if args["local_time"] is not None:
                local = datetime.fromisoformat(args["local_time"])
                zone = display_zone(value)
                # In a repeated autumn hour, use the first occurrence (fold=0).
                stamp = round(local.replace(tzinfo=zone, fold=0).timestamp() * 1000)
                if datetime.fromtimestamp(stamp / 1000, zone).replace(tzinfo=None) != local:
                    raise ValueError("This local time does not exist because of a daylight-saving transition")
                if not 0 <= stamp <= 8_640_000_000_000:
                    raise ValueError("Time is outside the supported range")
            result["offset_ms"] = stamp - now
        elif operation == "set_zone":
            result["time_zone"] = args["time_zone"]
        elif operation == "set_date":
            date.fromisoformat(args["date"])
            result["selected_date"] = args["date"]
        elif operation == "set_duration":
            result.update(duration_ms=args["seconds"] * 1000, elapsed_ms=0, started_at_ms=None, laps=[])
        elif operation in {"increment", "decrement", "set_count"}:
            count = args["value"] if operation == "set_count" else value["count"] + args["amount"] * (1 if operation == "increment" else -1)
            if abs(count) > MAX_COUNT:
                raise ValueError("Count must stay between -999999999 and 999999999")
            result["count"] = count
        elif operation == "start":
            if value["started_at_ms"] is None or (kind != "stopwatch" and elapsed(value, now) >= value["duration_ms"]):
                result["started_at_ms"] = now
                if kind != "stopwatch" and elapsed(value, now) >= value["duration_ms"]:
                    result["elapsed_ms"] = 0
        elif operation == "pause":
            total = elapsed(value, now)
            result.update(elapsed_ms=total if kind == "stopwatch" else min(total, value["duration_ms"]), started_at_ms=None)
        elif operation == "lap":
            result["laps"] = [*value["laps"], elapsed(value, now)][-20:]
        elif operation == "reset":
            if kind == "counter":
                result["count"] = 0
            elif kind in {"digital", "clock"}:
                result["offset_ms"] = 0
            elif kind == "calendar":
                result["selected_date"] = ""
            else:
                result.update(elapsed_ms=0, started_at_ms=None, laps=[])
        return result
    return apply


OPERATIONS = {
    "read": (NoArguments, "Read time or count", "Read this card's current time, date, remaining/elapsed milliseconds or count."),
    "set_time": (SetTime, "Set time", "Set time using either timestamp_ms (Unix milliseconds) or local_time (YYYY-MM-DDTHH:MM:SS in this card's city time zone). Does not change the system clock. Repeated autumn times use the first occurrence; nonexistent spring times are rejected."),
    "set_zone": (Zone, "Set time zone", "Choose a city's IANA time zone, for example Asia/Shanghai, Europe/London or America/New_York. Daylight saving follows that location's rules."),
    "set_date": (SetDate, "Select date", "Select a valid calendar date in YYYY-MM-DD format."),
    "set_duration": (Duration, "Set duration", "Set duration in seconds (1 to 604800). Stops and resets this timer."),
    "start": (NoArguments, "Start timer", "Start or resume this timer. Starting an expired countdown begins a new cycle."),
    "pause": (NoArguments, "Pause timer", "Stop this timer and retain elapsed time."),
    "reset": (NoArguments, "Reset", "Reset this card's timer, count, selected date or custom clock offset."),
    "lap": (NoArguments, "Record lap", "Record the cumulative elapsed time. Retains the latest 20 laps."),
    "increment": (Step, "Increment count", "Add a positive amount to the count (default +1)."),
    "decrement": (Step, "Decrement count", "Subtract a positive amount from the count (default -1)."),
    "set_count": (Count, "Set count", "Set the integer count between -999999999 and 999999999."),
}
