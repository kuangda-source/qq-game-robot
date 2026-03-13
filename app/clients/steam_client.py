from __future__ import annotations

from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.exceptions import DataSourceUnavailable


class SteamClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)

    def get_top_seller_discounts(self, limit: int, region: str = "cn", currency: str = "CNY") -> list[dict[str, Any]]:
        url = f"{self.settings.steam_base_url}/api/featuredcategories"
        params = {"cc": region, "l": self.settings.steam_lang}
        resp = self.client.get(url, params=params)
        if resp.status_code >= 400:
            raise DataSourceUnavailable(f"Steam top_sellers request failed: {resp.status_code}")

        payload = resp.json()
        items = (payload.get("top_sellers") or {}).get("items", [])
        output = []
        for index, item in enumerate(items[:limit], start=1):
            discount = int(item.get("discount_percent") or 0)
            if discount <= 0:
                continue
            output.append(
                {
                    "appid": int(item.get("id")),
                    "name": item.get("name") or "",
                    "original_price": item.get("original_price"),
                    "final_price": item.get("final_price"),
                    "currency": currency,
                    "discount_percent": discount,
                    "popularity_rank": index,
                }
            )
        return output

    def search_apps(self, keyword: str, limit: int = 5, region: str = "cn") -> list[dict[str, Any]]:
        query = keyword.strip()
        if not query:
            return []

        url = f"{self.settings.steam_base_url}/api/storesearch"
        params = {
            "term": query,
            "l": self.settings.steam_lang,
            "cc": region,
        }
        resp = self.client.get(url, params=params)
        if resp.status_code >= 400:
            raise DataSourceUnavailable(f"Steam store search failed: {resp.status_code}")

        payload = resp.json() or {}
        items = payload.get("items") or []
        results: list[dict[str, Any]] = []
        for item in items[:limit]:
            appid = item.get("id")
            name = item.get("name")
            if not appid or not name:
                continue
            results.append({"appid": int(appid), "name": str(name)})
        return results

    def get_app_details(self, appid: int, region: str = "cn") -> dict[str, Any]:
        url = f"{self.settings.steam_base_url}/api/appdetails"
        params = {"appids": appid, "cc": region, "l": self.settings.steam_lang}
        resp = self.client.get(url, params=params)
        if resp.status_code >= 400:
            raise DataSourceUnavailable(f"Steam app details failed: {resp.status_code}")

        data = (resp.json().get(str(appid)) or {}).get("data")
        if not data:
            raise DataSourceUnavailable(f"Steam app details missing for {appid}")

        genres = [item.get("description") for item in data.get("genres", []) if item.get("description")]
        categories = [item.get("description") for item in data.get("categories", []) if item.get("description")]
        store_tags = self._fetch_store_tags(appid=appid, region=region)
        all_tags = self._unique_text(genres + categories + store_tags)

        price_overview = data.get("price_overview") or {}
        return {
            "appid": appid,
            "name": data.get("name") or str(appid),
            "genres": genres,
            "tags": all_tags,
            "currency": price_overview.get("currency") or "CNY",
            "original_price": price_overview.get("initial"),
            "final_price": price_overview.get("final"),
            "discount_percent": int(price_overview.get("discount_percent") or 0),
            "aliases": [data.get("name") or "", data.get("short_description") or ""],
        }

    def get_review_summary(self, appid: int) -> dict[str, Any]:
        overall = self._get_reviews(appid, "all")
        recent = self._get_reviews(appid, "recent")
        return {
            "recent_summary": recent.get("review_score_desc"),
            "recent_percent": self._calc_percent(recent),
            "recent_total": recent.get("total_reviews"),
            "overall_summary": overall.get("review_score_desc"),
            "overall_percent": self._calc_percent(overall),
            "overall_total": overall.get("total_reviews"),
        }

    def _get_reviews(self, appid: int, review_filter: str) -> dict[str, Any]:
        url = f"{self.settings.steam_base_url}/appreviews/{appid}"
        params = {
            "json": 1,
            "language": self.settings.steam_lang,
            "filter": review_filter,
            "num_per_page": 0,
        }
        resp = self.client.get(url, params=params)
        if resp.status_code >= 400:
            raise DataSourceUnavailable(f"Steam reviews failed: {resp.status_code}")

        query_summary = (resp.json() or {}).get("query_summary") or {}
        return query_summary

    def _fetch_store_tags(self, appid: int, region: str = "cn") -> list[str]:
        # Steam Web API does not expose community tags directly; fetch public store page tags as supplement.
        url = f"{self.settings.steam_base_url}/app/{appid}"
        params = {"cc": region, "l": self.settings.steam_lang}
        cookies = {
            "birthtime": "0",
            "lastagecheckage": "1-0-1980",
            "wants_mature_content": "1",
        }
        try:
            resp = self.client.get(url, params=params, cookies=cookies, follow_redirects=True)
            if resp.status_code >= 400:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            tags = []
            for node in soup.select("a.app_tag"):
                value = node.get_text(" ", strip=True)
                if value:
                    tags.append(value)
            return self._unique_text(tags)
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _unique_text(values: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for value in values:
            normalized = value.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(normalized)
        return output

    @staticmethod
    def _calc_percent(query_summary: dict[str, Any]) -> int | None:
        total = query_summary.get("total_reviews")
        positive = query_summary.get("total_positive")
        if not total or positive is None:
            return None
        return int(round(positive * 100 / total))
