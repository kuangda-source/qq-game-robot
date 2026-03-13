from __future__ import annotations

from app.clients.qq_api import QQApiClient
from app.config import Settings
from app.exceptions import DataSourceUnavailable
from app.qq_adapter import QQAdapter
from app.schemas import QQEventMessage
from app.services.game_service import GameService


class FakeSteamClient:
    def get_top_seller_discounts(self, limit: int, region: str = "cn", currency: str = "CNY"):
        return [
            {
                "appid": 100,
                "name": "黑神话：悟空",
                "original_price": 29800,
                "final_price": 23840,
                "discount_percent": 20,
                "currency": currency,
                "popularity_rank": 1,
            },
            {
                "appid": 101,
                "name": "黑神话：悟空 Demo",
                "original_price": 9800,
                "final_price": 4900,
                "discount_percent": 50,
                "currency": currency,
                "popularity_rank": 2,
            },
            {
                "appid": 200,
                "name": "卧龙",
                "original_price": 29900,
                "final_price": 14950,
                "discount_percent": 50,
                "currency": currency,
                "popularity_rank": 3,
            },
            {
                "appid": 300,
                "name": "Battlefield 1",
                "original_price": 19800,
                "final_price": 990,
                "discount_percent": 95,
                "currency": currency,
                "popularity_rank": 4,
            },
            {
                "appid": 301,
                "name": "Party Animals",
                "original_price": 9800,
                "final_price": 6860,
                "discount_percent": 30,
                "currency": currency,
                "popularity_rank": 5,
            },
        ]

    def get_app_details(self, appid: int, region: str = "cn"):
        mapping = {
            100: {"name": "黑神话：悟空", "genres": ["动作"], "tags": ["动作", "类魂", "单人"], "currency": "CNY", "original_price": 29800, "final_price": 23840, "discount_percent": 20, "aliases": ["黑神话"]},
            101: {"name": "黑神话：悟空 Demo", "genres": ["动作"], "tags": ["动作"], "currency": "CNY", "original_price": 9800, "final_price": 4900, "discount_percent": 50, "aliases": ["黑神话 demo"]},
            200: {"name": "卧龙", "genres": ["动作", "角色扮演"], "tags": ["动作", "硬核"], "currency": "CNY", "original_price": 29900, "final_price": 14950, "discount_percent": 50, "aliases": ["卧龙苍天陨落"]},
            300: {"name": "Battlefield 1", "genres": ["动作", "射击"], "tags": ["动作", "FPS", "多人", "在线合作"], "currency": "CNY", "original_price": 19800, "final_price": 990, "discount_percent": 95, "aliases": ["战地1"]},
            301: {"name": "Party Animals", "genres": ["动作", "休闲"], "tags": ["派对", "多人", "联机"], "currency": "CNY", "original_price": 9800, "final_price": 6860, "discount_percent": 30, "aliases": ["动物派对"]},
        }
        return {"appid": appid, **mapping[appid]}

    def get_review_summary(self, appid: int):
        mapping = {
            100: {"recent_summary": "特别好评", "recent_percent": 88, "recent_total": 500, "overall_summary": "特别好评", "overall_percent": 90, "overall_total": 5000},
            101: {"recent_summary": "褒贬不一", "recent_percent": 65, "recent_total": 90, "overall_summary": "多半好评", "overall_percent": 72, "overall_total": 800},
            200: {"recent_summary": "特别好评", "recent_percent": 91, "recent_total": 200, "overall_summary": "特别好评", "overall_percent": 89, "overall_total": 2600},
            300: {"recent_summary": "特别好评", "recent_percent": 88, "recent_total": 320, "overall_summary": "特别好评", "overall_percent": 85, "overall_total": 6800},
            301: {"recent_summary": "特别好评", "recent_percent": 91, "recent_total": 180, "overall_summary": "特别好评", "overall_percent": 90, "overall_total": 4700},
        }
        return mapping[appid]

    def search_apps(self, keyword: str, limit: int = 5, region: str = "cn"):
        if "黑神话" in keyword:
            return [{"appid": 100, "name": "黑神话：悟空"}]
        if "卧龙" in keyword:
            return [{"appid": 200, "name": "卧龙"}]
        return []

    def get_similar_appids(self, appid: int, region: str = "cn", limit: int = 30):
        if appid == 100:
            return [200]
        return []


class FakeXHHSpider:
    def fetch_game_snapshot(self, appid: int, steam_name: str | None = None):
        if appid == 101:
            raise DataSourceUnavailable("blocked")
        return {
            "currency": "CNY",
            "final_price": 23000 if appid == 100 else 14500,
            "original_price": 29800 if appid == 100 else 29900,
            "discount_percent": 23 if appid == 100 else 52,
            "recent_summary": "特别好评",
            "recent_percent": 87,
            "recent_total": 100,
            "overall_summary": "特别好评",
            "overall_percent": 88,
            "overall_total": 1000,
        }


class FakeReranker:
    def rerank(self, seed, candidates, top_k=5):  # noqa: ANN001, ANN201
        rows = sorted(candidates, key=lambda x: x.score, reverse=True)[:top_k]
        return [
            type("Tmp", (), {
                "appid": item.appid,
                "name": item.name,
                "reason": "规则排序",
                "steam_price": item.steam_price,
                "steam_review": item.steam_review,
                "score": item.score,
            })
            for item in rows
        ]


class DummyQQClient(QQApiClient):
    def __init__(self, settings):
        self.settings = settings

    def send_message(self, scene: str, target_id: str, content: str, msg_id=None, event_id=None, msg_seq=1) -> bool:
        return True


def build_service(settings, memory_cache, session_factory):
    return GameService(
        settings=settings,
        cache=memory_cache,
        steam_client=FakeSteamClient(),
        xhh_spider=FakeXHHSpider(),
        reranker=FakeReranker(),
        session_factory=session_factory,
    )


def test_query_ambiguous_then_resolve(settings: Settings, memory_cache, session_factory):
    service = build_service(settings, memory_cache, session_factory)
    service.refresh_market_data(limit=10)

    adapter = QQAdapter(
        settings=settings,
        service=service,
        qq_client=DummyQQClient(settings),
        cache=memory_cache,
        session_factory=session_factory,
    )

    event = QQEventMessage(
        content="@机器人 查游戏 黑神话",
        channel_id="1000",
        user_id="u1",
        session_id="s1",
    )
    first = adapter.on_mention_query(event)
    assert "匹配到多个游戏" in first

    second_event = QQEventMessage(
        content="1",
        channel_id="1000",
        user_id="u1",
        session_id="s1",
    )
    second = adapter.on_mention_query(second_event)
    assert "黑神话：悟空" in second
    assert "Steam 价格" in second


def test_recommend_similar_discounted(settings: Settings, memory_cache, session_factory):
    service = build_service(settings, memory_cache, session_factory)
    service.refresh_market_data(limit=10)
    rows = service.recommend_similar_discounted("黑神话：悟空", top_k=3)
    assert rows
    assert rows[0]["appid"] == 200
    appids = {item["appid"] for item in rows}
    assert 300 not in appids
    assert 301 not in appids


def test_recommend_similar_with_ambiguous_seed(settings: Settings, memory_cache, session_factory):
    service = build_service(settings, memory_cache, session_factory)
    service.refresh_market_data(limit=10)
    rows = service.recommend_similar_discounted("黑神话", top_k=3)
    assert rows
    assert rows[0]["appid"] == 200


def test_xhh_degrade_message(settings: Settings, memory_cache, session_factory):
    service = build_service(settings, memory_cache, session_factory)
    service.refresh_market_data(limit=10)

    adapter = QQAdapter(
        settings=settings,
        service=service,
        qq_client=DummyQQClient(settings),
        cache=memory_cache,
        session_factory=session_factory,
    )

    event = QQEventMessage(
        content="@机器人 查游戏 黑神话：悟空 Demo",
        channel_id="1000",
        user_id="u1",
        session_id="s2",
    )
    text = adapter.on_mention_query(event)
    assert "自动降级" in text


def test_query_uses_store_search_fallback(settings: Settings, memory_cache, session_factory):
    service = build_service(settings, memory_cache, session_factory)
    result = service.query_game_snapshot("黑神话")
    assert result.status in {"ok", "ambiguous"}
    if result.status == "ok":
        assert result.game is not None
        assert "黑神话" in result.game.name
    else:
        assert result.candidates
