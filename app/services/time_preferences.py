from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class TimePreferenceStatus(StrEnum):
    PARSED = "parsed"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class TimeWindow:
    label: str
    start_time: str
    end_time: str

    def to_metadata(self) -> dict[str, str]:
        return {
            "label": self.label,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }


@dataclass(frozen=True, slots=True)
class TimePreferenceResult:
    status: TimePreferenceStatus
    window: TimeWindow | None = None
    source_text: str | None = None
    reason: str | None = None

    def to_metadata(self) -> dict[str, object | None]:
        return {
            "status": self.status.value,
            "label": self.window.label if self.window else None,
            "start_time": self.window.start_time if self.window else None,
            "end_time": self.window.end_time if self.window else None,
            "source_text": self.source_text,
            "reason": self.reason,
        }


class TimePreferenceParser:
    _windows = {
        "morning": TimeWindow(
            label="morning",
            start_time="08:00",
            end_time="12:00",
        ),
        "afternoon": TimeWindow(
            label="afternoon",
            start_time="12:00",
            end_time="17:00",
        ),
        "evening": TimeWindow(
            label="evening",
            start_time="17:00",
            end_time="20:00",
        ),
    }

    _unsupported_markers = [
        "early morning",
        "late morning",
        "early afternoon",
        "late afternoon",
        "late evening",
        "after lunch",
        "before lunch",
        "before noon",
        "after noon",
        "around",
        "any time",
        "anytime",
        "business hours",
        "lunch time",
    ]

    def parse(self, text: str) -> TimePreferenceResult:
        normalized_text = text.strip().lower()

        if not normalized_text:
            return TimePreferenceResult(
                status=TimePreferenceStatus.NOT_FOUND,
                reason="empty_text",
            )

        if self._contains_unsupported_expression(normalized_text):
            return TimePreferenceResult(
                status=TimePreferenceStatus.UNSUPPORTED,
                reason="unsupported_time_preference",
            )

        matches = self._find_supported_matches(normalized_text)

        if len(matches) > 1:
            return TimePreferenceResult(
                status=TimePreferenceStatus.AMBIGUOUS,
                source_text=", ".join(matches),
                reason="multiple_time_preferences",
            )

        if not matches:
            return TimePreferenceResult(
                status=TimePreferenceStatus.NOT_FOUND,
                reason="no_time_preference_found",
            )

        label = matches[0]
        return TimePreferenceResult(
            status=TimePreferenceStatus.PARSED,
            window=self._windows[label],
            source_text=label,
        )

    def _find_supported_matches(self, text: str) -> list[str]:
        matches: list[str] = []

        for label in self._windows:
            if re.search(rf"\b{label}\b", text):
                matches.append(label)

        return matches

    def _contains_unsupported_expression(self, text: str) -> bool:
        return any(marker in text for marker in self._unsupported_markers)


def is_time_in_window(*, time_value: str, window: TimeWindow) -> bool:
    return window.start_time <= time_value < window.end_time