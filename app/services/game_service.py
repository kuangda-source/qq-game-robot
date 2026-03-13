from __future__ import annotations

import logging
import re
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
    _NON_GAMEPLAY_TAG_KEYWORDS = {
        "steam achievements",
        "steam trading cards",
        "steam cloud",
        "steam deck",
        "full controller support",
        "partial controller support",
        "remote play",
        "remote play together",
        "family sharing",
        "stats",
        "captions available",
        "includes level editor",
        "workshop",
        "commentary available",
        "in-app purchases",
        "profile features limited",
        "points shop items available",
        "steam 集换式卡牌",
        "steam 云",
        "远程同乐",
        "支持控制器",
        "完全支持控制器",
        "部分支持控制器",
        "家庭共享",
        "创意工坊",
        "可用字幕",
        "统计数据",
        "点数商店物品",
        "应用内购买",
        "steam 成就",
    }
    _SHOOTER_KEYWORDS = {"fps", "射击", "shooter", "战地", "counter-strike", "cs2", "枪战", "tactical shooter"}
    _MULTIPLAYER_KEYWORDS = {"multi-player", "multiplayer", "online co-op", "co-op", "pvp", "在线合作", "多人", "联机"}
    _SINGLE_PLAYER_KEYWORDS = {"single-player", "single player", "单人"}
    _CONCEPT_KEYWORDS = {
        "soulslike": {"souls-like", "soulslike", "类魂", "魂类", "魂系"},
        "melee_action": {"action", "动作", "hack and slash", "hack & slash", "character action", "近战", "武术", "格斗"},
        "boss_challenge": {"boss", "首领", "高难度", "困难", "parry", "格挡", "dodge", "闪避"},
        "action_rpg": {"action rpg", "arpg", "角色扮演", "rpg", "动作角色扮演"},
        "action_adventure": {"adventure", "冒险", "剧情", "story rich"},
    }

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
        try:
            items = self.steam_client.get_top_seller_discounts(limit=limit, region=region, currency=currency)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Refresh market list failed: %s", exc)
            return 0
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
        details = self._load_details_with_degrade(appid=appid, preload=preload)
        try:
            reviews = self.steam_client.get_review_summary(appid=appid)
        except Exception as exc:  # noqa: BLE001
            logger.info("Steam review degrade for app %s: %s", appid, exc)
            reviews = {}

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

    def _load_details_with_degrade(self, appid: int, preload: dict | None = None) -> dict:
        try:
            return self.steam_client.get_app_details(appid=appid, region=self.settings.steam_cc)
        except Exception as exc:  # noqa: BLE001
            if preload:
                logger.info("Steam app details degrade for app %s, fallback to preload: %s", appid, exc)
                return {
                    "appid": appid,
                    "name": preload.get("name") or str(appid),
                    "genres": [],
                    "tags": [],
                    "currency": preload.get("currency") or "CNY",
                    "original_price": preload.get("original_price"),
                    "final_price": preload.get("final_price"),
                    "discount_percent": int(preload.get("discount_percent") or 0),
                    "aliases": [preload.get("name") or ""],
                }
            raise

    def get_daily_hot_discounts(self, limit: int, region: str = "cn", currency: str = "CNY") -> list[DailyDiscountItem]:
        cache_key = f"daily_hot:{region}:{currency}:{limit}"
        cached = self.cache.get_json(cache_key)
        if cached:
            return [DailyDiscountItem.model_validate(item) for item in cached["items"]]

        with self.session_factory() as session:
            repo = GameRepository(session)
            items = repo.list_hot_discounts(limit=limit)

        if len(items) < max(3, limit // 2):
            try:
                self.refresh_market_data(limit=max(limit * 3, 30), region=region, currency=currency)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Daily hot refresh attempt #1 failed: %s", exc)
            with self.session_factory() as session:
                repo = GameRepository(session)
                items = repo.list_hot_discounts(limit=limit)
        if len(items) < limit:
            try:
                self.refresh_market_data(limit=max(limit * 6, 80), region=region, currency=currency)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Daily hot refresh attempt #2 failed: %s", exc)
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
        if seed_result.status == "ambiguous" and seed_result.candidates:
            # For recommendation flow, default to top candidate to keep conversation smooth.
            # Query flow still keeps explicit disambiguation.
            fallback_appid = int(seed_result.candidates[0]["appid"])
            snapshot = self._get_snapshot_or_refresh(fallback_appid)
            if snapshot:
                seed_result = QueryResult(status="ok", game=snapshot)
        if seed_result.status != "ok" or not seed_result.game:
            self._seed_from_store_search(query, max_count=3)
            seed_result = self.query_game_snapshot(query)
        if seed_result.status == "ambiguous" and seed_result.candidates:
            fallback_appid = int(seed_result.candidates[0]["appid"])
            snapshot = self._get_snapshot_or_refresh(fallback_appid)
            if snapshot:
                seed_result = QueryResult(status="ok", game=snapshot)
        if seed_result.status != "ok" or not seed_result.game:
            raise DataSourceUnavailable(seed_result.message or "seed game unavailable")

        seed = seed_result.game
        similar_appids = self.steam_client.get_similar_appids(appid=seed.appid, region=self.settings.steam_cc, limit=40)
        similar_appid_set = set(similar_appids)
        prioritized: dict[int, GameSnapshot] = {}
        for appid in similar_appids:
            snapshot = self._get_snapshot_or_refresh(appid)
            if not snapshot:
                continue
            if (snapshot.steam_price.discount_percent or 0) <= 0:
                continue
            prioritized[snapshot.appid] = snapshot

        with self.session_factory() as session:
            repo = GameRepository(session)
            raw_candidates = repo.list_discounted_candidates(exclude_appid=seed.appid, max_count=120)

        candidate_pool: dict[int, GameSnapshot] = {}
        if similar_appid_set and prioritized:
            candidate_pool.update(prioritized)
        else:
            for item in raw_candidates:
                if item.appid == seed.appid:
                    continue
                candidate_pool[item.appid] = item

        filtered = []
        seed_genres = self._normalize_terms(seed.genres)
        seed_tags = self._strip_non_gameplay_tags(self._normalize_terms(seed.tags))
        seed_concepts = self._extract_gameplay_concepts(seed.name, seed_genres, seed_tags)
        seed_is_shooter = self._is_shooter(seed.name, seed_genres, seed_tags)
        seed_prefers_single = self._prefers_single_player(seed_tags)
        min_overlap = self._required_genre_overlap(seed_genres)
        for item in candidate_pool.values():
            review_percent = item.steam_review.overall_percent
            is_prioritized = item.appid in similar_appid_set
            if review_percent is not None and review_percent < self.settings.min_recommend_review_percent and not is_prioritized:
                continue
            if review_percent is None and not is_prioritized:
                continue
            cand_genres = self._normalize_terms(item.genres)
            cand_tags = self._strip_non_gameplay_tags(self._normalize_terms(item.tags))
            overlap = seed_genres.intersection(cand_genres)
            overlap_count = len(overlap)
            if overlap_count < min_overlap and not is_prioritized:
                continue

            genre_score = (overlap_count / len(seed_genres)) if seed_genres else 0
            tag_overlap = len(seed_tags.intersection(cand_tags))
            tag_score = (tag_overlap / len(seed_tags)) if seed_tags else 0
            cand_concepts = self._extract_gameplay_concepts(item.name, cand_genres, cand_tags)
            concept_overlap = len(seed_concepts.intersection(cand_concepts))
            concept_score = (concept_overlap / len(seed_concepts)) if seed_concepts else 0

            if similar_appid_set and not is_prioritized:
                if seed_concepts and concept_overlap == 0:
                    continue
                if not seed_concepts and tag_overlap == 0 and overlap_count == 0:
                    continue
            # If seed has a clear gameplay concept (e.g. soulslike), reject concept-mismatch candidates.
            if seed_concepts and concept_overlap == 0 and not is_prioritized:
                continue

            discount_score = min(1.0, (item.steam_price.discount_percent or 0) / 80)
            review_score = ((review_percent or 0) / 100) * 0.6
            if seed_concepts:
                score = (
                    concept_score * 0.40
                    + genre_score * 0.25
                    + tag_score * 0.20
                    + discount_score * 0.075
                    + review_score * 0.075
                )
            else:
                score = genre_score * 0.45 + tag_score * 0.22 + discount_score * 0.165 + review_score * 0.165

            # Penalize mismatch in play style: seed like 黑神话 should avoid party/FPS-heavy results.
            if seed_prefers_single and self._is_multiplayer_focused(cand_tags):
                score -= 0.25
            if not seed_is_shooter and self._is_shooter(item.name, cand_genres, cand_tags):
                score -= 0.20
            if "soulslike" in seed_concepts and "soulslike" not in cand_concepts:
                score -= 0.20
            if is_prioritized:
                score += 0.18

            if score < 0.22 and not is_prioritized:
                continue

            filtered.append(
                CandidateGame(
                    appid=item.appid,
                    name=item.name,
                    genres=item.genres,
                    tags=item.tags,
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

    @staticmethod
    def _normalize_terms(values: list[str] | None) -> set[str]:
        if not values:
            return set()
        output: set[str] = set()
        for value in values:
            normalized = value.strip().lower()
            if not normalized:
                continue
            # Keep only meaningful terms.
            normalized = re.sub(r"\s+", " ", normalized)
            output.add(normalized)
        return output

    @staticmethod
    def _required_genre_overlap(seed_genres: set[str]) -> int:
        # Stronger similarity gate for broad-genre seeds (e.g. action/adventure/rpg).
        if len(seed_genres) >= 3:
            return 2
        if len(seed_genres) >= 2:
            return 1
        return 1 if seed_genres else 0

    @staticmethod
    def _is_multiplayer_focused(tags: set[str]) -> bool:
        words = GameService._MULTIPLAYER_KEYWORDS
        # If explicit single-player exists alongside multiplayer, do not over-penalize.
        has_multi = any(word in " ".join(tags) for word in words)
        has_single = any(word in " ".join(tags) for word in GameService._SINGLE_PLAYER_KEYWORDS)
        return has_multi and not has_single

    @staticmethod
    def _prefers_single_player(tags: set[str]) -> bool:
        text = " ".join(tags)
        has_single = any(word in text for word in GameService._SINGLE_PLAYER_KEYWORDS)
        has_multi = any(word in text for word in GameService._MULTIPLAYER_KEYWORDS)
        return has_single and not has_multi

    @staticmethod
    def _is_shooter(name: str, genres: set[str], tags: set[str]) -> bool:
        text = f"{name.lower()} {' '.join(genres)} {' '.join(tags)}"
        return any(word in text for word in GameService._SHOOTER_KEYWORDS)

    @staticmethod
    def _strip_non_gameplay_tags(tags: set[str]) -> set[str]:
        if not tags:
            return set()
        return {
            tag
            for tag in tags
            if not any(noise in tag for noise in GameService._NON_GAMEPLAY_TAG_KEYWORDS)
        }

    @staticmethod
    def _extract_gameplay_concepts(name: str, genres: set[str], tags: set[str]) -> set[str]:
        text = f"{name.lower()} {' '.join(genres)} {' '.join(tags)}"
        concepts: set[str] = set()
        for concept, keywords in GameService._CONCEPT_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                concepts.add(concept)
        return concepts

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
        queries = self._build_store_search_queries(keyword)
        rows: list[dict] = []
        seen: set[int] = set()
        for query in queries:
            try:
                batch = self.steam_client.search_apps(keyword=query, limit=max_count, region=self.settings.steam_cc)
            except Exception as exc:  # noqa: BLE001
                logger.info("Steam store search fallback failed for '%s': %s", query, exc)
                continue
            for item in batch:
                appid = int(item["appid"])
                if appid in seen:
                    continue
                seen.add(appid)
                rows.append(item)
            if len(rows) >= max_count:
                break

        updated = 0
        for item in rows:
            appid = int(item["appid"])
            try:
                self._refresh_single_app(appid=appid)
                updated += 1
            except Exception as exc:  # noqa: BLE001
                logger.info("Seed app refresh failed for %s: %s", appid, exc)
        return updated

    @staticmethod
    def _build_store_search_queries(keyword: str) -> list[str]:
        base = keyword.strip()
        if not base:
            return []

        normalized = re.sub(r"[\W_]+", "", base, flags=re.UNICODE)
        queries: list[str] = []
        for item in [base, normalized]:
            if item and item not in queries:
                queries.append(item)

        if normalized and len(normalized) >= 4:
            prefix_len = 3 if len(normalized) >= 5 else 2
            prefix = normalized[:prefix_len]
            if prefix and prefix not in queries:
                queries.append(prefix)
        return queries
