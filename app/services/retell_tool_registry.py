from __future__ import annotations

from app.domain.retell_tools import RetellSupportedToolName
from app.domain.voice_tool_execution import ToolExecutionStaleReclaimPolicy

RETELL_TOOL_ALLOWLIST: frozenset[RetellSupportedToolName] = frozenset(RetellSupportedToolName)

SIDE_EFFECTING_RETELL_TOOLS: frozenset[RetellSupportedToolName] = frozenset(
    {
        RetellSupportedToolName.HOLD_APPOINTMENT_SLOT,
        RetellSupportedToolName.RELEASE_APPOINTMENT_HOLD,
        RetellSupportedToolName.BOOK_APPOINTMENT,
        RetellSupportedToolName.CANCEL_APPOINTMENT,
        RetellSupportedToolName.RESCHEDULE_APPOINTMENT,
        RetellSupportedToolName.RESOLVE_PATIENT_IDENTITY,
        RetellSupportedToolName.CONFIRM_PATIENT_IDENTITY,
    },
)

# Stale adapter claims may automatically re-execute only when the domain boundary is
# independently safe to retry (classification B/C). Tools omitted here are D and require
# explicit recovery instead of blind lease-expiry reclaim.
SAFE_STALE_RECLAIM_RETELL_TOOLS: frozenset[RetellSupportedToolName] = frozenset(
    {
        RetellSupportedToolName.RELEASE_APPOINTMENT_HOLD,
        RetellSupportedToolName.BOOK_APPOINTMENT,
        RetellSupportedToolName.CANCEL_APPOINTMENT,
        RetellSupportedToolName.RESCHEDULE_APPOINTMENT,
        RetellSupportedToolName.CONFIRM_PATIENT_IDENTITY,
    },
)


def is_allowlisted_retell_tool(tool_name: RetellSupportedToolName) -> bool:
    return tool_name in RETELL_TOOL_ALLOWLIST


def is_side_effecting_retell_tool(tool_name: RetellSupportedToolName) -> bool:
    return tool_name in SIDE_EFFECTING_RETELL_TOOLS


def stale_reclaim_policy_for_retell_tool(
    tool_name: RetellSupportedToolName,
) -> ToolExecutionStaleReclaimPolicy:
    if tool_name in SAFE_STALE_RECLAIM_RETELL_TOOLS:
        return ToolExecutionStaleReclaimPolicy.ALLOW_SAFE_RETRY
    return ToolExecutionStaleReclaimPolicy.REQUIRE_MANUAL_RECOVERY
