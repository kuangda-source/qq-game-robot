from __future__ import annotations

import json
import time
import re
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
        resp = self._get_with_retry(url=url, params=params)
        if resp.status_code >= 400:
            raise DataSourceUnavailable(f"Steam top_sellers request failed: {resp.status_code}")

        payload = resp.json()
        top_items = (payload.get("top_sellers") or {}).get("items", [])
        specials = (payload.get("specials") or {}).get("items", [])

        output: list[dict[str, Any]] = []
        seen: set[int] = set()

        def append_items(items: list[dict[str, Any]], base_rank: int) -> int:
            rank = base_rank
            for item in items:
                appid_raw = item.get("id")
                if appid_raw is None:
                    continue
                appid = int(appid_raw)
                if appid in seen:
                    continue
                discount = int(item.get("discount_percent") or 0)
                if discount <= 0:
                    continue
                seen.add(appid)
                output.append(
                    {
                        "appid": appid,
                        "name": item.get("name") or "",
                        "original_price": item.get("original_price"),
                        "final_price": item.get("final_price"),
                        "currency": currency,
                        "discount_percent": discount,
                        "popularity_rank": rank,
                    }
                )
                rank += 1
                if len(output) >= limit:
                    break
            return rank

        rank_cursor = append_items(top_items, 1)
        if len(output) < limit:
            append_items(specials, rank_cursor)
        return output[:limit]

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
        resp = self._get_with_retry(url=url, params=params)
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
        resp = self._get_with_retry(url=url, params=params)
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
        resp = self._get_with_retry(url=url, params=params)
        if resp.status_code >= 400:
            raise DataSourceUnavailable(f"Steam reviews failed: {resp.status_code}")

        query_summary = (resp.json() or {}).get("query_summary") or {}
        return query_summary

    def _fetch_store_tags(self, appid: int, region: str = "cn") -> list[str]:
        # Steam Web API does not expose community tags directly; fetch public store page tags as supplement.
        soup = self._fetch_store_soup(appid=appid, region=region)
        if soup is None:
            return []
        tags = []
        for node in soup.select("a.app_tag"):
            value = node.get_text(" ", strip=True)
            if value:
                tags.append(value)
        return self._unique_text(tags)

    def get_similar_appids(self, appid: int, region: str = "cn", limit: int = 30) -> list[int]:
        soup = self._fetch_store_soup(appid=appid, region=region)
        if soup is None:
            return []

        by_carousel = self._extract_similar_appids_from_carousel(soup=soup, appid=appid, limit=limit)
        if by_carousel:
            return by_carousel

        roots = []
        for selector in ["#recommended_block", ".similar_grid_ctn", ".recommendation_carousel_items"]:
            roots.extend(soup.select(selector))

        if not roots:
            root = self._find_similar_section_root(soup)
            if root is not None:
                roots = [root]
            else:
                return []

        output: list[int] = []
        seen: set[int] = set()
        for root in roots:
            for node in root.select("[data-ds-appid], a[href*='/app/']"):
                for candidate in self._parse_candidate_appids(node=node):
                    if candidate == appid or candidate in seen:
                        continue
                    seen.add(candidate)
                    output.append(candidate)
                    if len(output) >= limit:
                        return output
        return output

    def _extract_similar_appids_from_carousel(self, soup: BeautifulSoup, appid: int, limit: int) -> list[int]:
        output: list[int] = []
        seen: set[int] = set()
        for node in soup.select("[data-featuretarget='storeitems-carousel']"):
            raw_props = node.get("data-props")
            if not raw_props:
                continue
            try:
                props = json.loads(raw_props)
            except Exception:
                continue

            title = str(props.get("title") or "")
            see_all = str(props.get("seeAllLink") or "")
            if ("类似产品" not in title and "More Like This" not in title) and "/recommended/morelike/" not in see_all:
                continue

            for value in props.get("appIDs") or []:
                try:
                    candidate = int(value)
                except Exception:
                    continue
                if candidate == appid or candidate in seen:
                    continue
                seen.add(candidate)
                output.append(candidate)
                if len(output) >= limit:
                    return output
        return output

    @staticmethod
    def _find_similar_section_root(soup: BeautifulSoup):  # noqa: ANN205
        marker_texts = {"更多类似产品", "更多相似产品", "More Like This"}
        for node in soup.find_all(["h2", "div", "span"]):
            text = node.get_text(" ", strip=True)
            if text in marker_texts:
                parent = node.find_parent("div")
                if parent is not None:
                    return parent
        return None

    def _fetch_store_soup(self, appid: int, region: str = "cn") -> BeautifulSoup | None:
        url = f"{self.settings.steam_base_url}/app/{appid}"
        params = {"cc": region, "l": self.settings.steam_lang}
        cookies = {
            "birthtime": "0",
            "lastagecheckage": "1-0-1980",
            "wants_mature_content": "1",
        }
        try:
            resp = self._get_with_retry(url=url, params=params, cookies=cookies, follow_redirects=True)
            if resp.status_code >= 400:
                return None
            return BeautifulSoup(resp.text, "html.parser")
        except Exception:  # noqa: BLE001
            return None

    def _get_with_retry(self, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return self.client.get(url, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
                continue
        raise DataSourceUnavailable(f"Steam request failed after retries: {last_exc}")

    @staticmethod
    def _parse_candidate_appids(node) -> list[int]:  # noqa: ANN001
        output: list[int] = []
        raw = node.get("data-ds-appid")
        if raw:
            for value in re.findall(r"\d+", str(raw)):
                try:
                    output.append(int(value))
                except ValueError:
                    continue

        href = node.get("href") or ""
        match = re.search(r"/app/(\d+)", href)
        if match:
            try:
                output.append(int(match.group(1)))
            except ValueError:
                pass
        return output

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
