from __future__ import annotations

from pathlib import Path

_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "operations"
    / "retell-master-prompt-v3.md"
)


def _paste_ready_block() -> str:
    content = _PROMPT_PATH.read_text(encoding="utf-8")
    marker = "## PASTE-READY RETELL MASTER PROMPT"
    start = content.index(marker)
    block_start = content.index("```", start) + 3
    block_end = content.index("```", block_start)
    return content[block_start:block_end].strip()


def test_master_prompt_v3_contains_strong_end_call_restrictions() -> None:
    prompt = _paste_ready_block().lower()

    assert "never call end_call after asking a question" in prompt
    assert "never call end_call while an appointment time is being held" in prompt
    assert "never call end_call before patient identity is resolved" in prompt
    assert "goodbye" in prompt
    assert "if unsure whether the caller is finished, continue the conversation" in prompt


def test_master_prompt_v3_requires_resolve_before_demo_create() -> None:
    prompt = _paste_ready_block().lower()

    assert "resolve_patient_identity" in prompt
    assert "allow_demo_patient_creation" in prompt
    assert "even if caller_claims_existing_patient is false" in prompt
    assert "possible existing profile" in prompt


def test_master_prompt_v3_waits_after_confirmation_questions() -> None:
    prompt = _paste_ready_block().lower()

    assert "never call a tool in the same turn after asking" in prompt
    assert "is that correct?" in prompt
    assert "only after the caller confirms the email" in prompt
    assert "sample_email_required" not in prompt
    assert "do not require" in prompt and ".test" in prompt


def test_master_prompt_v3_uses_preferred_post_booking_wording() -> None:
    prompt = _paste_ready_block().lower()

    assert "is there anything else you need today?" in prompt
    assert 'do not end with "how can i help you?"' in prompt


def test_master_prompt_v3_limits_scope_to_booking_flow() -> None:
    prompt = _paste_ready_block().lower()

    assert "book new appointments" in prompt or "new appointment scheduling" in prompt
    assert "list_patient_appointments" in prompt
    assert (
        "cancellation and rescheduling execution are not enabled in this voice flow yet."
        in prompt
    )
    assert "do not call cancel_appointment" in prompt
    assert "do not call reschedule_appointment" in prompt
    assert "do not say the appointment has been cancelled" in prompt
    assert "do not say the appointment has been rescheduled" in prompt


def test_master_prompt_v3_prohibits_invented_contact_details() -> None:
    prompt = _paste_ready_block().lower()

    assert "never invent an email address" in prompt
    assert "never invent a phone number" in prompt
    assert "never reveal stored email or phone" in prompt
