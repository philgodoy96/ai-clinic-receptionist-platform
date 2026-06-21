# Escalation Assignment Workflow

## Context

Human escalation records represent operational handoff requests.

This implementation phase adds assignment so a staff member can take ownership of an escalation.

## Design Principle

Assignment means operational ownership.

It does not mean live human chat exists.

It does not connect the user to a person in real time.

## Current Implementation

The demo implementation supports:

- assigning an escalation to a staff label
- unassigning an escalation
- listing assigned escalations
- listing unassigned escalations
- listing overdue escalations
- simple priority-based due dates

## Demo Staff Identity

The project does not include a `StaffUser` table yet.

`assigned_to` is a string for demo/internal usage.

In production, this should reference an authenticated staff user.

## SLA Policy

The demo policy is:

- urgent: due in 15 minutes
- high: due in 4 hours
- normal: due in 24 hours

These values are operational demo defaults, not clinical/legal SLA guarantees.

## State Transitions

Open or acknowledged escalations can be assigned.

Resolved or cancelled escalations cannot be assigned.

Assigning an open escalation moves it to acknowledged.

## Boundaries

Assignment does not:

- create appointments
- create holds
- send notifications in this implementation phase
- call an LLM
- create live human chat
- implement auth/RBAC

## Future Work

Future implementation phases may add:

- StaffUser table
- authentication and RBAC
- assignment audit events
- staff dashboard
- assignment notification job
- optimistic concurrency for assignment races
