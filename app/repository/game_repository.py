from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app import models
from app.schemas import DailyDiscountItem, GameSnapshot, PriceInfo, ReviewInfo


@dataclass
class GameCandidate:
    appid: int
    name: str


class GameRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_game(self, appid: int, name: str, genres: list[str], tags: list[str], aliases: Iterable[str] | None = None) -> None:
        game = self.session.execute(select(models.Game).where(models.Game.appid == appid)).scalar_one_or_none()
        if game is None:
            game = models.Game(appid=appid, name=name, genres=genres, tags=tags)
            self.session.add(game)
        else:
            game.name = name
            game.genres = genres
            game.tags = tags

        alias_items = {name.strip()}
        if aliases:
            alias_items.update(item.strip() for item in aliases if item and item.strip())

        for alias in alias_items:
            exists = self.session.execute(
                select(models.GameAlias).where(and_(models.GameAlias.appid == appid, models.GameAlias.alias == alias))
            ).scalar_one_or_none()
            if exists is None:
                self.session.add(models.GameAlias(appid=appid, alias=alias))

    def save_price_snapshot(
        self,
        appid: int,
        source: str,
        currency: str,
        original_price: int | None,
        final_price: int | None,
        discount_percent: int,
        popularity_rank: int | None = None,
    ) -> None:
        self.session.add(
            models.PriceSnapshot(
                appid=appid,
                source=source,
                currency=currency,
                original_price=original_price,
                final_price=final_price,
                discount_percent=discount_percent,
                popularity_rank=popularity_rank,
                captured_at=datetime.utcnow(),
            )
        )

    def save_review_snapshot(
        self,
        appid: int,
        source: str,
        recent_summary: str | None,
        recent_percent: int | None,
        recent_total: int | None,
        overall_summary: str | None,
        overall_percent: int | None,
        overall_total: int | None,
    ) -> None:
        self.session.add(
            models.ReviewSnapshot(
                appid=appid,
                source=source,
                recent_summary=recent_summary,
                recent_percent=recent_percent,
                recent_total=recent_total,
                overall_summary=overall_summary,
                overall_percent=overall_percent,
                overall_total=overall_total,
                captured_at=datetime.utcnow(),
            )
        )

    def save_source_comparison(
        self,
        appid: int,
        steam_final_price: int | None,
        xhh_final_price: int | None,
        steam_overall_percent: int | None,
        xhh_overall_percent: int | None,
    ) -> None:
        flags: dict[str, bool] = {
            "price_diff": steam_final_price is not None and xhh_final_price is not None and steam_final_price != xhh_final_price,
            "review_diff": (
                steam_overall_percent is not None
                and xhh_overall_percent is not None
                and abs(steam_overall_percent - xhh_overall_percent) >= 5
            ),
        }
        self.session.add(
            models.SourceComparison(
                appid=appid,
                steam_final_price=steam_final_price,
                xhh_final_price=xhh_final_price,
                steam_overall_percent=steam_overall_percent,
                xhh_overall_percent=xhh_overall_percent,
                diff_flags=flags,
                captured_at=datetime.utcnow(),
            )
        )

    def log_message(self, session_id: str, user_id: str, request_text: str, response_text: str) -> None:
        self.session.add(
            models.MessageLog(
                session_id=session_id,
                user_id=user_id,
                request_text=request_text,
                response_text=response_text,
                created_at=datetime.utcnow(),
            )
        )

    def get_game_by_appid(self, appid: int) -> models.Game | None:
        return self.session.execute(select(models.Game).where(models.Game.appid == appid)).scalar_one_or_none()

    def search_games(self, query: str, limit: int = 5) -> list[GameCandidate]:
        q = query.strip()
        if not q:
            return []

        pattern = f"%{q}%"
        name_rows = self.session.execute(select(models.Game).where(models.Game.name.ilike(pattern)).limit(limit * 2)).scalars().all()

        alias_appids = self.session.execute(
            select(models.GameAlias.appid).where(models.GameAlias.alias.ilike(pattern)).limit(limit * 2)
        ).scalars().all()
        alias_rows = []
        if alias_appids:
            alias_rows = self.session.execute(select(models.Game).where(models.Game.appid.in_(alias_appids))).scalars().all()

        merged: dict[int, models.Game] = {}
        for row in name_rows + alias_rows:
            merged[row.appid] = row

        ranked = sorted(
            merged.values(),
            key=lambda row: (
                0 if row.name.lower() == q.lower() else 1,
                0 if row.name.lower().startswith(q.lower()) else 1,
                len(row.name),
            ),
        )
        return [GameCandidate(appid=row.appid, name=row.name) for row in ranked[:limit]]

    def _latest_price(self, appid: int, source: str) -> models.PriceSnapshot | None:
        return self.session.execute(
            select(models.PriceSnapshot)
            .where(and_(models.PriceSnapshot.appid == appid, models.PriceSnapshot.source == source))
            .order_by(models.PriceSnapshot.captured_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _latest_review(self, appid: int, source: str) -> models.ReviewSnapshot | None:
        return self.session.execute(
            select(models.ReviewSnapshot)
            .where(and_(models.ReviewSnapshot.appid == appid, models.ReviewSnapshot.source == source))
            .order_by(models.ReviewSnapshot.captured_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def get_game_snapshot(self, appid: int) -> GameSnapshot | None:
        game = self.get_game_by_appid(appid)
        if game is None:
            return None

        steam_price = self._latest_price(appid, "steam")
        steam_review = self._latest_review(appid, "steam")
        if steam_price is None or steam_review is None:
            return None

        xhh_price = self._latest_price(appid, "xhh")
        xhh_review = self._latest_review(appid, "xhh")

        return GameSnapshot(
            appid=appid,
            name=game.name,
            genres=game.genres or [],
            tags=game.tags or [],
            steam_price=PriceInfo(
                currency=steam_price.currency,
                original_price=steam_price.original_price,
                final_price=steam_price.final_price,
                discount_percent=steam_price.discount_percent,
            ),
            steam_review=ReviewInfo(
                recent_summary=steam_review.recent_summary,
                recent_percent=steam_review.recent_percent,
                recent_total=steam_review.recent_total,
                overall_summary=steam_review.overall_summary,
                overall_percent=steam_review.overall_percent,
                overall_total=steam_review.overall_total,
            ),
            xhh_price=(
                PriceInfo(
                    currency=xhh_price.currency,
                    original_price=xhh_price.original_price,
                    final_price=xhh_price.final_price,
                    discount_percent=xhh_price.discount_percent,
                )
                if xhh_price
                else None
            ),
            xhh_review=(
                ReviewInfo(
                    recent_summary=xhh_review.recent_summary,
                    recent_percent=xhh_review.recent_percent,
                    recent_total=xhh_review.recent_total,
                    overall_summary=xhh_review.overall_summary,
                    overall_percent=xhh_review.overall_percent,
                    overall_total=xhh_review.overall_total,
                )
                if xhh_review
                else None
            ),
            popularity_rank=steam_price.popularity_rank,
            captured_at=steam_price.captured_at,
        )

    def list_hot_discounts(self, limit: int, max_age_minutes: int = 240) -> list[DailyDiscountItem]:
        threshold = datetime.utcnow() - timedelta(minutes=max_age_minutes)

        latest_subq = (
            select(models.PriceSnapshot.appid, func.max(models.PriceSnapshot.captured_at).label("max_captured"))
            .where(and_(models.PriceSnapshot.source == "steam", models.PriceSnapshot.captured_at >= threshold))
            .group_by(models.PriceSnapshot.appid)
            .subquery()
        )

        rows = self.session.execute(
            select(models.PriceSnapshot)
            .join(
                latest_subq,
                and_(
                    models.PriceSnapshot.appid == latest_subq.c.appid,
                    models.PriceSnapshot.captured_at == latest_subq.c.max_captured,
                ),
            )
            .where(and_(models.PriceSnapshot.source == "steam", models.PriceSnapshot.discount_percent > 0))
            .order_by(models.PriceSnapshot.popularity_rank.asc().nullslast(), models.PriceSnapshot.discount_percent.desc())
            .limit(limit)
        ).scalars().all()

        output: list[DailyDiscountItem] = []
        for row in rows:
            snapshot = self.get_game_snapshot(row.appid)
            if snapshot is None:
                continue
            output.append(
                DailyDiscountItem(
                    appid=snapshot.appid,
                    name=snapshot.name,
                    genres=snapshot.genres,
                    steam_price=snapshot.steam_price,
                    steam_review=snapshot.steam_review,
                    xhh_price=snapshot.xhh_price,
                    xhh_review=snapshot.xhh_review,
                    popularity_rank=snapshot.popularity_rank,
                    captured_at=snapshot.captured_at,
                )
            )
        return output

    def list_discounted_candidates(self, exclude_appid: int, max_count: int = 200) -> list[GameSnapshot]:
        latest_subq = (
            select(models.PriceSnapshot.appid, func.max(models.PriceSnapshot.captured_at).label("max_captured"))
            .where(models.PriceSnapshot.source == "steam")
            .group_by(models.PriceSnapshot.appid)
            .subquery()
        )

        rows = self.session.execute(
            select(models.PriceSnapshot.appid)
            .join(
                latest_subq,
                and_(
                    models.PriceSnapshot.appid == latest_subq.c.appid,
                    models.PriceSnapshot.captured_at == latest_subq.c.max_captured,
                ),
            )
            .where(
                and_(
                    models.PriceSnapshot.source == "steam",
                    models.PriceSnapshot.discount_percent > 0,
                    models.PriceSnapshot.appid != exclude_appid,
                )
            )
            .order_by(models.PriceSnapshot.popularity_rank.asc().nullslast(), models.PriceSnapshot.discount_percent.desc())
            .limit(max_count)
        ).scalars().all()

        output = []
        for appid in rows:
            snapshot = self.get_game_snapshot(appid)
            if snapshot:
                output.append(snapshot)
        return output
