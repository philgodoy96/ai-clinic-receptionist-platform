from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

TOOL_EXECUTION_STATUS_METADATA_KEY = "execution_status"
TOOL_EXECUTION_CLAIMED_AT_METADATA_KEY = "claimed_at"
TOOL_EXECUTION_OUTCOME_METADATA_KEY = "tool_call_outcome"
TOOL_EXECUTION_ERROR_CODE_METADATA_KEY = "execution_error_code"
TOOL_EXECUTION_RECOVERY_METADATA_KEY = "execution_recovery"

# Provider-visible retry signal when another worker owns the same tool-execution identity.
TOOL_EXECUTION_IN_PROGRESS_ERROR_CODE = "tool_execution_in_progress"

# Deterministic recovery signal when a stale in_progress claim must not be blindly re-executed.
# Lease expiry proves loss of ownership, not that the prior side effect did not happen.
TOOL_EXECUTION_AMBIGUOUS_RECOVERY_ERROR_CODE = "tool_execution_ambiguous_recovery"

# Marker persisted on ambiguous stale claims for operator reconciliation.
AMBIGUOUS_DUAL_WRITE_RECOVERY = "ambiguous_dual_write"

# Lease used only to decide that the previous worker is no longer the active owner.
# Automatic re-execution after lease expiry requires an explicit safe-retry policy.
DEFAULT_TOOL_EXECUTION_CLAIM_LEASE = timedelta(seconds=120)


class VoiceToolExecutionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolExecutionStaleReclaimPolicy(StrEnum):
    """Whether a stale in_progress claim may automatically re-execute the mutation."""

    # Domain/provider boundary is independently safe to retry (A/B/C classification).
    ALLOW_SAFE_RETRY = "allow_safe_retry"
    # Crash-after-side-effect-before-outcome is ambiguous; do not re-execute automatically (D).
    REQUIRE_MANUAL_RECOVERY = "require_manual_recovery"


@dataclass(frozen=True, slots=True)
class ToolExecutionClaimResult:
    owned: bool
    status: VoiceToolExecutionStatus
    outcome: dict[str, Any] | None = None
    # True when a stale in_progress claim was left untouched because retry is not proven safe.
    recovery_required: bool = False


def build_in_progress_execution_metadata(*, claimed_at: datetime | None = None) -> dict[str, Any]:
    claimed = claimed_at or datetime.now(UTC)
    return {
        TOOL_EXECUTION_STATUS_METADATA_KEY: VoiceToolExecutionStatus.IN_PROGRESS.value,
        TOOL_EXECUTION_CLAIMED_AT_METADATA_KEY: claimed.isoformat(),
    }


def build_succeeded_execution_metadata(
    outcome: dict[str, Any],
    *,
    claimed_at: datetime | None = None,
) -> dict[str, Any]:
    metadata = build_in_progress_execution_metadata(claimed_at=claimed_at)
    metadata[TOOL_EXECUTION_STATUS_METADATA_KEY] = VoiceToolExecutionStatus.SUCCEEDED.value
    metadata[TOOL_EXECUTION_OUTCOME_METADATA_KEY] = outcome
    return metadata


def build_failed_execution_metadata(
    *,
    error_code: str | None = None,
    claimed_at: datetime | None = None,
) -> dict[str, Any]:
    metadata = build_in_progress_execution_metadata(claimed_at=claimed_at)
    metadata[TOOL_EXECUTION_STATUS_METADATA_KEY] = VoiceToolExecutionStatus.FAILED.value
    if error_code is not None:
        metadata[TOOL_EXECUTION_ERROR_CODE_METADATA_KEY] = error_code
    return metadata


def read_tool_execution_status(metadata: dict[str, Any] | None) -> VoiceToolExecutionStatus | None:
    if not isinstance(metadata, dict):
        return None

    raw_status = metadata.get(TOOL_EXECUTION_STATUS_METADATA_KEY)
    if isinstance(raw_status, str):
        try:
            return VoiceToolExecutionStatus(raw_status)
        except ValueError:
            return None

    # Historical outcome rows recorded only the serialized response.
    outcome = metadata.get(TOOL_EXECUTION_OUTCOME_METADATA_KEY)
    if isinstance(outcome, dict):
        return VoiceToolExecutionStatus.SUCCEEDED

    return None


def read_tool_execution_outcome(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None

    outcome = metadata.get(TOOL_EXECUTION_OUTCOME_METADATA_KEY)
    if isinstance(outcome, dict):
        return outcome

    return None


def read_tool_execution_claimed_at(metadata: dict[str, Any] | None) -> datetime | None:
    if not isinstance(metadata, dict):
        return None

    raw = metadata.get(TOOL_EXECUTION_CLAIMED_AT_METADATA_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_stale_in_progress_claim(
    metadata: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    lease: timedelta = DEFAULT_TOOL_EXECUTION_CLAIM_LEASE,
) -> bool:
    status = read_tool_execution_status(metadata)
    if status is not VoiceToolExecutionStatus.IN_PROGRESS:
        return False

    claimed_at = read_tool_execution_claimed_at(metadata)
    if claimed_at is None:
        return False

    current = now or datetime.now(UTC)
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=UTC)

    return current - claimed_at >= lease


def mark_ambiguous_dual_write_recovery(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Preserve claim correlation while documenting ambiguous dual-write recovery state."""
    base = dict(metadata) if isinstance(metadata, dict) else {}
    base[TOOL_EXECUTION_RECOVERY_METADATA_KEY] = AMBIGUOUS_DUAL_WRITE_RECOVERY
    return base


def read_tool_execution_recovery(metadata: dict[str, Any] | None) -> str | None:
    if not isinstance(metadata, dict):
        return None

    raw = metadata.get(TOOL_EXECUTION_RECOVERY_METADATA_KEY)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    return None
