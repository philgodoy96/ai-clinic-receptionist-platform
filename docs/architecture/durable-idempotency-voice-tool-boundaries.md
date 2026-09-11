# Durable Idempotency at External Voice Tool Boundaries

## Context

Retell (and similar voice providers) deliver tool callbacks with at-least-once semantics.
Sequential duplicate delivery was already mitigated by storing successful tool outcomes on
`voice_call_events` and by domain attempt rows (`voice_booking_attempts`,
`appointment_cancellation_attempts`, `appointment_reschedule_attempts`).

Concurrent duplicate delivery exposed a race: two workers could both observe “no completed
outcome,” both execute the protected domain mutation, and only later compete on uniqueness.

## Decision

Protect side-effecting Retell tools with a durable pre-side-effect execution claim:

1. Derive a deterministic execution identity:
   `provider:provider_call_id:tool:tool_name:tool_call_id`
2. Atomically claim ownership by inserting (or reclaiming) a `voice_call_events` row under the
   unique `idempotency_key`, with metadata status `in_progress`.
3. Only the claim owner executes the domain side effect.
4. On success, update the same row to `succeeded` with the reusable serialized outcome.
5. On failure/rejection after claim, mark `failed` so a later retry may reclaim ownership.
6. Concurrent non-owners receive a deterministic retryable `tool_execution_in_progress`
   response and must not execute the side effect.

**Primary guarantee:** Concurrent duplicate callbacks sharing one durable execution identity
cannot independently execute the protected mutation. This is **not** a claim of exactly-once
execution under arbitrary crash timing.

Domain attempt tables remain authoritative for booking/cancel/reschedule business records:

- `VoiceBookingAttempt.status=pending` is exclusive ownership; duplicates raise
  `booking_in_progress` instead of calling `book_appointment`.
- Reschedule `PENDING` attempts raise `reschedule_in_progress`.
- Cancellation inserts the attempt row before mutation; concurrent losers defer.

### Stale-claim reclaim policy (not blind 120s retry)

A 120-second lease proves only that the previous worker is no longer considered the active
owner. It does **not** prove the previous side effect did not happen.

Automatic reclaim of a stale `in_progress` claim is allowed **only** when the tool's
underlying mutation is classified as safely retryable (A/B/C below). Tools classified **D**
do not re-execute after ambiguous stale claims; they return
`tool_execution_ambiguous_recovery` and stamp `execution_recovery=ambiguous_dual_write` on
the durable claim metadata for reconciliation.

| Classification | Meaning |
|---|---|
| A | Atomic with the durable claim/outcome transaction |
| B | Independently idempotent at the domain/database boundary |
| C | Externally idempotent through a stable provider/resource key |
| D | Not safely repeatable / ambiguous after crash-after-side-effect |

| Tool | Class | Stale reclaim |
|---|---|---|
| `release_appointment_hold` | B | Allow safe retry (release is idempotent) |
| `book_appointment` | B | Allow; domain attempt PENDING/SUCCEEDED gate mutation |
| `cancel_appointment` | B | Allow; unique attempt + cancel-if-already-cancelled |
| `reschedule_appointment` | B | Allow; domain PENDING/SUCCEEDED/FAILED reclaim |
| `confirm_patient_identity` | B | Allow; confirm/reject on existing resolution token |
| `hold_appointment_slot` | D | Manual recovery; Redis hold uses a fresh `hold_id` |
| `resolve_patient_identity` | D | Manual recovery; new resolution tokens / demo patients |

Explicit `failed` claims may still be reclaimed (failure path completed). That is distinct from
lease-expiry reclaim of `in_progress`.

## Alternatives considered

- In-memory mutex / process-local cache — rejected; not durable across workers.
- Redis distributed lock — rejected; unnecessary new infrastructure for this boundary.
- Rely only on downstream appointment uniqueness — rejected; fails closed noisily and does
  not provide reusable stored outcomes for in-flight duplicates.
- Full outbox/saga — deferred; local transactional mutations do not require it for this PR.
- Blind stale reclaim for all tools after 120s — rejected; lease ≠ side-effect absence.

## Consequences

- Provider delivery remains at-least-once; the application does not claim globally exactly-once
  messaging.
- Supported tool boundaries get effectively-once concurrent mutation control for a given
  execution identity while an owner is active, plus reusable outcomes after success.
- Crash after claim and before completion leaves `in_progress`. After lease expiry:
  - safe-retry tools may reclaim and re-run (domain gates still apply);
  - ambiguous tools return `tool_execution_ambiguous_recovery` without re-execution.
- External dual-write boundaries (Redis holds, identity resolution store vs Postgres claim)
  can still diverge under crash; operators reconcile using the durable claim identity.

## Failure semantics

| Crash point | Durable state | Retry behavior |
|---|---|---|
| After authenticity/validation, before claim | No claim | Safe to retry; new owner can claim |
| After claim, before side effect | `in_progress` | Duplicates get `tool_execution_in_progress`; stale reclaim follows per-tool policy |
| During local DB mutation before commit | `in_progress` or domain `failed` | No succeeded outcome persisted; failed/reclaim paths apply |
| After side effect, before outcome (D tools) | stale `in_progress` + recovery marker | Ambiguous dual-write; no automatic re-execution |
| After outcome persistence | `succeeded` + outcome | Duplicates reuse stored outcome |

## Trust-boundary separation

- HMAC/signature → authenticity
- Timestamp tolerance → bounded freshness (unchanged by this decision)
- Durable execution identity + claim → duplicate/replay effect control
- Domain confirmation/business rules → authorization to perform the action

## Revisit triggers

- Need for stronger cross-store atomicity with Redis holds or email providers
- Deterministic hold/resolution keys derived from tool-call identity (would move D → C)
- Multi-region active-active tool execution
- Provider contracts that require synchronous wait-for-in-progress instead of retryable failure
