# Demo Availability Generation

## Purpose

The demo availability generator maintains **future bookable slots** for local development and public demo environments. It is **not** a production schedule engine, admin schedule manager, or background worker.

Use it when:

- Preparing a local or hosted demo and availability should cover the full booking window.
- Refreshing demo slots after the booking horizon has rolled forward.
- Confirming that idempotent slot generation is working before a voice or API smoke test.

The generator complements `scripts/seed_demo_data.py`. Seed creates the fictional clinic roster (specialties, doctors, patients) and a short rolling window of slots. The generator extends availability through the configured booking horizon without duplicating existing rows.

## Single source of truth

`SCHEDULING_BOOKING_HORIZON_DAYS` (default **14**) controls:

- How far `check_availability` may show bookable times.
- How far the demo generator creates future slots.

Do **not** introduce a separate generation-window setting (for example `DEMO_GENERATION_DAYS`). See [Configuration — Scheduling Availability Policy](../configuration.md#scheduling-availability-policy).

## Command

```powershell
python -m app.scripts.generate_demo_availability
```

## Preconditions

1. Database migrations applied (`python -m alembic upgrade head`).
2. Base demo data seeded (`python -m scripts.seed_demo_data`) so doctors exist.

The generator reads active doctors from the database and creates slots for each demo doctor using the existing demo slot grid (10:00, 11:00, 14:00, and 15:00 clinic-local, 30-minute duration) on configured business days within business hours.

## Recommended local sequence

```powershell
docker compose up -d postgres redis rabbitmq
python -m alembic upgrade head
python -m scripts.seed_demo_data
python -m app.scripts.generate_demo_availability
```

For hosted public demo deploys, run migrations and seed from a release job or one-off task, then run the generator before smoke tests. See [Public Demo Deployment](public-demo-deployment.md).

## Behavior

| Property | Detail |
|----------|--------|
| Time context | Uses `CLINIC_TIMEZONE` and current clinic date/time |
| Date range | Current clinic date through `clinic_now + SCHEDULING_BOOKING_HORIZON_DAYS` |
| Business days | Respects `CLINIC_BUSINESS_DAYS` (default Monday–Friday) |
| Slot times | Existing demo convention: 10:00, 11:00, 14:00, 15:00 clinic-local |
| Idempotency | Explicit lookup on `(doctor_id, start_time)` before insert; safe to run repeatedly |
| Booked slots | Never overwritten |
| Appointments | Never modified |
| Past slots | Not deleted; availability queries exclude them via scheduling policy |
| Background worker | Not used in this slice — run the CLI manually before demos |

## CLI summary

After each run, the command prints:

```text
Demo availability generation complete: slots_created=<n> slots_existing=<n> horizon_days=<n> start_date=<YYYY-MM-DD> end_date=<YYYY-MM-DD>
```

| Field | Meaning |
|-------|---------|
| `slots_created` | New `available` rows inserted this run |
| `slots_existing` | Rows already present at the same doctor/start time (including booked slots counted as existing, not modified) |
| `horizon_days` | Value of `SCHEDULING_BOOKING_HORIZON_DAYS` used for the run |
| `start_date` | Clinic-local start date for generation |
| `end_date` | Clinic-local end date corresponding to the booking horizon |

### Expected idempotent re-run

```powershell
python -m app.scripts.generate_demo_availability
python -m app.scripts.generate_demo_availability
```

First run: `slots_created >= 0`, `slots_existing >= 0`.

Second run: `slots_created = 0`, `slots_existing > 0` (when the first run created slots).

## What this is not

- **Not a dynamic doctor schedule engine** — all demo doctors share the same slot template.
- **Not admin schedule management** — no UI or per-doctor hours.
- **Not a background job** — no cron or worker process; operators run the CLI explicitly.
- **Not production rolling schedules** — production evolution would add doctor-specific rules, durable schedule tables, and automated generation.

## Related documentation

- [Local Development](local-development.md)
- [Scheduling Application Services](../architecture/scheduling-services.md)
- [Retell Manual Smoke Tests](retell-manual-smoke-tests.md) — availability and horizon smoke scenarios
- [Retell Tool Descriptions](retell-tool-descriptions.md) — `check_availability` response metadata
