"""create scheduling tables

Revision ID: 20260619_0001
Revises:
Create Date: 2026-06-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260619_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


availability_slot_status_enum = postgresql.ENUM(
    "available",
    "held",
    "booked",
    "blocked",
    name="availability_slot_status",
    create_type=False,
)

appointment_status_enum = postgresql.ENUM(
    "scheduled",
    "rescheduled",
    "cancelled",
    "completed",
    name="appointment_status",
    create_type=False,
)


def upgrade() -> None:
    availability_slot_status_enum.create(op.get_bind(), checkfirst=True)
    appointment_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "specialties",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_specialties_name", "specialties", ["name"], unique=False)

    op.create_table(
        "doctors",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("specialty_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone_number", sa.String(length=40), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["specialty_id"], ["specialties.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_doctors_full_name", "doctors", ["full_name"], unique=False)
    op.create_index("ix_doctors_specialty_id", "doctors", ["specialty_id"], unique=False)

    op.create_table(
        "patients",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("phone_number", sa.String(length=40), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("phone_number"),
    )
    op.create_index("ix_patients_email", "patients", ["email"], unique=False)
    op.create_index("ix_patients_full_name", "patients", ["full_name"], unique=False)
    op.create_index("ix_patients_phone_number", "patients", ["phone_number"], unique=False)

    op.create_table(
        "availability_slots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", availability_slot_status_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("end_time > start_time", name="ck_availability_slots_end_after_start"),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doctor_id", "start_time", name="uq_availability_slots_doctor_start_time"),
    )
    op.create_index(
        "ix_availability_slots_doctor_start_time",
        "availability_slots",
        ["doctor_id", "start_time"],
        unique=False,
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.Column("specialty_id", sa.UUID(), nullable=False),
        sa.Column("availability_slot_id", sa.UUID(), nullable=True),
        sa.Column("rescheduled_from_appointment_id", sa.UUID(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", appointment_status_enum, nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=500), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("end_time > start_time", name="ck_appointments_end_after_start"),
        sa.ForeignKeyConstraint(["availability_slot_id"], ["availability_slots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["rescheduled_from_appointment_id"],
            ["appointments.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["specialty_id"], ["specialties.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appointments_availability_slot_id", "appointments", ["availability_slot_id"])
    op.create_index("ix_appointments_doctor_id", "appointments", ["doctor_id"])
    op.create_index("ix_appointments_doctor_start_time", "appointments", ["doctor_id", "start_time"])
    op.create_index("ix_appointments_patient_id", "appointments", ["patient_id"])
    op.create_index("ix_appointments_patient_start_time", "appointments", ["patient_id", "start_time"])
    op.create_index(
        "ix_appointments_rescheduled_from_appointment_id",
        "appointments",
        ["rescheduled_from_appointment_id"],
    )
    op.create_index("ix_appointments_specialty_id", "appointments", ["specialty_id"])
    op.create_index(
        "uq_scheduled_appointment_doctor_start_time",
        "appointments",
        ["doctor_id", "start_time"],
        unique=True,
        postgresql_where=sa.text("status = 'scheduled'"),
    )


def downgrade() -> None:
    op.drop_index("uq_scheduled_appointment_doctor_start_time", table_name="appointments")
    op.drop_index("ix_appointments_specialty_id", table_name="appointments")
    op.drop_index("ix_appointments_rescheduled_from_appointment_id", table_name="appointments")
    op.drop_index("ix_appointments_patient_start_time", table_name="appointments")
    op.drop_index("ix_appointments_patient_id", table_name="appointments")
    op.drop_index("ix_appointments_doctor_start_time", table_name="appointments")
    op.drop_index("ix_appointments_doctor_id", table_name="appointments")
    op.drop_index("ix_appointments_availability_slot_id", table_name="appointments")
    op.drop_table("appointments")

    op.drop_index("ix_availability_slots_doctor_start_time", table_name="availability_slots")
    op.drop_table("availability_slots")

    op.drop_index("ix_patients_phone_number", table_name="patients")
    op.drop_index("ix_patients_full_name", table_name="patients")
    op.drop_index("ix_patients_email", table_name="patients")
    op.drop_table("patients")

    op.drop_index("ix_doctors_specialty_id", table_name="doctors")
    op.drop_index("ix_doctors_full_name", table_name="doctors")
    op.drop_table("doctors")

    op.drop_index("ix_specialties_name", table_name="specialties")
    op.drop_table("specialties")

    appointment_status_enum.drop(op.get_bind(), checkfirst=True)
    availability_slot_status_enum.drop(op.get_bind(), checkfirst=True)