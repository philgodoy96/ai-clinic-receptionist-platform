from __future__ import annotations

from app.domain.retell_tools import RetellSupportedToolName

RETELL_TOOL_ALLOWLIST: frozenset[RetellSupportedToolName] = frozenset(RetellSupportedToolName)

SIDE_EFFECTING_RETELL_TOOLS: frozenset[RetellSupportedToolName] = frozenset(
    {
        RetellSupportedToolName.HOLD_APPOINTMENT_SLOT,
        RetellSupportedToolName.RELEASE_APPOINTMENT_HOLD,
        RetellSupportedToolName.BOOK_APPOINTMENT,
        RetellSupportedToolName.CANCEL_APPOINTMENT,
    },
)


def is_allowlisted_retell_tool(tool_name: RetellSupportedToolName) -> bool:
    return tool_name in RETELL_TOOL_ALLOWLIST


def is_side_effecting_retell_tool(tool_name: RetellSupportedToolName) -> bool:
    return tool_name in SIDE_EFFECTING_RETELL_TOOLS
