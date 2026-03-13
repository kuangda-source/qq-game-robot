from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime

from app.cache import CacheBackend
from app.clients.steam_client import SteamClient
from app.clients.xhh_spider import XiaoHeiHeSpider
from app.config import Settings
from app.database import db_session
from app.exceptions import DataSourceUnavailable
from app.repository.game_repository import GameRepository
from app.schemas import CandidateGame, DailyDiscountItem, GameSnapshot, QueryResult
from app.services.nlp_recommendation import LLMReranker

logger = logging.getLogger(__name__)


class GameService:
    def __init__(
        self,
        settings: Settings,
        cache: CacheBackend,
        steam_client: SteamClient,
        xhh_spider: XiaoHeiHeSpider,
        reranker: LLMReranker,
        session_factory: Callable = db_session,
    ):
        self.settings = settings
        self.cache = cache
        self.steam_client = steam_client
        self.xhh_spider = xhh_spider
        self.reranker = reranker
        self.session_factory = session_factory

    def refresh_market_data(self, limit: int | None = None, region: str = "cn", currency: str = "CNY") -> int:
        limit = limit or self.settings.refresh_batch_size
        items = self.steam_client.get_top_seller_discounts(limit=limit, region=region, currency=currency)
        updated = 0
        for item in items:
            appid = item["appid"]
            try:
                self._refresh_single_app(appid=appid, popularity_rank=item.get("popularity_rank"), preload=item)
                updated += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Refresh app %s failed: %s", appid, exc)
        return updated

    def _refresh_single_app(self, appid: int, popularity_rank: int | None = None, preload: dict | None = None) -> None:
        details = self.steam_client.get_app_details(appid=appid, region=self.settings.steam_cc)
        reviews = self.steam_client.get_review_summary(appid=appid)

        currency = preload.get("currency") if preload else details.get("currency")
        final_price = (preload or {}).get("final_price") or details.get("final_price")
        original_price = (preload or {}).get("original_price") or details.get("original_price")
        discount_percent = (preload or {}).get("discount_percent") or details.get("discount_percent") or 0

        xhh_data = None
        try:
            xhh_data = self.xhh_spider.fetch_game_snapshot(appid=appid, steam_name=details.get("name"))
        except DataSourceUnavailable as exc:
            logger.info("XHH degrade for app %s: %s", appid, exc)

        with self.session_factory() as session:
            repo = GameRepository(session)
            repo.upsert_game(
                appid=appid,
                name=details.get("name") or str(appid),
                genres=details.get("genres") or [],
                tags=details.get("tags") or details.get("genres") or [],
                aliases=details.get("aliases") or [],
            )
            repo.save_price_snapshot(
                appid=appid,
                source="steam",
                currency=currency or "CNY",
                original_price=original_price,
                final_price=final_price,
                discount_percent=int(discount_percent),
                popularity_rank=popularity_rank,
            )
            repo.save_review_snapshot(
                appid=appid,
                source="steam",
                recent_summary=reviews.get("recent_summary"),
                recent_percent=reviews.get("recent_percent"),
                recent_total=reviews.get("recent_total"),
                overall_summary=reviews.get("overall_summary"),
                overall_percent=reviews.get("overall_percent"),
                overall_total=reviews.get("overall_total"),
            )

            if xhh_data:
                repo.save_price_snapshot(
                    appid=appid,
                    source="xhh",
                    currency=xhh_data.get("currency") or "CNY",
                    original_price=xhh_data.get("original_price"),
                    final_price=xhh_data.get("final_price"),
                    discount_percent=xhh_data.get("discount_percent") or 0,
                )
                repo.save_review_snapshot(
                    appid=appid,
                    source="xhh",
                    recent_summary=xhh_data.get("recent_summary"),
                    recent_percent=xhh_data.get("recent_percent"),
                    recent_total=xhh_data.get("recent_total"),
                    overall_summary=xhh_data.get("overall_summary"),
                    overall_percent=xhh_data.get("overall_percent"),
                    overall_total=xhh_data.get("overall_total"),
                )

            repo.save_source_comparison(
                appid=appid,
                steam_final_price=final_price,
                xhh_final_price=xhh_data.get("final_price") if xhh_data else None,
                steam_overall_percent=reviews.get("overall_percent"),
                xhh_overall_percent=xhh_data.get("overall_percent") if xhh_data else None,
            )

    def get_daily_hot_discounts(self, limit: int, region: str = "cn", currency: str = "CNY") -> list[DailyDiscountItem]:
        cache_key = f"daily_hot:{region}:{currency}:{limit}"
        cached = self.cache.get_json(cache_key)
        if cached:
            return [DailyDiscountItem.model_validate(item) for item in cached["items"]]

        with self.session_factory() as session:
            repo = GameRepository(session)
            items = repo.list_hot_discounts(limit=limit)

        if len(items) < max(3, limit // 2):
            self.refresh_market_data(limit=max(limit * 3, 30), region=region, currency=currency)
            with self.session_factory() as session:
                repo = GameRepository(session)
                items = repo.list_hot_discounts(limit=limit)

        self.cache.set_json(
            cache_key,
            {
                "items": [item.model_dump(mode="json") for item in items],
                "captured_at": datetime.utcnow().isoformat(),
            },
            ttl_seconds=600,
        )
        return items

    def query_game_snapshot(self, name_or_appid: str) -> QueryResult:
        keyword = name_or_appid.strip()
        if not keyword:
            return QueryResult(status="not_found", message="请输入游戏名或 appid")

        if keyword.isdigit():
            appid = int(keyword)
            snapshot = self._get_snapshot_or_refresh(appid)
            if snapshot is None:
                return QueryResult(status="not_found", message="未找到该游戏")
            return QueryResult(status="ok", game=snapshot)

        candidates = self.resolve_ambiguous_name(keyword)
        if not candidates:
            self.refresh_market_data(limit=40)
            candidates = self.resolve_ambiguous_name(keyword)
        if not candidates:
            self._seed_from_store_search(keyword, max_count=3)
            candidates = self.resolve_ambiguous_name(keyword)

        if not candidates:
            return QueryResult(status="not_found", message="未找到匹配游戏")
        exact = next((item for item in candidates if item["name"].strip().lower() == keyword.lower()), None)
        if exact:
            snapshot = self._get_snapshot_or_refresh(exact["appid"])
            if not snapshot:
                return QueryResult(status="not_found", message="游戏信息暂不可用")
            return QueryResult(status="ok", game=snapshot)
        if len(candidates) > 1:
            return QueryResult(
                status="ambiguous",
                candidates=[{"appid": item["appid"], "name": item["name"]} for item in candidates[:3]],
                message="匹配到多个游戏，请回复序号选择",
            )

        snapshot = self._get_snapshot_or_refresh(candidates[0]["appid"])
        if not snapshot:
            return QueryResult(status="not_found", message="游戏信息暂不可用")
        return QueryResult(status="ok", game=snapshot)

    def resolve_ambiguous_name(self, query: str) -> list[dict]:
        with self.session_factory() as session:
            repo = GameRepository(session)
            candidates = repo.search_games(query, limit=3)
            return [{"appid": item.appid, "name": item.name} for item in candidates]

    def recommend_similar_discounted(self, seed_game: str | int, top_k: int = 5) -> list[dict]:
        query = str(seed_game)
        seed_result = self.query_game_snapshot(query)
        if seed_result.status != "ok" or not seed_result.game:
            self._seed_from_store_search(query, max_count=3)
            seed_result = self.query_game_snapshot(query)
        if seed_result.status != "ok" or not seed_result.game:
            raise DataSourceUnavailable(seed_result.message or "seed game unavailable")

        seed = seed_result.game
        with self.session_factory() as session:
            repo = GameRepository(session)
            raw_candidates = repo.list_discounted_candidates(exclude_appid=seed.appid, max_count=120)

        filtered = []
        seed_genres = set(seed.genres)
        for item in raw_candidates:
            if (item.steam_review.overall_percent or 0) < self.settings.min_recommend_review_percent:
                continue
            overlap = seed_genres.intersection(set(item.genres))
            genre_score = (len(overlap) / len(seed_genres)) if seed_genres else 0
            discount_score = min(1.0, (item.steam_price.discount_percent or 0) / 80)
            review_score = ((item.steam_review.overall_percent or 0) / 100) * 0.6
            score = genre_score * 0.55 + discount_score * 0.25 + review_score * 0.20
            filtered.append(
                CandidateGame(
                    appid=item.appid,
                    name=item.name,
                    genres=item.genres,
                    steam_price=item.steam_price,
                    steam_review=item.steam_review,
                    score=score,
                )
            )

        reranked = self.reranker.rerank(seed=seed, candidates=filtered, top_k=top_k)
        return [
            {
                "appid": item.appid,
                "name": item.name,
                "reason": item.reason,
                "discount_percent": item.steam_price.discount_percent,
                "final_price": item.steam_price.final_price,
                "overall_review": item.steam_review.overall_summary,
                "overall_percent": item.steam_review.overall_percent,
                "score": round(item.score, 3),
            }
            for item in reranked
        ]

    def _get_snapshot_or_refresh(self, appid: int) -> GameSnapshot | None:
        with self.session_factory() as session:
            repo = GameRepository(session)
            snapshot = repo.get_game_snapshot(appid)

        if snapshot:
            return snapshot

        try:
            self._refresh_single_app(appid=appid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Single refresh failed for %s: %s", appid, exc)
            return None

        with self.session_factory() as session:
            repo = GameRepository(session)
            return repo.get_game_snapshot(appid)

    def _seed_from_store_search(self, keyword: str, max_count: int = 3) -> int:
        try:
            rows = self.steam_client.search_apps(keyword=keyword, limit=max_count, region=self.settings.steam_cc)
        except Exception as exc:  # noqa: BLE001
            logger.info("Steam store search fallback failed for '%s': %s", keyword, exc)
            return 0

        updated = 0
        for item in rows:
            appid = int(item["appid"])
            try:
                self._refresh_single_app(appid=appid)
                updated += 1
            except Exception as exc:  # noqa: BLE001
                logger.info("Seed app refresh failed for %s: %s", appid, exc)
        return updated
