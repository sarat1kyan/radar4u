"""Relational schema. Portable across PostgreSQL (production) and SQLite
(tests, local dev). Money is stored as a decimal string plus currency code so
no database float ever touches an amount.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from radar.core.provenance import utcnow


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    language: Mapped[str] = mapped_column(String(8), default="en")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    time_format: Mapped[str] = mapped_column(String(3), default="24h")
    timezone: Mapped[str | None] = mapped_column(String(64))
    home_place: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    favorites: Mapped[list[Any]] = mapped_column(JSON, default=list)
    quiet_start: Mapped[time | None] = mapped_column(Time)
    quiet_end: Mapped[time | None] = mapped_column(Time)
    digest_mode: Mapped[str] = mapped_column(String(16), default="immediate")
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()

    radars: Mapped[list[Radar]] = relationship(back_populates="user", cascade="all, delete-orphan")


class SearchSession(Base):
    """Last search per chat: intent for refinements, results for button callbacks."""

    __tablename__ = "search_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    intent: Mapped[dict[str, Any]] = mapped_column(JSON)
    results: Mapped[list[Any]] = mapped_column(JSON, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class Radar(Base):
    __tablename__ = "radars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(32))
    intent: Mapped[dict[str, Any]] = mapped_column(JSON)
    monitor_kind: Mapped[str] = mapped_column(String(32))
    threshold_amount: Mapped[str | None] = mapped_column(String(40))
    threshold_currency: Mapped[str | None] = mapped_column(String(3))
    state: Mapped[str] = mapped_column(String(16), default="active", index=True)
    interval_s: Mapped[int] = mapped_column(Integer)
    providers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    notify_mode: Mapped[str] = mapped_column(String(16), default="immediate")
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checks: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()

    user: Mapped[User] = relationship(back_populates="radars")
    observations: Mapped[list[RadarObservation]] = relationship(cascade="all, delete-orphan")

    __table_args__ = (Index("ix_radars_due", "state", "next_check_at"),)


class RadarObservation(Base):
    __tablename__ = "radar_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    radar_id: Mapped[int] = mapped_column(ForeignKey("radars.id", ondelete="CASCADE"), index=True)
    result_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    provider: Mapped[str] = mapped_column(String(64))
    price_amount: Mapped[str | None] = mapped_column(String(40))
    price_currency: Mapped[str | None] = mapped_column(String(3))
    availability: Mapped[str] = mapped_column(String(16))
    first_seen_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    missing_checks: Mapped[int] = mapped_column(Integer, default=0)
    # Price history is kept only where the provider's terms allow storing it.
    history: Mapped[list[Any]] = mapped_column(JSON, default=list)

    __table_args__ = (UniqueConstraint("radar_id", "result_key"),)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    radar_id: Mapped[int | None] = mapped_column(ForeignKey("radars.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    digest: Mapped[bool] = mapped_column(Boolean, default=False)
    deliver_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), index=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    price_amount: Mapped[str | None] = mapped_column(String(40))
    price_currency: Mapped[str | None] = mapped_column(String(3))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    confirm_nonce: Mapped[str | None] = mapped_column(String(64))
    confirm_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference: Mapped[str | None] = mapped_column(String(120))
    handoff_url: Mapped[str | None] = mapped_column(Text)
    events: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Itinerary(Base):
    __tablename__ = "itineraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class AuditLog(Base):
    """State-changing and externally binding actions. No message text, no sensitive values."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()
