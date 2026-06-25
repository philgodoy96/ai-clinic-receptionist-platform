from __future__ import annotations

from pathlib import Path

_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "operations"
    / "retell-master-prompt-v5.md"
)
_V4_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "operations"
    / "retell-master-prompt-v4.md"
)


def _paste_ready_block(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    marker = "## PASTE-READY RETELL MASTER PROMPT"
    start = content.index(marker)
    block_start = content.index("```", start) + 3
    block_end = content.index("```", block_start)
    return content[block_start:block_end].strip()


def test_master_prompt_v5_is_active_and_supersedes_v4() -> None:
    header = _PROMPT_PATH.read_text(encoding="utf-8").lower()

    assert (
        "prompt version: `retell-receptionist-v5`" in header
        or "retell-receptionist-v5" in header
    )
    assert "status:** active" in header or "**status:** active" in header
    assert "supersedes" in header
    assert "retell-master-prompt-v4" in header


def test_master_prompt_v5_enables_rescheduling_execution() -> None:
    prompt = _paste_ready_block(_PROMPT_PATH).lower()

    assert "reschedule_appointment" in prompt
    assert "rescheduling" in prompt
    assert "never say the appointment has been rescheduled until reschedule_appointment" in prompt


def test_master_prompt_v5_requires_reschedule_confirmation_before_tool_call() -> None:
    prompt = _paste_ready_block(_PROMPT_PATH).lower()

    assert "never call reschedule_appointment" in prompt
    assert "explicit_confirmation" in prompt
    assert "confirmation_text" in prompt
    assert "patient_resolution_id" in prompt
    assert "appointment_id" in prompt
    assert "new_slot_id" in prompt
    assert "hold_id" in prompt


def test_master_prompt_v5_documents_backend_owned_hold_ttl() -> None:
    prompt = _paste_ready_block(_PROMPT_PATH).lower()
    header = _PROMPT_PATH.read_text(encoding="utf-8").lower()

    assert "backend owns hold duration" in prompt or "backend owns hold" in header
    assert "expires_in_seconds" in header or "300 seconds" in header
    assert "cannot choose or extend the ttl" in prompt or "cannot choose" in prompt


def test_master_prompt_v5_documents_patient_aware_hold_recovery_as_future_work() -> None:
    header = _PROMPT_PATH.read_text(encoding="utf-8").lower()

    assert "patient-aware hold recovery" in header
    assert "not implemented" in header or "future" in header


def test_master_prompt_v4_remains_cancellation_focused_without_rescheduling_execution() -> None:
    prompt = _paste_ready_block(_V4_PROMPT_PATH).lower()

    assert "rescheduling execution is not enabled" in prompt
    assert "do not call reschedule_appointment" in prompt
