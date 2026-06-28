"""Selection revision and progressive refinement helpers for chat flows."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from zoneinfo import ZoneInfo

from app.services.appointment_time_normalization import (
    normalize_appointment_time_expression,
)
from app.services.chat_confirmation import is_simple_affirmative
from app.services.chat_offered_appointment_selection import (
    OfferedAppointmentView,
    constraints_have_signals,
    extract_appointment_selection_constraints,
    extract_option_number_index,
    extract_ordinal_index,
    load_offered_appointment_views,
    prepare_appointment_selection_message,
)

PENDING_APPOINTMENT_SELECTION_IDS_KEY = "pending_appointment_selection_ids"

_REVISION_PHRASE_PATTERNS = (
    r"\bon second thought\b",
    r"\bi meant\b",
    r"\binstead\b",
    r"\bno,\s*the\s+one\b",
    r"\bchange it to\b",
    r"\bmake it\b",
    r"\bi want the one\b",
    r"\bwait\b",
    r"\bactually\b",
)

_CROSS_FLOW_INTENT_KEYWORDS = (
    "cancel",
    "cancellation",
    "reschedule",
    "list my",
    "look up",
    "show my",
    "my appointments",
    "book an",
    "book a ",
    "schedule",
)

_PLAIN_REJECTIONS = frozenset(
    {
        "no",
        "nope",
        "nah",
        "no thanks",
        "no thank you",
    },
)

_BARE_INTEGER_PATTERN = re.compile(r"^\d{1,2}$")
_ORDINAL_SLOT_KEYWORDS = {
    "the first one": 0,
    "first one": 0,
    "the first": 0,
    "the second one": 1,
    "second one": 1,
    "the second": 1,
}


def has_selection_revision_phrase(normalized_message: str) -> bool:
    return any(
        re.search(pattern, normalized_message) for pattern in _REVISION_PHRASE_PATTERNS
    )


def is_plain_rejection(message: str) -> bool:
    normalized = message.lower().strip().rstrip(".,!?")
    return normalized in _PLAIN_REJECTIONS


def message_looks_like_cross_flow_intent_switch(normalized_message: str) -> bool:
    has_marker = any(
        marker in normalized_message
        for marker in ("instead", "actually", "on second thought", "wait")
    )
    if not has_marker:
        return False
    return any(keyword in normalized_message for keyword in _CROSS_FLOW_INTENT_KEYWORDS)


def filter_offered_by_pending_ids(
    offered: Sequence[OfferedAppointmentView],
    chat_context: dict[str, Any],
) -> tuple[tuple[OfferedAppointmentView, ...], bool]:
    raw_pending = chat_context.get(PENDING_APPOINTMENT_SELECTION_IDS_KEY)
    if not isinstance(raw_pending, list) or not raw_pending:
        return tuple(offered), False

    pending_ids = {str(item) for item in raw_pending}
    filtered = tuple(
        item for item in offered if item.appointment_id in pending_ids
    )
    if not filtered:
        return tuple(offered), False
    return filtered, True


def pending_selection_context_updates(
    matching: Sequence[OfferedAppointmentView],
) -> dict[str, Any]:
    return {
        PENDING_APPOINTMENT_SELECTION_IDS_KEY: [
            item.appointment_id for item in matching
        ],
    }


def clear_pending_selection_context_updates() -> dict[str, Any]:
    return {PENDING_APPOINTMENT_SELECTION_IDS_KEY: None}


def format_pending_subset_no_match_message(
    *,
    scope_description: str | None,
    candidates: Sequence[OfferedAppointmentView],
) -> str:
    options = "\n".join(
        f"{index}. {item.summary}."
        for index, item in enumerate(candidates, start=1)
    )
    scope = scope_description or "those"
    return (
        f"That didn't match any of the {scope} appointments I listed. "
        f"Please choose one of:\n\n"
        f"{options}"
    )


def is_appointment_selection_revision_message(
    message: str,
    *,
    chat_context: dict[str, Any],
    clinic_timezone: ZoneInfo,
    clinic_today: Any,
    action_keywords: Sequence[str],
) -> bool:
    if not chat_context.get("offered_appointments"):
        return False
    if is_simple_affirmative(message) or is_plain_rejection(message):
        return False

    normalized_message = message.lower().strip()
    if not has_selection_revision_phrase(normalized_message):
        return False

    selection_message = prepare_appointment_selection_message(
        message,
        action_keywords=action_keywords,
    )
    offered_all = load_offered_appointment_views(
        chat_context,
        clinic_timezone=clinic_timezone,
    )
    offered_candidates, _ = filter_offered_by_pending_ids(offered_all, chat_context)
    constraints = extract_appointment_selection_constraints(
        message=selection_message,
        normalized_message=selection_message.lower().strip(),
        offered=offered_candidates,
        clinic_today=clinic_today,
    )
    return constraints_have_signals(constraints)


def has_offered_slot_selection_signals(
    message: str,
    normalized_message: str,
    offered_slots: Sequence[dict[str, Any]],
) -> bool:
    if extract_option_number_index(
        normalized_message,
        option_count=len(offered_slots),
    ) is not None:
        return True

    if extract_ordinal_index(normalized_message, option_count=len(offered_slots)) is not None:
        return True

    for keyword, index in _ORDINAL_SLOT_KEYWORDS.items():
        if keyword in normalized_message and index < len(offered_slots):
            return True

    stripped = message.strip()
    if _BARE_INTEGER_PATTERN.fullmatch(stripped):
        number = int(stripped)
        if 1 <= number <= len(offered_slots):
            return True
        normalized_time = normalize_appointment_time_expression(
            stripped,
            allow_bare_hour=True,
        )
        if normalized_time is not None:
            return any(
                slot.get("display_time") == normalized_time.value
                for slot in offered_slots
            )

    normalized_time = normalize_appointment_time_expression(
        message,
        allow_bare_hour=False,
    )
    if normalized_time is None:
        normalized_time = normalize_appointment_time_expression(
            message,
            allow_bare_hour=True,
        )
    if normalized_time is not None:
        return any(
            slot.get("display_time") == normalized_time.value for slot in offered_slots
        )

    return False


def is_slot_selection_revision_message(
    message: str,
    *,
    chat_context: dict[str, Any],
    has_active_hold: bool,
) -> bool:
    if not has_active_hold:
        return False

    offered_slots = chat_context.get("offered_slots")
    if not isinstance(offered_slots, list) or not offered_slots:
        return False
    if is_simple_affirmative(message) or is_plain_rejection(message):
        return False

    normalized_message = message.lower().strip()
    if message_looks_like_cross_flow_intent_switch(normalized_message):
        return False
    if not has_selection_revision_phrase(normalized_message):
        return False

    return has_offered_slot_selection_signals(
        message,
        normalized_message,
        offered_slots,
    )
