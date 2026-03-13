from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PriceInfo(BaseModel):
    currency: str = "CNY"
    original_price: int | None = None
    final_price: int | None = None
    discount_percent: int = 0


class ReviewInfo(BaseModel):
    recent_summary: str | None = None
    recent_percent: int | None = None
    recent_total: int | None = None
    overall_summary: str | None = None
    overall_percent: int | None = None
    overall_total: int | None = None


class GameSnapshot(BaseModel):
    appid: int
    name: str
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    steam_price: PriceInfo
    steam_review: ReviewInfo
    xhh_price: PriceInfo | None = None
    xhh_review: ReviewInfo | None = None
    popularity_rank: int | None = None
    captured_at: datetime


class DailyDiscountItem(BaseModel):
    appid: int
    name: str
    genres: list[str] = Field(default_factory=list)
    steam_price: PriceInfo
    steam_review: ReviewInfo
    xhh_price: PriceInfo | None = None
    xhh_review: ReviewInfo | None = None
    popularity_rank: int | None = None
    captured_at: datetime


class CandidateGame(BaseModel):
    appid: int
    name: str
    genres: list[str] = Field(default_factory=list)
    steam_price: PriceInfo
    steam_review: ReviewInfo
    score: float = 0.0


class RecommendationItem(BaseModel):
    appid: int
    name: str
    reason: str
    steam_price: PriceInfo
    steam_review: ReviewInfo
    score: float


class QueryResult(BaseModel):
    status: Literal["ok", "not_found", "ambiguous"]
    game: GameSnapshot | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    message: str | None = None


class QQEventMessage(BaseModel):
    content: str
    channel_id: str | None = None
    guild_id: str | None = None
    group_id: str | None = None
    group_openid: str | None = None
    user_openid: str | None = None
    user_id: str
    message_id: str | None = None
    event_id: str | None = None
    scene: Literal["group", "channel", "c2c"] = "channel"
    session_id: str


class QQEvent(BaseModel):
    event_type: str
    message: QQEventMessage
