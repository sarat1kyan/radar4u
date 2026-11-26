"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-26 20:07:05.172577
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # schema baseline
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_log_user_id"), ["user_id"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("time_format", sa.String(length=3), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("home_place", sa.JSON(), nullable=True),
        sa.Column("favorites", sa.JSON(), nullable=False),
        sa.Column("quiet_start", sa.Time(), nullable=True),
        sa.Column("quiet_end", sa.Time(), nullable=True),
        sa.Column("digest_mode", sa.String(length=16), nullable=False),
        sa.Column("onboarded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_telegram_id"), ["telegram_id"], unique=True)

    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("price_amount", sa.String(length=40), nullable=True),
        sa.Column("price_currency", sa.String(length=3), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("confirm_nonce", sa.String(length=64), nullable=True),
        sa.Column("confirm_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("handoff_url", sa.Text(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_bookings_state"), ["state"], unique=False)
        batch_op.create_index(batch_op.f("ix_bookings_user_id"), ["user_id"], unique=False)

    op.create_table(
        "itineraries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("itineraries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_itineraries_user_id"), ["user_id"], unique=False)

    op.create_table(
        "radars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=32), nullable=False),
        sa.Column("intent", sa.JSON(), nullable=False),
        sa.Column("monitor_kind", sa.String(length=32), nullable=False),
        sa.Column("threshold_amount", sa.String(length=40), nullable=True),
        sa.Column("threshold_currency", sa.String(length=3), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("interval_s", sa.Integer(), nullable=False),
        sa.Column("providers", sa.JSON(), nullable=False),
        sa.Column("notify_mode", sa.String(length=16), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checks", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("radars", schema=None) as batch_op:
        batch_op.create_index("ix_radars_due", ["state", "next_check_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_radars_next_check_at"), ["next_check_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_radars_state"), ["state"], unique=False)
        batch_op.create_index(batch_op.f("ix_radars_user_id"), ["user_id"], unique=False)

    op.create_table(
        "search_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("intent", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("search_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_search_sessions_user_id"), ["user_id"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("radar_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("digest", sa.Boolean(), nullable=False),
        sa.Column("deliver_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["radar_id"], ["radars.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_notifications_deliver_after"), ["deliver_after"], unique=False)
        batch_op.create_index(batch_op.f("ix_notifications_sent_at"), ["sent_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_notifications_user_id"), ["user_id"], unique=False)

    op.create_table(
        "radar_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("radar_id", sa.Integer(), nullable=False),
        sa.Column("result_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("price_amount", sa.String(length=40), nullable=True),
        sa.Column("price_currency", sa.String(length=3), nullable=True),
        sa.Column("availability", sa.String(length=16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("missing_checks", sa.Integer(), nullable=False),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["radar_id"], ["radars.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("radar_id", "result_key"),
    )
    with op.batch_alter_table("radar_observations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_radar_observations_radar_id"), ["radar_id"], unique=False)


def downgrade() -> None:
    # schema baseline
    with op.batch_alter_table("radar_observations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_radar_observations_radar_id"))

    op.drop_table("radar_observations")
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notifications_user_id"))
        batch_op.drop_index(batch_op.f("ix_notifications_sent_at"))
        batch_op.drop_index(batch_op.f("ix_notifications_deliver_after"))

    op.drop_table("notifications")
    with op.batch_alter_table("search_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_search_sessions_user_id"))

    op.drop_table("search_sessions")
    with op.batch_alter_table("radars", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_radars_user_id"))
        batch_op.drop_index(batch_op.f("ix_radars_state"))
        batch_op.drop_index(batch_op.f("ix_radars_next_check_at"))
        batch_op.drop_index("ix_radars_due")

    op.drop_table("radars")
    with op.batch_alter_table("itineraries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_itineraries_user_id"))

    op.drop_table("itineraries")
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_bookings_user_id"))
        batch_op.drop_index(batch_op.f("ix_bookings_state"))

    op.drop_table("bookings")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_telegram_id"))

    op.drop_table("users")
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_log_user_id"))

    op.drop_table("audit_log")
