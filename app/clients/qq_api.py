from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class QQApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)

    def send_message(self, channel_id: str, content: str) -> bool:
        if not self.settings.qq_bot_app_id or not self.settings.qq_bot_token:
            logger.warning("QQ credentials not configured. Skip send message")
            return False

        url = f"{self.settings.qq_api_base_url}{self.settings.qq_send_message_path_template.format(channel_id=channel_id)}"
        headers = {
            "Authorization": f"Bot {self.settings.qq_bot_app_id}.{self.settings.qq_bot_token}",
            "X-Union-Appid": self.settings.qq_bot_app_id,
            "Content-Type": "application/json",
        }
        payload = {"content": content}
        resp = self.client.post(url, headers=headers, json=payload)
        if resp.status_code >= 300:
            logger.error("QQ send message failed: %s %s", resp.status_code, resp.text)
            return False
        return True
