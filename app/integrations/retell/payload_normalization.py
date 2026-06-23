from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.services.clock import Clock, SystemClock

_LIFECYCLE_CALL_METADATA_FIELDS = (
    "agent_id",
    "agent_version",
    "call_status",
    "call_type",
    "disconnect_reason",
    "disconnection_reason",
    "duration_ms",
    "duration_seconds",
    "end_reason",
)

_LIFECYCLE_STRING_METADATA_FIELDS = frozenset(
    {
        "agent_id",
        "agent_version",
        "call_status",
        "call_type",
        "disconnect_reason",
        "disconnection_reason",
        "end_reason",
        "occurred_at_source",
    },
)

_LIFECYCLE_BLOCKED_METADATA_FIELDS = frozenset(
    {
        "access_token",
    },
)

_TOOL_CALL_ID_FIELDS = (
    "tool_call_id",
    "tool_callId",
    "invocation_id",
    "id",
)


class RetellPayloadNormalizationError(ValueError):
    """Raised when a Retell-native payload cannot be normalized safely."""


def normalize_retell_tool_payload(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        msg = "Retell payload must be a JSON object."
        raise RetellPayloadNormalizationError(msg)

    if _is_normalized_tool_payload(raw):
        return _normalize_normalized_tool_payload(raw)

    if _is_native_tool_payload(raw):
        return _normalize_native_tool_payload(raw)

    if _is_args_only_tool_payload(raw):
        msg = (
            "Retell tool payload is missing tool name and call id. "
            'Disable "Payload: args only" in the Retell dashboard.'
        )
        raise RetellPayloadNormalizationError(msg)

    msg = "Retell tool payload is invalid."
    raise RetellPayloadNormalizationError(msg)


def normalize_retell_lifecycle_payload(
    raw: dict[str, Any],
    *,
    clock: Clock | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        msg = "Retell payload must be a JSON object."
        raise RetellPayloadNormalizationError(msg)

    resolved_clock = clock or SystemClock()

    if _is_normalized_lifecycle_payload(raw):
        return dict(raw)

    if not _is_native_lifecycle_payload(raw):
        msg = "Retell lifecycle payload is invalid."
        raise RetellPayloadNormalizationError(msg)

    return _normalize_native_lifecycle_payload(raw, clock=resolved_clock)


def _is_normalized_tool_payload(raw: dict[str, Any]) -> bool:
    return bool((raw.get("tool_name") or "").strip()) and bool(
        (raw.get("provider_call_id") or "").strip(),
    )


def _is_native_tool_payload(raw: dict[str, Any]) -> bool:
    tool_name = (raw.get("name") or "").strip()
    provider_call_id = _extract_call_id(raw.get("call"))
    return bool(tool_name) and bool(provider_call_id)


def _is_args_only_tool_payload(raw: dict[str, Any]) -> bool:
    return not _is_normalized_tool_payload(raw) and not _is_native_tool_payload(raw)


def _normalize_normalized_tool_payload(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(raw)
    arguments = normalized.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    normalized["arguments"] = arguments

    provider_call_id = str(normalized["provider_call_id"]).strip()
    tool_name = str(normalized["tool_name"]).strip()
    normalized["provider_call_id"] = provider_call_id
    normalized["tool_name"] = tool_name
    normalized["tool_call_id"] = _resolve_tool_call_id(
        raw=raw,
        provider_call_id=provider_call_id,
        tool_name=tool_name,
        arguments=arguments,
    )
    return normalized


def _normalize_native_tool_payload(raw: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(raw["name"]).strip()
    provider_call_id = _extract_call_id(raw.get("call"))
    if not provider_call_id:
        msg = "Retell tool payload is missing call id."
        raise RetellPayloadNormalizationError(msg)

    arguments = raw.get("args")
    if not isinstance(arguments, dict):
        arguments = {}

    tool_call_id = _resolve_tool_call_id(
        raw=raw,
        provider_call_id=provider_call_id,
        tool_name=tool_name,
        arguments=arguments,
    )

    return {
        "provider_call_id": provider_call_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "arguments": arguments,
        "occurred_at": raw.get("occurred_at"),
    }


def _resolve_tool_call_id(
    *,
    raw: dict[str, Any],
    provider_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    for field_name in _TOOL_CALL_ID_FIELDS:
        candidate = raw.get(field_name)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    call = raw.get("call")
    if isinstance(call, dict):
        for field_name in _TOOL_CALL_ID_FIELDS:
            candidate = call.get(field_name)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

    metadata = raw.get("metadata")
    if isinstance(metadata, dict):
        for field_name in _TOOL_CALL_ID_FIELDS:
            candidate = metadata.get(field_name)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

    canonical_args = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(
        f"{provider_call_id}:{tool_name}:{canonical_args}".encode(),
    ).hexdigest()[:24]
    return f"derived-{digest}"


def _is_normalized_lifecycle_payload(raw: dict[str, Any]) -> bool:
    event = (raw.get("event") or raw.get("event_type") or "").strip()
    has_call_reference = bool((raw.get("call_id") or "").strip()) or isinstance(
        raw.get("call"),
        dict,
    )
    return bool(event) and bool(raw.get("occurred_at")) and has_call_reference


def _is_native_lifecycle_payload(raw: dict[str, Any]) -> bool:
    event = (raw.get("event") or raw.get("event_type") or "").strip()
    has_call_reference = bool((raw.get("call_id") or "").strip()) or isinstance(
        raw.get("call"),
        dict,
    )
    return bool(event) and has_call_reference


def _normalize_native_lifecycle_payload(
    raw: dict[str, Any],
    *,
    clock: Clock,
) -> dict[str, Any]:
    event = str(raw.get("event") or raw.get("event_type")).strip()
    call_raw = raw.get("call")
    call: dict[str, Any] = call_raw if isinstance(call_raw, dict) else {}
    call_id = (raw.get("call_id") or _extract_call_id(call) or "").strip()
    if not call_id:
        msg = "Retell lifecycle payload is missing call id."
        raise RetellPayloadNormalizationError(msg)

    occurred_at, occurred_at_source = _resolve_lifecycle_occurred_at(
        event=event,
        raw=raw,
        call=call,
        clock=clock,
    )

    normalized: dict[str, Any] = {
        "event": event,
        "call_id": call_id,
        "occurred_at": occurred_at.isoformat(),
        "event_id": raw.get("event_id") or raw.get("provider_event_id"),
    }

    if occurred_at_source is not None:
        normalized["occurred_at_source"] = occurred_at_source

    for field_name in _LIFECYCLE_CALL_METADATA_FIELDS:
        if field_name in raw:
            source_value = raw[field_name]
        elif field_name in call:
            source_value = call[field_name]
        else:
            continue

        coerced_value = _coerce_lifecycle_metadata_field(field_name, source_value)
        if coerced_value is not None:
            normalized[field_name] = coerced_value

    if call:
        normalized["call"] = {
            "call_id": call_id,
            "direction": _optional_string(call.get("direction")),
            "from_number": _optional_string(call.get("from_number")),
            "to_number": _optional_string(call.get("to_number")),
        }

    return normalized


def _resolve_lifecycle_occurred_at(
    *,
    event: str,
    raw: dict[str, Any],
    call: dict[str, Any],
    clock: Clock,
) -> tuple[datetime, str | None]:
    timestamp_candidates: list[Any] = []

    if event == "call_started":
        timestamp_candidates.extend(
            [
                raw.get("event_timestamp"),
                call.get("start_timestamp"),
                raw.get("start_timestamp"),
            ],
        )
    elif event in {"call_ended", "call_analyzed"}:
        timestamp_candidates.extend(
            [
                raw.get("event_timestamp"),
                call.get("end_timestamp"),
                raw.get("end_timestamp"),
            ],
        )
    else:
        timestamp_candidates.extend(
            [
                raw.get("event_timestamp"),
                call.get("end_timestamp"),
                call.get("start_timestamp"),
                raw.get("end_timestamp"),
                raw.get("start_timestamp"),
                raw.get("occurred_at"),
            ],
        )

    for candidate in timestamp_candidates:
        parsed = _parse_timestamp(candidate)
        if parsed is not None:
            return parsed, None

    return clock.now(), "backend_clock_fallback"


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 1_000_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=UTC)

    if isinstance(value, str) and value.strip():
        normalized = value.strip()
        if normalized.isdigit():
            return _parse_timestamp(int(normalized))

        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None

        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    return None


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _coerce_lifecycle_metadata_field(field_name: str, value: object) -> object | None:
    if field_name in _LIFECYCLE_BLOCKED_METADATA_FIELDS:
        return None

    if field_name in _LIFECYCLE_STRING_METADATA_FIELDS:
        return _optional_string(value)

    if field_name in {"duration_ms", "duration_seconds"}:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        return None

    return value


def _extract_call_id(call: Any) -> str | None:
    if not isinstance(call, dict):
        return None

    call_id = call.get("call_id")
    if isinstance(call_id, str) and call_id.strip():
        return call_id.strip()

    return None
