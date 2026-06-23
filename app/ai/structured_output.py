from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError


class StructuredOutputParseError(ValueError):
    def __init__(self, message: str, *, repair_attempted: bool = False) -> None:
        super().__init__(message)
        self.repair_attempted = repair_attempted


class StructuredOutputValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class JsonObjectParseResult:
    payload: dict[str, object]
    used_repair: bool = False


@dataclass(frozen=True, slots=True)
class StructuredOutputParseOutcome[StructuredOutputT: BaseModel]:
    value: StructuredOutputT
    used_repair: bool


def parse_json_object(raw_output: str) -> JsonObjectParseResult:
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        repaired_output = extract_json_object(raw_output)
        if repaired_output is None:
            raise StructuredOutputParseError("LLM output was not valid JSON") from exc

        try:
            payload = json.loads(repaired_output)
        except json.JSONDecodeError as repair_exc:
            raise StructuredOutputParseError(
                "LLM output repair failed",
                repair_attempted=True,
            ) from repair_exc

        if not isinstance(payload, dict):
            raise StructuredOutputParseError(
                "LLM output JSON root must be an object",
            ) from None

        return JsonObjectParseResult(payload=payload, used_repair=True)

    if not isinstance(payload, dict):
        raise StructuredOutputParseError("LLM output JSON root must be an object")

    return JsonObjectParseResult(payload=payload, used_repair=False)


def extract_json_object(raw_output: str) -> str | None:
    start = raw_output.find("{")
    end = raw_output.rfind("}")

    if start == -1 or end == -1 or end <= start:
        return None

    return raw_output[start : end + 1]


def parse_structured_output[StructuredOutputT: BaseModel](
    *,
    raw_output: str,
    model_type: type[StructuredOutputT],
) -> StructuredOutputT:
    return parse_structured_output_with_repair_flag(
        raw_output=raw_output,
        model_type=model_type,
    ).value


def parse_structured_output_with_repair_flag[StructuredOutputT: BaseModel](
    *,
    raw_output: str,
    model_type: type[StructuredOutputT],
) -> StructuredOutputParseOutcome[StructuredOutputT]:
    parse_result = parse_json_object(raw_output)

    try:
        value = model_type.model_validate(parse_result.payload)
    except ValidationError as exc:
        raise StructuredOutputValidationError(
            "LLM output failed schema validation",
        ) from exc

    return StructuredOutputParseOutcome(
        value=value,
        used_repair=parse_result.used_repair,
    )
