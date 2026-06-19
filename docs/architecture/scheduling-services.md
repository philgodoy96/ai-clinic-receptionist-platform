# Scheduling Application Services

## Context

Scheduling workflows require business validation before they are exposed through API routes, chat flows, or Retell tools.

The application service layer coordinates use cases and protects business invariants while keeping HTTP, Retell, and persistence details outside the core workflow logic.

## Decision

Scheduling use cases are implemented in `app/services/scheduling.py`.

The scheduling service depends on repository interfaces instead of SQLAlchemy queries directly.

This creates a clean boundary:

    API / Retell / Chat adapters
        -> application services
            -> repository interfaces
                -> SQLAlchemy repository adapters
                    -> database models

## Implemented Capabilities

The current scheduling service supports:

- Listing active specialties
- Listing active doctors
- Checking doctor availability
- Looking up patients with sufficient identity
- Listing upcoming patient appointments
- Finding scheduled appointment conflicts

## Patient Identity Rule

Patient lookup requires more than full name and date of birth.

The service requires at least one additional identifier:

- Phone number
- Email

This reduces the risk of exposing patient data when two patients share similar names or dates of birth.

Future API and Retell tool layers must preserve this rule.

## Availability Rule

Availability lookup requires:

- Existing doctor
- Active doctor
- Valid time window where `start_to > start_from`

The service rejects invalid windows before calling the availability repository.

## Transaction Boundary

The scheduling service does not commit transactions.

Future write workflows should keep transaction ownership at the application use case or request boundary.

This matters because future booking will coordinate several steps:

1. Patient validation
2. Doctor validation
3. Availability validation
4. Redis appointment hold validation
5. Appointment creation
6. Audit log creation
7. Email job scheduling

Durable database changes, such as appointment creation and audit log records, should be committed atomically.

External or operational steps, such as Redis hold validation and RabbitMQ email delivery, should not be treated as part of the same database transaction. They should be coordinated through clear ordering, idempotency, retries, and eventually an outbox-style pattern if stronger reliability is needed.

## Current Limitations

This slice does not implement:

- Appointment booking
- Appointment rescheduling
- Appointment cancellation
- Redis appointment holds
- Retell tool endpoints
- Chat conversation flow
- Audit logs
- RabbitMQ email confirmation jobs

## Testing Strategy

Service tests use fake repositories.

This keeps tests fast and focused on application behavior rather than persistence details.

Repository tests separately validate SQLAlchemy query behavior.