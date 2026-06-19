# Scheduling Repository Boundaries

## Context

The scheduling domain needs database access for patients, doctors, specialties, availability slots, and appointments.

Application services should not spread SQLAlchemy query logic across the codebase.

Repositories provide a boundary between business/application logic and persistence details.

## Decision

Scheduling data access is exposed through repository interfaces.

SQLAlchemy-specific query logic lives in SQLAlchemy repository adapters.

Current package structure:

    app/repositories/scheduling.py
    app/repositories/sqlalchemy/scheduling.py

## Responsibilities

Repository interfaces define what the application layer needs.

SQLAlchemy repositories define how data is queried or persisted.

Application services should depend on repository behavior rather than constructing SQLAlchemy queries directly.

## Transaction Boundary

Repositories do not commit transactions.

Repositories may call flush when adding new records so generated identifiers become available.

The application service or request boundary should decide when to commit or rollback.

This is important because scheduling workflows often require multiple operations to succeed or fail together.

Example future booking flow:

1. Validate patient
2. Validate doctor
3. Validate availability slot
4. Validate Redis hold
5. Create appointment
6. Update slot state if needed
7. Write audit log
8. Enqueue email job
9. Commit durable transaction

A repository-level commit would make this flow harder to reason about and harder to rollback safely.

## Current Repository Capabilities

Implemented repositories support:

- Listing active specialties
- Getting specialty by name or ID
- Listing active doctors
- Getting doctor by ID
- Getting patients by ID, email, phone number, or sufficient identity
- Adding patients
- Listing available appointment slots
- Getting availability slot by ID
- Getting appointments by ID
- Listing upcoming scheduled appointments for a patient
- Finding scheduled appointment conflicts
- Adding appointments

## Data Privacy Boundary

Patient lookup intentionally supports sufficient identity checks.

Future services and APIs must ensure patient appointment data is not returned unless enough identifiers are provided.

The repository helps with this, but the application service remains responsible for enforcing policy.

## Testing Strategy

Repository tests use an isolated database session.

The tests currently use SQLite in memory to validate repository behavior without requiring Postgres for every test run.

PostgreSQL-specific constraints and migrations are still validated separately through Alembic and Docker Compose checks.

## Future Work

Future slices should add:

- Scheduling application services
- Unit tests for scheduling invariants
- Redis appointment hold repository/service
- Audit log repositories
- Tool call repositories
- Retell tool endpoint adapters
- Integration tests against PostgreSQL when service behavior becomes more complex