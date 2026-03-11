from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.cache import MemoryCache, RedisCache
from app.clients.qq_api import QQApiClient
from app.clients.steam_client import SteamClient
from app.clients.xhh_spider import XiaoHeiHeSpider
from app.config import get_settings
from app.database import init_db
from app.logging import setup_logging
from app.qq_adapter import QQAdapter
from app.scheduler import BotScheduler
from app.schemas import QQEvent, QQEventMessage
from app.services.game_service import GameService
from app.services.nlp_recommendation import LLMReranker

setup_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


def _build_cache():
    try:
        return RedisCache(settings.redis_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis unavailable, using memory cache: %s", exc)
        return MemoryCache()


cache = _build_cache()
steam_client = SteamClient(settings)
xhh_spider = XiaoHeiHeSpider(settings)
reranker = LLMReranker(settings)
service = GameService(
    settings=settings,
    cache=cache,
    steam_client=steam_client,
    xhh_spider=xhh_spider,
    reranker=reranker,
)
qq_client = QQApiClient(settings)
adapter = QQAdapter(settings=settings, service=service, qq_client=qq_client, cache=cache)
scheduler = BotScheduler(settings=settings, service=service, adapter=adapter)

app = FastAPI(title="QQ Discount Bot")


@app.on_event("startup")
def startup() -> None:
    init_db()
    scheduler.start()


@app.on_event("shutdown")
def shutdown() -> None:
    scheduler.shutdown()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/qq/events")
async def qq_events(request: Request) -> JSONResponse:
    body = await request.body()
    verify_signature(request=request, body=body)

    payload = await request.json()
    event = normalize_event(payload)
    if event is None:
        return JSONResponse({"ok": True, "ignored": True})

    if "MESSAGE" not in event.event_type.upper():
        return JSONResponse({"ok": True, "ignored": True})

    content = event.message.content or ""
    if "@" not in content and "<@" not in content and settings.qq_bot_name not in content:
        return JSONResponse({"ok": True, "ignored": True})

    response = adapter.on_mention_query(event.message)
    sent = qq_client.send_message(channel_id=event.message.channel_id, content=response)
    return JSONResponse({"ok": True, "sent": sent, "preview": response[:120]})


def verify_signature(request: Request, body: bytes) -> None:
    if not settings.qq_webhook_secret:
        return

    signature = request.headers.get("X-Signature") or request.headers.get("X-QQ-Signature")
    if not signature:
        raise HTTPException(status_code=401, detail="signature missing")

    digest = hmac.new(settings.qq_webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, signature):
        raise HTTPException(status_code=401, detail="signature invalid")


def normalize_event(payload: dict[str, Any]) -> QQEvent | None:
    event_type = payload.get("event_type") or payload.get("t") or ""
    data = payload.get("message") or payload.get("d") or {}

    if not isinstance(data, dict):
        return None

    content = data.get("content") or ""
    channel_id = data.get("channel_id") or data.get("group_id") or data.get("guild_id")
    user_id = (data.get("author") or {}).get("id") or data.get("user_id")

    if not channel_id or not user_id:
        return None

    message = QQEventMessage(
        content=content,
        channel_id=str(channel_id),
        guild_id=str(data.get("guild_id")) if data.get("guild_id") else None,
        group_id=str(data.get("group_id")) if data.get("group_id") else None,
        user_id=str(user_id),
        session_id=str(data.get("session_id") or f"{channel_id}:{user_id}"),
    )
    return QQEvent(event_type=event_type, message=message)
