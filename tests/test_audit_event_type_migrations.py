from __future__ import annotations

import re
from pathlib import Path

from app.domain.audit.enums import AuditEventType

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"
_INITIAL_AUDIT_EVENT_TYPE_PATTERN = re.compile(
    r'audit_event_type_enum\s*=\s*postgresql\.ENUM\(\s*((?:"[^"]+"\s*,?\s*)+)',
    re.MULTILINE,
)
_ADD_AUDIT_EVENT_TYPE_PATTERN = re.compile(
    r"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '([^']+)'",
)


def _postgres_audit_event_types_from_migrations() -> set[str]:
    application_values = {event_type.value for event_type in AuditEventType}
    postgres_values: set[str] = set()

    for migration_path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        migration_source = migration_path.read_text(encoding="utf-8")

        initial_match = _INITIAL_AUDIT_EVENT_TYPE_PATTERN.search(migration_source)
        if initial_match is not None:
            postgres_values.update(re.findall(r'"([^"]+)"', initial_match.group(1)))

        if "audit_event_type" not in migration_source:
            continue

        postgres_values.update(
            value
            for value in application_values
            if f"'{value}'" in migration_source or f'"{value}"' in migration_source
        )
        postgres_values.update(_ADD_AUDIT_EVENT_TYPE_PATTERN.findall(migration_source))

    return postgres_values


def test_audit_event_type_enum_values_are_defined_in_postgres_migrations() -> None:
    postgres_values = _postgres_audit_event_types_from_migrations()
    application_values = {event_type.value for event_type in AuditEventType}

    missing_in_postgres = sorted(application_values - postgres_values)
    assert not missing_in_postgres, (
        "AuditEventType values missing from audit_event_type migrations: "
        f"{missing_in_postgres}"
    )


def test_cancellation_audit_event_types_are_defined_in_postgres_migrations() -> None:
    postgres_values = _postgres_audit_event_types_from_migrations()

    assert (
        AuditEventType.APPOINTMENT_CANCELLATION_CONFIRMED.value in postgres_values
    )
    assert AuditEventType.APPOINTMENT_CANCELLATION_FAILED.value in postgres_values
