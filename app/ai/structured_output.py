from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError


class StructuredOutputParseError(ValueError):
    def __init__(self, message: str, *, repair_attempted: bool = False) -> None:
        super().__init__(message)
        self.repair_attempted = repair_attempted


class StructuredOutputValidationError(ValueError):
    pass


def parse_json_object(raw_output: str) -> dict[str, object]:
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
        raise StructuredOutputParseError("LLM output JSON root must be an object")

    return payload


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
    payload = parse_json_object(raw_output)

    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise StructuredOutputValidationError(
            "LLM output failed schema validation",
        ) from exc