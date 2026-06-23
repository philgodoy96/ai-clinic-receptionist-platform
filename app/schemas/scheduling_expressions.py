from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain.scheduling.expressions import (
    DateExpression,
    DateExpressionKind,
    TimeWindowExpression,
    TimeWindowExpressionKind,
    Weekday,
)


class DateExpressionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DateExpressionKind
    weekday: Weekday | None = None
    days_offset: int | None = Field(default=None, ge=0)
    exact_date: date | None = None

    def to_domain(self) -> DateExpression:
        return DateExpression(
            kind=self.kind,
            weekday=self.weekday,
            days_offset=self.days_offset,
            exact_date=self.exact_date,
        )


class TimeWindowExpressionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TimeWindowExpressionKind
    exact_time: str | None = Field(default=None, max_length=5)

    def to_domain(self) -> TimeWindowExpression:
        return TimeWindowExpression(
            kind=self.kind,
            exact_time=self.exact_time,
        )
