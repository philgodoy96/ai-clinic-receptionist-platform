from __future__ import annotations

from app.db.session import SessionLocal
from app.services.demo_availability_generation import generate_demo_availability


def main() -> None:
    with SessionLocal() as session:
        result = generate_demo_availability(session)
        session.commit()

    print(
        "Demo availability generation complete:",
        f"slots_created={result.slots_created}",
        f"slots_existing={result.slots_existing}",
        f"horizon_days={result.horizon_days}",
        f"start_date={result.start_date.isoformat()}",
        f"end_date={result.end_date.isoformat()}",
    )


if __name__ == "__main__":
    main()
