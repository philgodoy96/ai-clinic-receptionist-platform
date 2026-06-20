from __future__ import annotations


def normalize_phone_digits(phone_number: str) -> str:
    return "".join(character for character in phone_number if character.isdigit())
