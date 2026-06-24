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
    assert "if unsure, continue the conversation" in prompt


def test_master_prompt_v3_requires_resolve_before_demo_create() -> None:
    prompt = _paste_ready_block().lower()

    assert "resolve_patient_identity" in prompt
    assert "allow_demo_patient_creation" in prompt
    assert "even if the caller says they are new" in prompt
    assert "possible existing profile" in prompt or "possible_match" in prompt


def test_master_prompt_v3_prohibits_invented_contact_details() -> None:
    prompt = _paste_ready_block().lower()

    assert "never invent email" in prompt
    assert "never invent" in prompt and "phone" in prompt
    assert "do not reveal stored contact details" in prompt
