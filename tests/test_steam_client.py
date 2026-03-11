from __future__ import annotations

import respx
from httpx import Response

from app.clients.steam_client import SteamClient
from app.config import Settings


def test_top_seller_discounts_filters_non_discounted():
    settings = Settings(steam_base_url="https://store.steampowered.com")
    client = SteamClient(settings)

    with respx.mock(assert_all_called=True) as router:
        router.get("https://store.steampowered.com/api/featuredcategories").mock(
            return_value=Response(
                200,
                json={
                    "top_sellers": {
                        "items": [
                            {"id": 1, "name": "A", "discount_percent": 0, "original_price": 1000, "final_price": 1000},
                            {"id": 2, "name": "B", "discount_percent": 30, "original_price": 2000, "final_price": 1400},
                        ]
                    }
                },
            )
        )
        rows = client.get_top_seller_discounts(limit=10)

    assert len(rows) == 1
    assert rows[0]["appid"] == 2
    assert rows[0]["discount_percent"] == 30


def test_review_summary_maps_recent_and_overall():
    settings = Settings(steam_base_url="https://store.steampowered.com")
    client = SteamClient(settings)

    with respx.mock(assert_all_called=True) as router:
        def handler(request):
            review_filter = request.url.params.get("filter")
            if review_filter == "recent":
                return Response(
                    200,
                    json={
                        "query_summary": {
                            "review_score_desc": "多半好评",
                            "total_reviews": 20,
                            "total_positive": 16,
                        }
                    },
                )
            return Response(
                200,
                json={
                    "query_summary": {
                        "review_score_desc": "特别好评",
                        "total_reviews": 100,
                        "total_positive": 90,
                    }
                },
            )

        router.get("https://store.steampowered.com/appreviews/100").mock(side_effect=handler)
        summary = client.get_review_summary(100)

    assert summary["overall_summary"] == "特别好评"
    assert summary["overall_percent"] == 90
    assert summary["recent_summary"] == "多半好评"
    assert summary["recent_percent"] == 80
