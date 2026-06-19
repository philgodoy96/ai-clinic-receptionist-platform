# Scheduling Domain Model

## Context

The AI Clinic Receptionist Platform needs enough clinic scheduling data to support receptionist workflows without becoming a full clinic CRM.

The initial scheduling model supports:

- Specialty listing
- Doctor listing
- Availability lookup
- Patient lookup
- Appointment booking
- Appointment rescheduling
- Appointment cancellation

## Implemented Entities

### Specialty

Represents a medical specialty supported by the fictional clinic.

Examples:

- Dermatology
- Cardiology
- Primary Care

### Doctor

Represents a doctor who belongs to one specialty.

A doctor can have many availability slots and many appointments.

### Patient

Represents a lightweight patient record.

Patient identity uses safer identifiers:

- Full name
- Date of birth
- Phone number
- Email

The model intentionally avoids highly sensitive identifiers such as:

- SSN
- CPF
- Insurance member ID
- Diagnosis
- Clinical notes

### AvailabilitySlot

Represents a bookable time window for a doctor.

The first implementation stores availability slots in PostgreSQL.

Future Redis appointment holds will reserve a selected slot temporarily before final booking.

### Appointment

Represents a scheduled, rescheduled, cancelled, or completed clinic appointment.

An appointment belongs to:

- Patient
- Doctor
- Specialty

An appointment may reference an availability slot when it was booked from a known slot.

## Important Invariants

- A doctor belongs to a specialty.
- An availability slot belongs to a doctor.
- An appointment belongs to a patient, doctor, and specialty.
- An appointment end time must be greater than its start time.
- An availability slot end time must be greater than its start time.
- A doctor cannot have two availability slots with the same start time.
- A doctor cannot have two scheduled appointments with the same start time.
- Patient appointment data must not be exposed without sufficient identification in future service/API layers.

## Database Constraints

The schema includes:

- Foreign keys for entity relationships
- Check constraints for valid time ranges
- Unique constraint for doctor availability start time
- Partial unique index for scheduled appointment conflicts

The partial unique index prevents two active scheduled appointments for the same doctor at the same start time while still allowing historical cancelled or rescheduled records.

## Out of Scope for This Slice

This slice does not implement:

- Redis appointment holds
- Appointment booking service logic
- Retell tool endpoints
- Chat conversation flow
- Audit logs
- Escalation cases
- RabbitMQ email jobs
- Admin/demo APIs

Those concerns will be added in later slices.