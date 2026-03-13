from __future__ import annotations

import random
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.exceptions import DataSourceUnavailable


class XiaoHeiHeSpider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=settings.xhh_timeout_seconds)
        self._agents = settings.xhh_agent_pool()

    def fetch_game_snapshot(self, appid: int, steam_name: str | None = None) -> dict[str, Any]:
        url = self.settings.xhh_game_url_template.format(appid=appid)
        headers = {
            "User-Agent": random.choice(self._agents),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.xiaoheihe.cn/",
        }
        resp = self.client.get(url, headers=headers, follow_redirects=True)
        if resp.status_code >= 400:
            raise DataSourceUnavailable(f"XHH request failed: {resp.status_code}")
        final_url = str(getattr(resp, "url", url))
        if "/app/bbs/home" in final_url:
            raise DataSourceUnavailable("XHH redirected to home page (likely anti-bot/unauthenticated)")

        soup = BeautifulSoup(resp.text, "html.parser")
        full_text = soup.get_text(" ", strip=True)

        current_price = self._extract_price(full_text)
        discount = self._extract_discount(full_text)
        review_percent = self._extract_review_percent(full_text)
        review_desc = self._extract_review_desc(full_text)

        if current_price is None and review_percent is None:
            raise DataSourceUnavailable("XHH parse failed from public page")

        original_price = None
        if current_price is not None and discount and discount > 0:
            original_price = int(round(current_price / (1 - discount / 100)))

        return {
            "name": steam_name,
            "currency": "CNY",
            "final_price": current_price,
            "original_price": original_price,
            "discount_percent": discount or 0,
            "recent_summary": review_desc,
            "recent_percent": review_percent,
            "recent_total": None,
            "overall_summary": review_desc,
            "overall_percent": review_percent,
            "overall_total": None,
        }

    @staticmethod
    def _extract_price(text: str) -> int | None:
        pattern = re.compile(r"(?:￥|¥)\s*([0-9]+(?:\.[0-9]{1,2})?)")
        match = pattern.search(text)
        if not match:
            return None
        value = float(match.group(1))
        return int(round(value * 100))

    @staticmethod
    def _extract_discount(text: str) -> int | None:
        pattern = re.compile(r"([1-9][0-9]?)%\s*OFF|(-[1-9][0-9]?)%")
        match = pattern.search(text)
        if not match:
            return None
        raw = match.group(1) or match.group(2)
        raw = raw.replace("%", "")
        return abs(int(raw))

    @staticmethod
    def _extract_review_percent(text: str) -> int | None:
        pattern = re.compile(r"好评率\s*([0-9]{1,3})%|([0-9]{1,3})%\s*好评")
        match = pattern.search(text)
        if not match:
            return None
        value = match.group(1) or match.group(2)
        return int(value)

    @staticmethod
    def _extract_review_desc(text: str) -> str | None:
        options = ["好评如潮", "特别好评", "多半好评", "褒贬不一", "多半差评", "差评如潮"]
        for item in options:
            if item in text:
                return item
        return None
