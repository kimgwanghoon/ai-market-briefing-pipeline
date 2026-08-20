import unittest
from unittest.mock import Mock, patch

from requests import HTTPError

from discord_notifications import (
    build_daily_embed,
    build_intraday_embed,
    build_weekly_embed,
    post_discord_webhook,
)


def metric(price="100.00", change="▲ 1.00 (+1.00%)"):
    return {"price": price, "change": change}


class DiscordEmbedTests(unittest.TestCase):
    def test_daily_embed_groups_markets_summaries_and_news(self):
        indexes = {
            key: metric()
            for key in ("kospi", "kosdaq", "ewy", "sp500", "dow", "nasdaq", "vix", "usdkrw", "us10y", "wti")
        }
        embed = build_daily_embed(
            "Morning Briefing: 간밤의 미장 & 국장 프리뷰",
            "2026-08-20 08:00:00",
            "코스피·나스닥 동반 강세",
            ["[한국 시장]", "**KOSPI** 확인", "[미국 시장]", "**NASDAQ** 확인"],
            indexes,
            [{"title": "반도체 수주", "link": "https://example.com/news", "press": "테스트경제"}],
            "https://example.github.io/report",
        )

        fields = {field["name"]: field["value"] for field in embed["fields"]}
        self.assertIn("KOSPI", fields["🇰🇷 국내 시장"])
        self.assertIn("NASDAQ 확인", fields["미국 핵심 요약"])
        self.assertIn("[반도체 수주](https://example.com/news)", fields["📰 주요 뉴스"])
        self.assertEqual(embed["url"], "https://example.github.io/report")

    def test_intraday_embed_shows_delta_reliability_and_new_events(self):
        payload = {
            "timestamp": "2026-08-20 12:30:00",
            "execution": {"actual_timestamp_kst": "2026-08-20 12:30:02"},
            "sentiment": {"label": "우호", "score": 64, "data_completeness": 91},
            "comparison": {"previous_score": 59, "delta": 5},
            "reliability": {"status": "방향성 참고 제한", "reference_limited": True},
            "market_signals": {
                key: metric()
                for key in ("kospi", "kosdaq", "nasdaq", "vix", "usdkrw")
            },
            "key_points": ["**시장 점수**가 개선됐습니다."],
            "watchpoint": "**VIX** 반등 여부를 확인하세요.",
            "events": {
                "news_count": 1,
                "dart_count": 1,
                "news": [
                    {
                        "title": "AI 수요 확대",
                        "url": "https://example.com/ai",
                        "impact_score": 4,
                    }
                ],
                "dart": [
                    {
                        "corp_name": "테스트전자",
                        "title": "공급계약체결",
                        "url": "https://example.com/dart",
                        "impact_score": 3,
                    }
                ],
            },
        }

        embed = build_intraday_embed(payload, "https://example.github.io/report/")
        fields = {field["name"]: field["value"] for field in embed["fields"]}

        self.assertIn("직전 대비 +5.0p", embed["description"])
        self.assertIn("방향성은 참고용", embed["description"])
        self.assertIn("AI 수요 확대", fields["🗞️ 이번 실행 이벤트 · 뉴스 1 / 공시 1"])
        self.assertEqual(embed["url"], "https://example.github.io/report/live.html")

    def test_weekly_embed_exposes_scenario_conditions(self):
        payload = {
            "title": "주간 시장 리포트 | 2026-08-17 ~ 2026-08-21",
            "summary": {
                "score_avg": 62,
                "label": "우호",
                "score_start": 55,
                "score_end": 65,
                "score_change_text": "+10.0p",
                "trading_days": 5,
                "count": 35,
                "top_watchpoint": "**VIX** 안정 여부",
            },
            "market_performance": [{"label": "KOSPI", "change_text": "+2.10%"}],
            "opportunity_events": [{"title": "대형 수주", "impact_score": 5, "url": "https://example.com/good"}],
            "risk_events": [{"title": "환율 상승", "impact_score": -4, "url": "https://example.com/risk"}],
            "next_week_outlook": {
                "bias": "우호 흐름 우세",
                "expected_range": "58.0~72.0점",
                "confidence": "보통",
                "upside_conditions": ["VIX 안정"],
                "downside_conditions": ["달러원 상승"],
            },
        }

        embed = build_weekly_embed(payload, "https://example.github.io/report")
        fields = {field["name"]: field["value"] for field in embed["fields"]}

        self.assertIn("58.0~72.0점", fields["🔭 다음 주 조건부 전망"])
        self.assertIn("대형 수주", fields["🔴 주요 기회"])
        self.assertIn("환율 상승", fields["🔵 주요 위험"])
        self.assertEqual(embed["url"], "https://example.github.io/report/weekly.html")

    def test_field_values_are_truncated_to_discord_limit(self):
        indexes = {
            key: metric()
            for key in ("kospi", "kosdaq", "ewy", "sp500", "dow", "nasdaq", "vix", "usdkrw", "us10y", "wti")
        }
        embed = build_daily_embed(
            "Morning Briefing",
            "2026-08-20 08:00:00",
            "테스트",
            ["[한국 시장]", "가" * 3000, "[미국 시장]", "나" * 3000],
            indexes,
            [],
            "https://example.com",
        )
        self.assertTrue(all(len(field["value"]) <= 1024 for field in embed["fields"]))


class DiscordDeliveryTests(unittest.TestCase):
    @patch("discord_notifications.time.sleep")
    @patch("discord_notifications.requests.post")
    def test_retries_rate_limit_and_disables_mentions(self, post, sleep):
        limited = Mock(status_code=429)
        limited.json.return_value = {"retry_after": 0.1}
        success = Mock(status_code=204)
        post.side_effect = [limited, success]

        delivered = post_discord_webhook("https://discord.example/webhook", {"title": "@everyone 테스트"})

        self.assertTrue(delivered)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs["json"]["allowed_mentions"], {"parse": []})
        sleep.assert_called_once_with(0.1)

    @patch("discord_notifications.time.sleep")
    @patch("discord_notifications.requests.post")
    def test_does_not_retry_permanent_client_error(self, post, sleep):
        rejected = Mock(status_code=401)
        rejected.raise_for_status.side_effect = HTTPError("unauthorized")
        post.return_value = rejected

        delivered = post_discord_webhook("https://discord.example/webhook", {"title": "테스트"})

        self.assertFalse(delivered)
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
