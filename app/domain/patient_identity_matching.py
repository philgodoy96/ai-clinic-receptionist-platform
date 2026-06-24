from __future__ import annotations

import re

from app.models.scheduling import Patient

_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_patient_name(name: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", name.strip())


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_optional_phone(phone: str | None) -> str | None:
    if phone is None:
        return None

    normalized = phone.strip()
    return normalized or None


def name_tokens(name: str) -> tuple[str, ...]:
    normalized = normalize_patient_name(name).lower()
    if not normalized:
        return ()

    return tuple(normalized.split())


def is_exact_name_match(spoken_name: str, stored_name: str) -> bool:
    return (
        normalize_patient_name(spoken_name).lower()
        == normalize_patient_name(stored_name).lower()
    )


def _tokens_are_ordered_subsequence(
    shorter: tuple[str, ...],
    longer: tuple[str, ...],
) -> bool:
    if not shorter:
        return False

    if len(shorter) > len(longer):
        return False

    index = 0
    for token in longer:
        if token == shorter[index]:
            index += 1
            if index == len(shorter):
                return True

    return False


def is_possible_name_match(spoken_name: str, stored_name: str) -> bool:
    if is_exact_name_match(spoken_name, stored_name):
        return True

    spoken_tokens = name_tokens(spoken_name)
    stored_tokens = name_tokens(stored_name)
    if not spoken_tokens or not stored_tokens:
        return False

    return _tokens_are_ordered_subsequence(
        spoken_tokens,
        stored_tokens,
    ) or _tokens_are_ordered_subsequence(stored_tokens, spoken_tokens)


def classify_name_match(spoken_name: str, stored_name: str) -> str | None:
    if is_exact_name_match(spoken_name, stored_name):
        return "exact"

    if is_possible_name_match(spoken_name, stored_name):
        return "possible"

    return None


def build_confirmation_question(patient: Patient) -> str:
    return (
        f"I found a possible existing profile for {patient.full_name}. "
        "Is that you?"
    )
