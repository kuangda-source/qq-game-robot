from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey

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
    payload = json.loads(body.decode("utf-8") or "{}") if body else {}

    if payload.get("op") == 13:
        return callback_validation(payload)

    verify_signature(request=request, body=body)

    event = normalize_event(payload)
    if event is None:
        return callback_ack()

    if "MESSAGE" not in event.event_type.upper():
        return callback_ack()

    if settings.qq_private_only and event.message.scene != "c2c":
        return callback_ack()

    admin_ids = set(settings.admin_user_id_list())
    if settings.qq_private_only and admin_ids:
        if event.message.user_id not in admin_ids and (event.message.user_openid or "") not in admin_ids:
            qq_client.send_from_event(event.message, "当前机器人仅对管理员开放私聊使用。")
            return callback_ack()

    content = event.message.content or ""
    should_process = any(
        keyword in event.event_type.upper()
        for keyword in ["AT_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"]
    )
    if not should_process and event.message.scene != "c2c":
        if "@" not in content and "<@" not in content and settings.qq_bot_name not in content:
            return callback_ack()

    response = adapter.on_mention_query(event.message)
    qq_client.send_from_event(event.message, response)
    return callback_ack()


def callback_ack() -> JSONResponse:
    # QQ callback mode expects op=12 ack.
    return JSONResponse({"op": 12, "d": 0})


def callback_validation(payload: dict[str, Any]) -> JSONResponse:
    secret = settings.qq_bot_secret or settings.qq_webhook_secret
    if not secret:
        raise HTTPException(status_code=500, detail="qq_bot_secret is required for callback validation")

    data = payload.get("d") or {}
    plain_token = data.get("plain_token")
    event_ts = data.get("event_ts")
    if not plain_token or not event_ts:
        raise HTTPException(status_code=400, detail="invalid callback validation payload")

    signing_key = _build_signing_key(secret)
    signature = signing_key.sign(f"{event_ts}{plain_token}".encode("utf-8")).signature.hex()
    return JSONResponse({"plain_token": plain_token, "signature": signature})


def verify_signature(request: Request, body: bytes) -> None:
    secret = settings.qq_bot_secret or settings.qq_webhook_secret
    if not secret:
        return

    signature = request.headers.get("X-Signature-Ed25519")
    timestamp = request.headers.get("X-Signature-Timestamp")
    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="signature headers missing")

    signing_key = _build_signing_key(secret)
    verify_key = signing_key.verify_key
    try:
        verify_key.verify(timestamp.encode("utf-8") + body, bytes.fromhex(signature))
    except (BadSignatureError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="signature invalid") from exc


def _build_signing_key(secret: str) -> SigningKey:
    # Tencent docs show HexEncoder style. Some control panels expose non-hex secret,
    # so we keep a raw-seed fallback for compatibility.
    try:
        return SigningKey(secret.encode("utf-8"), encoder=HexEncoder)
    except Exception:  # noqa: BLE001
        raw = secret.encode("utf-8")
        if len(raw) < 32:
            raw = raw.ljust(32, b"0")
        elif len(raw) > 32:
            raw = raw[:32]
        return SigningKey(raw)


def normalize_event(payload: dict[str, Any]) -> QQEvent | None:
    event_type = payload.get("t") or payload.get("event_type") or ""
    data = payload.get("d") or payload.get("message") or {}
    event_id = payload.get("id")

    if not isinstance(data, dict):
        return None

    content = data.get("content") or ""
    author = data.get("author") or {}
    user_id = (
        author.get("id")
        or author.get("member_openid")
        or author.get("user_openid")
        or data.get("user_id")
        or data.get("openid")
    )
    if not user_id:
        return None

    group_openid = data.get("group_openid")
    user_openid = author.get("user_openid") or data.get("user_openid") or data.get("openid")
    channel_id = data.get("channel_id")

    scene = "channel"
    session_id = f"{channel_id}:{user_id}" if channel_id else str(user_id)
    if group_openid:
        scene = "group"
        session_id = f"{group_openid}:{user_id}"
    elif user_openid and not channel_id:
        scene = "c2c"
        session_id = f"{user_openid}:{user_id}"

    message = QQEventMessage(
        content=content,
        channel_id=str(channel_id) if channel_id else None,
        guild_id=str(data.get("guild_id")) if data.get("guild_id") else None,
        group_id=str(data.get("group_id")) if data.get("group_id") else None,
        group_openid=str(group_openid) if group_openid else None,
        user_openid=str(user_openid) if user_openid else None,
        user_id=str(user_id),
        message_id=str(data.get("id")) if data.get("id") else None,
        event_id=str(event_id) if event_id else None,
        scene=scene,
        session_id=str(session_id),
    )
    return QQEvent(event_type=event_type, message=message)
