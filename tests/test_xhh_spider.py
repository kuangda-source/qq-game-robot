from __future__ import annotations

import pytest

from app.clients.xhh_spider import XiaoHeiHeSpider
from app.config import Settings
from app.exceptions import DataSourceUnavailable


class DummyResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


def test_xhh_spider_parses_public_html(monkeypatch):
    settings = Settings(xhh_game_url_template="https://example.com/{appid}")
    spider = XiaoHeiHeSpider(settings)

    def fake_get(*args, **kwargs):  # noqa: ANN002, ANN003
        html = "<html><body>好评率 88% 特别好评 现价 ￥128.00 -20%</body></html>"
        return DummyResponse(200, html)

    monkeypatch.setattr(spider.client, "get", fake_get)
    row = spider.fetch_game_snapshot(123)
    assert row["final_price"] == 12800
    assert row["discount_percent"] == 20
    assert row["overall_percent"] == 88


def test_xhh_spider_raises_when_no_public_data(monkeypatch):
    settings = Settings(xhh_game_url_template="https://example.com/{appid}")
    spider = XiaoHeiHeSpider(settings)

    def fake_get(*args, **kwargs):  # noqa: ANN002, ANN003
        html = "<html><body>empty</body></html>"
        return DummyResponse(200, html)

    monkeypatch.setattr(spider.client, "get", fake_get)
    with pytest.raises(DataSourceUnavailable):
        spider.fetch_game_snapshot(123)
