from __future__ import annotations

import logging
import threading
import time

import httpx

from app.config import Settings
from app.schemas import QQEventMessage

logger = logging.getLogger(__name__)


class QQApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)
        self._token: str | None = None
        self._token_expire_at: float = 0.0
        self._lock = threading.Lock()

    def _auth_headers(self) -> dict[str, str] | None:
        if not self.settings.qq_bot_app_id:
            logger.warning("QQ appid not configured")
            return None

        # Prefer official v2 access token flow. Keep legacy token fallback for compatibility.
        if self.settings.qq_bot_secret:
            token = self._get_access_token()
            if not token:
                return None
            auth = f"QQBot {token}"
        elif self.settings.qq_bot_token:
            auth = f"Bot {self.settings.qq_bot_app_id}.{self.settings.qq_bot_token}"
        else:
            logger.warning("QQ credentials not configured. Skip send message")
            return None

        return {
            "Authorization": auth,
            "X-Union-Appid": self.settings.qq_bot_app_id,
            "Content-Type": "application/json",
        }

    def _get_access_token(self) -> str | None:
        now = time.time()
        if self._token and now < self._token_expire_at - 120:
            return self._token

        with self._lock:
            now = time.time()
            if self._token and now < self._token_expire_at - 120:
                return self._token

            if not self.settings.qq_bot_secret or not self.settings.qq_bot_app_id:
                return None

            payload = {
                "appId": self.settings.qq_bot_app_id,
                "clientSecret": self.settings.qq_bot_secret,
            }
            resp = self.client.post(self.settings.qq_access_token_url, json=payload)
            if resp.status_code >= 300:
                logger.error("QQ access token request failed: %s %s", resp.status_code, resp.text)
                return None

            data = resp.json() or {}
            token = data.get("access_token")
            expires_in = int(data.get("expires_in") or 0)
            if not token or expires_in <= 0:
                logger.error("QQ access token response invalid: %s", data)
                return None

            self._token = token
            self._token_expire_at = time.time() + expires_in
            return self._token

    def send_message(
        self,
        scene: str,
        target_id: str,
        content: str,
        msg_id: str | None = None,
        event_id: str | None = None,
        msg_seq: int = 1,
    ) -> bool:
        headers = self._auth_headers()
        if not headers:
            return False

        route = {
            "channel": f"/channels/{target_id}/messages",
            "group": f"/v2/groups/{target_id}/messages",
            "c2c": f"/v2/users/{target_id}/messages",
        }.get(scene)
        if not route:
            logger.error("Unsupported QQ message scene: %s", scene)
            return False

        payload: dict[str, object] = {"content": content}
        if scene in {"group", "c2c"}:
            payload["msg_type"] = 0
            payload["msg_seq"] = msg_seq
            # Group/C2C push requires msg_id or event_id in many cases.
            payload["msg_id"] = msg_id or "MESSAGE_CREATE"
            if event_id:
                payload["event_id"] = event_id
        elif msg_id:
            payload["msg_id"] = msg_id

        url = f"{self.settings.qq_api_base_url}{route}"
        resp = self.client.post(url, headers=headers, json=payload)
        if resp.status_code >= 300:
            logger.error("QQ send message failed: %s %s", resp.status_code, resp.text)
            return False
        return True

    def send_from_event(self, event: QQEventMessage, content: str) -> bool:
        if event.scene == "group" and event.group_openid:
            return self.send_message(
                scene="group",
                target_id=event.group_openid,
                content=content,
                msg_id=event.message_id,
                event_id=event.event_id,
            )

        if event.scene == "c2c" and event.user_openid:
            return self.send_message(
                scene="c2c",
                target_id=event.user_openid,
                content=content,
                msg_id=event.message_id,
                event_id=event.event_id,
            )

        if event.channel_id:
            return self.send_message(
                scene="channel",
                target_id=event.channel_id,
                content=content,
                msg_id=event.message_id,
            )

        logger.error("Unable to route QQ message reply: %s", event.model_dump())
        return False
