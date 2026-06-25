from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any


class AvailabilityCheckStatus(StrEnum):
    AVAILABLE = "available"
    NO_MATCHING_SLOTS = "no_matching_slots"
    OUTSIDE_BOOKING_HORIZON = "outside_booking_horizon"
    NEEDS_DATE_CLARIFICATION = "needs_date_clarification"


@dataclass(frozen=True, slots=True)
class BookingWindow:
    earliest_bookable_date: date
    latest_bookable_date: date
    timezone: str

    def to_dict(self) -> dict[str, str]:
        return {
            "earliest_bookable_date": self.earliest_bookable_date.isoformat(),
            "latest_bookable_date": self.latest_bookable_date.isoformat(),
            "timezone": self.timezone,
        }


def format_friendly_date(value: date) -> str:
    return f"{value.strftime('%B')} {value.day}"


def build_outside_booking_horizon_response_text(latest_bookable_date: date) -> str:
    friendly_date = format_friendly_date(latest_bookable_date)
    return (
        f"The clinic schedule is currently open through {friendly_date}. "
        "I can check dates within that window."
    )


NO_MATCHING_SLOTS_RESPONSE_TEXT = (
    "I'm not seeing any openings for that day. "
    "Would you like me to check another day or time?"
)

NEEDS_DATE_CLARIFICATION_RESPONSE_TEXT = (
    "Sure. Is there a specific day you'd like me to check?"
)


def build_availability_status_payload(
    *,
    status: AvailabilityCheckStatus,
    booking_window: BookingWindow | None = None,
    suggested_response_text: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"availability_status": status.value}
    if booking_window is not None:
        payload["booking_window"] = booking_window.to_dict()
    if suggested_response_text is not None:
        payload["suggested_response_text"] = suggested_response_text
    return payload
