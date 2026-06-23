from enum import StrEnum


class ReceptionistResponseType(StrEnum):
    INFORMATIONAL = "informational"
    SCHEDULING = "scheduling"
    CONFIRMATION = "confirmation"
    ESCALATION = "escalation"
    FALLBACK = "fallback"
    CRITICAL = "critical"


class ReceptionistResponseSafetyLevel(StrEnum):
    STANDARD = "standard"
    ELEVATED = "elevated"
    CRITICAL = "critical"
