from __future__ import annotations

import json
import logging
from typing import Iterable

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

from app.config import Settings
from app.schemas import CandidateGame, GameSnapshot, RecommendationItem

logger = logging.getLogger(__name__)


class LLMReranker:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.openai_api_key and OpenAI:
            kwargs = {"api_key": settings.openai_api_key}
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self.client = OpenAI(**kwargs)
        else:
            self.client = None

    def rerank(self, seed: GameSnapshot, candidates: Iterable[CandidateGame], top_k: int = 5) -> list[RecommendationItem]:
        candidates = list(candidates)
        fallback = self._fallback(seed, candidates, top_k)
        if not self.client:
            return fallback

        try:
            prompt_payload = {
                "seed_game": {"name": seed.name, "genres": seed.genres, "tags": seed.tags},
                "candidates": [
                    {
                        "appid": c.appid,
                        "name": c.name,
                        "genres": c.genres,
                        "discount_percent": c.steam_price.discount_percent,
                        "final_price": c.steam_price.final_price,
                        "review_percent": c.steam_review.overall_percent,
                        "rule_score": c.score,
                    }
                    for c in candidates[:40]
                ],
                "constraints": {
                    "top_k": top_k,
                    "discounted_only": True,
                    "output": "json",
                    "fields": ["appid", "reason", "score"],
                },
            }

            resp = self.client.responses.create(
                model=self.settings.openai_model,
                input=[
                    {
                        "role": "system",
                        "content": "你是游戏推荐重排器。严格输出JSON数组，每项包含appid, reason, score(0-1)。",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(prompt_payload, ensure_ascii=False),
                    },
                ],
                temperature=0.2,
            )
            text = getattr(resp, "output_text", "") or ""
            if not text.strip():
                return fallback

            parsed = json.loads(text)
            by_id = {item.appid: item for item in fallback}
            reranked: list[RecommendationItem] = []
            for row in parsed:
                appid = int(row.get("appid"))
                if appid not in by_id:
                    continue
                base = by_id[appid]
                reranked.append(
                    RecommendationItem(
                        appid=appid,
                        name=base.name,
                        reason=(row.get("reason") or base.reason)[:80],
                        steam_price=base.steam_price,
                        steam_review=base.steam_review,
                        score=float(row.get("score") or base.score),
                    )
                )
                if len(reranked) >= top_k:
                    break
            return reranked or fallback
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM rerank failed, fallback to rule ranking: %s", exc)
            return fallback

    def _fallback(self, seed: GameSnapshot, candidates: list[CandidateGame], top_k: int) -> list[RecommendationItem]:
        output = []
        seed_genres = set(seed.genres)
        for item in sorted(candidates, key=lambda x: x.score, reverse=True)[:top_k]:
            overlap = seed_genres.intersection(set(item.genres))
            if overlap:
                reason = f"同类型({','.join(list(overlap)[:2])})，折扣{item.steam_price.discount_percent}%"
            else:
                reason = f"评价与折扣综合较高，折扣{item.steam_price.discount_percent}%"
            output.append(
                RecommendationItem(
                    appid=item.appid,
                    name=item.name,
                    reason=reason,
                    steam_price=item.steam_price,
                    steam_review=item.steam_review,
                    score=item.score,
                )
            )
        return output
