from __future__ import annotations

from pathlib import Path

_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "operations"
    / "retell-master-prompt-v4.md"
)


def _paste_ready_block() -> str:
    content = _PROMPT_PATH.read_text(encoding="utf-8")
    marker = "## PASTE-READY RETELL MASTER PROMPT"
    start = content.index(marker)
    block_start = content.index("```", start) + 3
    block_end = content.index("```", block_start)
    return content[block_start:block_end].strip()


def test_master_prompt_v4_supports_booking_lookup_and_cancellation() -> None:
    prompt = _paste_ready_block().lower()

    assert "new appointment scheduling" in prompt or "book new appointments" in prompt
    assert "list_patient_appointments" in prompt
    assert "cancel_appointment" in prompt
    assert "appointment cancellation" in prompt


def test_master_prompt_v4_requires_cancellation_confirmation_before_tool_call() -> None:
    prompt = _paste_ready_block().lower()

    assert "never call cancel_appointment in the same assistant turn" in prompt
    assert "never say the appointment is cancelled until cancel_appointment" in prompt
    assert "confirmation_text" in prompt
    assert "patient_resolution_id" in prompt
    assert "appointment_id" in prompt
    assert "explicit_confirmation" in prompt


def test_master_prompt_v4_keeps_rescheduling_disabled() -> None:
    prompt = _paste_ready_block().lower()

    assert "rescheduling execution is not enabled" in prompt
    assert "do not call reschedule_appointment" in prompt
    assert "do not say the appointment has been rescheduled" in prompt


def test_master_prompt_v4_uses_preferred_post_booking_wording() -> None:
    prompt = _paste_ready_block().lower()

    assert "is there anything else you need today?" in prompt
    assert 'do not end with "how can i help you?"' in prompt
