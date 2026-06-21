from __future__ import annotations

from typing import Any

SECRET_LIKE_KEY_FRAGMENTS = (
    "aws_secret_access_key",
    "api_key",
)

SECRET_LIKE_EXACT_KEYS = {
    "token",
    "access_token",
    "auth_token",
    "refresh_token",
    "bearer_token",
}

SAFE_KEY_NAMES = {
    "input_tokens",
    "output_tokens",
}


def is_secret_like_key(key: str) -> bool:
    normalized_key = key.lower()
    if normalized_key in SAFE_KEY_NAMES:
        return False
    if normalized_key in SECRET_LIKE_EXACT_KEYS:
        return True
    if any(fragment in normalized_key for fragment in SECRET_LIKE_KEY_FRAGMENTS):
        return True
    if normalized_key.endswith("_token"):
        return True
    return False


def collect_secret_like_key_paths(value: object, path: str = "") -> list[str]:
    matches: list[str] = []

    if isinstance(value, dict):
        for key, nested in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if is_secret_like_key(str(key)):
                matches.append(key_path)
            matches.extend(collect_secret_like_key_paths(nested, key_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            matches.extend(collect_secret_like_key_paths(item, f"{path}[{index}]"))

    return matches


def assert_report_excludes_secret_like_keys(report: dict[str, Any]) -> None:
    secret_like_keys = collect_secret_like_key_paths(report)
    assert secret_like_keys == []
