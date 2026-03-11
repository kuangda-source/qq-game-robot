from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.database import Base


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appid: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appid: Mapped[int] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    currency: Mapped[str] = mapped_column(String(12), default="CNY")
    original_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    final_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)
    popularity_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ReviewSnapshot(Base):
    __tablename__ = "review_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appid: Mapped[int] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    recent_summary: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    recent_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    recent_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    overall_summary: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    overall_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    overall_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class SourceComparison(Base):
    __tablename__ = "source_comparisons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appid: Mapped[int] = mapped_column(Integer, index=True)
    steam_final_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    xhh_final_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    steam_overall_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    xhh_overall_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    diff_flags: Mapped[dict] = mapped_column(JSON, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    request_text: Mapped[str] = mapped_column(Text)
    response_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GameAlias(Base):
    __tablename__ = "game_aliases"
    __table_args__ = (UniqueConstraint("appid", "alias", name="uq_appid_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appid: Mapped[int] = mapped_column(Integer, index=True)
    alias: Mapped[str] = mapped_column(String(255), index=True)
