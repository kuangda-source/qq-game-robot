from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from app.cache import CacheBackend
from app.clients.qq_api import QQApiClient
from app.config import Settings
from app.database import db_session
from app.repository.game_repository import GameRepository
from app.schemas import QQEventMessage
from app.services.game_service import GameService


class QQAdapter:
    def __init__(
        self,
        settings: Settings,
        service: GameService,
        qq_client: QQApiClient,
        cache: CacheBackend,
        session_factory: Callable = db_session,
    ):
        self.settings = settings
        self.service = service
        self.qq_client = qq_client
        self.cache = cache
        self.session_factory = session_factory

    def on_mention_query(self, event: QQEventMessage) -> str:
        clean = self._strip_mentions(event.content)

        pending_key = self._pending_key(event.session_id, event.user_id)
        pending = self.cache.get_json(pending_key)
        if pending and clean.strip().isdigit():
            return self.on_disambiguation_reply(event, int(clean.strip()))

        if "今日折扣" in clean:
            items = self.service.get_daily_hot_discounts(limit=self.settings.hot_default_limit)
            response = self._format_daily_discounts(items)
            self._log(event=event, response=response)
            return response

        if clean.startswith("查游戏"):
            keyword = clean.replace("查游戏", "", 1).strip()
            result = self.service.query_game_snapshot(keyword)
            response = self._render_query_result(event, result)
            self._log(event=event, response=response)
            return response

        match = re.search(r"推荐\s*和(.+?)类似且在打折", clean)
        if match:
            seed = match.group(1).strip()
            try:
                items = self.service.recommend_similar_discounted(seed_game=seed, top_k=5)
                response = self._format_recommendations(seed, items)
            except Exception as exc:  # noqa: BLE001
                response = f"推荐生成失败：{exc}"
            self._log(event=event, response=response)
            return response

        response = (
            "可用指令：\n"
            "1) @机器人 今日折扣\n"
            "2) @机器人 查游戏 <游戏名>\n"
            "3) @机器人 推荐 和<游戏名>类似且在打折"
        )
        self._log(event=event, response=response)
        return response

    def on_disambiguation_reply(self, event: QQEventMessage, index: int) -> str:
        key = self._pending_key(event.session_id, event.user_id)
        pending = self.cache.get_json(key)
        if not pending:
            return "当前没有待选择的候选，请重新发送“查游戏 游戏名”"

        options = pending.get("options") or []
        if index < 1 or index > len(options):
            return f"序号无效，请输入 1-{len(options)}"

        selected = options[index - 1]
        result = self.service.query_game_snapshot(str(selected["appid"]))
        self.cache.delete(key)
        response = self._render_query_result(event, result)
        self._log(event=event, response=response)
        return response

    def on_daily_push(self, channel_id: str) -> bool:
        items = self.service.get_daily_hot_discounts(limit=self.settings.hot_default_limit)
        message = self._format_daily_discounts(items)
        return self.qq_client.send_message(channel_id=channel_id, content=message)

    def _render_query_result(self, event: QQEventMessage, result) -> str:
        if result.status == "not_found":
            return result.message or "未找到匹配游戏"

        if result.status == "ambiguous":
            candidates = result.candidates[:3]
            lines = ["匹配到多个游戏，请回复序号："]
            for idx, item in enumerate(candidates, start=1):
                lines.append(f"{idx}. {item['name']} (appid: {item['appid']})")
            self.cache.set_json(
                self._pending_key(event.session_id, event.user_id),
                {"options": candidates, "created_at": datetime.utcnow().isoformat()},
                ttl_seconds=self.settings.disambiguation_ttl_seconds,
            )
            return "\n".join(lines)

        return self._format_game_snapshot(result.game)

    def _strip_mentions(self, content: str) -> str:
        cleaned = re.sub(r"<@!?\d+>", "", content)
        cleaned = re.sub(rf"@?{re.escape(self.settings.qq_bot_name)}", "", cleaned, count=1)
        return cleaned.strip()

    @staticmethod
    def _format_money(cents: int | None) -> str:
        if cents is None:
            return "N/A"
        return f"¥{cents / 100:.2f}"

    def _format_game_snapshot(self, game) -> str:
        steam_price = (
            f"Steam 价格: {self._format_money(game.steam_price.final_price)}"
            f" (原价 {self._format_money(game.steam_price.original_price)}, -{game.steam_price.discount_percent}%)"
        )
        steam_review = (
            f"Steam 评价: 近期 {game.steam_review.recent_summary or 'N/A'}"
            f"({game.steam_review.recent_percent or 'N/A'}%), "
            f"总体 {game.steam_review.overall_summary or 'N/A'}({game.steam_review.overall_percent or 'N/A'}%)"
        )

        xhh_section = "小黑盒数据暂不可用（自动降级为 Steam）"
        if game.xhh_price or game.xhh_review:
            xhh_price = game.xhh_price
            xhh_review = game.xhh_review
            xhh_section = (
                f"小黑盒 价格: {self._format_money(xhh_price.final_price if xhh_price else None)}"
                f" (原价 {self._format_money(xhh_price.original_price if xhh_price else None)}, "
                f"-{xhh_price.discount_percent if xhh_price else 0}%)\n"
                f"小黑盒 评价: {xhh_review.overall_summary if xhh_review else 'N/A'}"
                f"({xhh_review.overall_percent if xhh_review else 'N/A'}%)"
            )

        genres = ", ".join(game.genres[:4]) if game.genres else "未知"
        timestamp = game.captured_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        return (
            f"{game.name} (appid: {game.appid})\n"
            f"类型: {genres}\n"
            f"{steam_price}\n"
            f"{steam_review}\n"
            f"{xhh_section}\n"
            f"数据时间: {timestamp}"
        )

    def _format_daily_discounts(self, items) -> str:
        if not items:
            return "今天暂无热门折扣数据，稍后再试。"

        lines = ["今日热门折扣（Steam主数据 + 小黑盒补充）"]
        for idx, item in enumerate(items, start=1):
            rank = f"#{item.popularity_rank}" if item.popularity_rank else f"#{idx}"
            xhh_price = self._format_money(item.xhh_price.final_price) if item.xhh_price else "N/A"
            xhh_review = f"{item.xhh_review.overall_percent}%" if item.xhh_review and item.xhh_review.overall_percent else "N/A"
            lines.append(
                f"{rank} {item.name} | Steam {self._format_money(item.steam_price.final_price)} "
                f"(-{item.steam_price.discount_percent}%) | 近期/总体: "
                f"{item.steam_review.recent_percent or 'N/A'}%/{item.steam_review.overall_percent or 'N/A'}% | "
                f"小黑盒 价: {xhh_price} 评: {xhh_review}"
            )
        lines.append("注: 若小黑盒异常会自动降级，仅显示 Steam 数据")
        return "\n".join(lines)

    @staticmethod
    def _format_recommendations(seed: str, items: list[dict]) -> str:
        if not items:
            return f"未找到与“{seed}”相似且在打折的游戏"

        lines = [f"与“{seed}”相似且在打折的推荐："]
        for idx, item in enumerate(items, start=1):
            price = item["final_price"]
            price_text = f"¥{price / 100:.2f}" if price is not None else "N/A"
            lines.append(
                f"{idx}. {item['name']} (appid:{item['appid']}) | {price_text} -{item['discount_percent']}% | "
                f"总体{item.get('overall_percent') or 'N/A'}% | {item['reason']}"
            )
        return "\n".join(lines)

    @staticmethod
    def _pending_key(session_id: str, user_id: str) -> str:
        return f"disamb:{session_id}:{user_id}"

    def _log(self, event: QQEventMessage, response: str) -> None:
        with self.session_factory() as session:
            repo = GameRepository(session)
            repo.log_message(
                session_id=event.session_id,
                user_id=event.user_id,
                request_text=event.content,
                response_text=response,
            )
