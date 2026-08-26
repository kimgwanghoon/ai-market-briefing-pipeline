import base64
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
import intraday
import weekly_report
from main import bold_filter, require_market_coverage
from intraday import (
    KST,
    build_data_quality,
    build_live_event_view,
    build_live_pulse_summary,
    build_score_comparison,
    build_top_live_events,
    compute_reliability,
    filter_unseen_events,
    find_previous_snapshot,
    has_execution_target,
    is_material_dart_event,
    raw_score_label,
)
from weekly_report import build_week_summary


def snapshot(score, *, raw_score=None):
    sentiment = {"score": score, "label": "legacy"}
    if raw_score is not None:
        sentiment["raw_score"] = raw_score
    return {
        "timestamp": "2026-08-18 09:30:00",
        "sentiment": sentiment,
        "events": {"news": [], "dart": []},
        "watchpoint": "지표 확인",
    }


class WeeklySummaryTests(unittest.TestCase):
    def test_uses_current_zero_to_one_hundred_score_scale(self):
        summary = build_week_summary([snapshot(65, raw_score=30), snapshot(75, raw_score=50)])
        self.assertEqual(summary["score_avg"], 70)
        self.assertEqual(summary["label"], "우호")

    def test_normalizes_legacy_minus_one_hundred_to_one_hundred_scale(self):
        summary = build_week_summary([snapshot(-40), snapshot(0)])
        self.assertEqual(summary["score_avg"], 40)
        self.assertEqual(summary["label"], "중립")

    def test_separates_downside_risk_from_positive_opportunity(self):
        item = snapshot(50, raw_score=0)
        item["events"] = {
            "news": [
                {"title": "대형 수주", "impact_score": 5},
                {"title": "실적 악화", "impact_score": -3},
            ],
            "dart": [],
        }
        summary = build_week_summary([item])
        self.assertEqual(summary["top_risk"], "실적 악화")
        self.assertEqual(summary["top_opportunity"], "대형 수주")

    def test_weights_each_trading_day_equally(self):
        snapshots = []
        for minute in (10, 20, 30):
            item = snapshot(80, raw_score=45)
            item["timestamp"] = f"2026-08-17 09:{minute:02d}:00"
            snapshots.append(item)
        second_day = snapshot(20, raw_score=-45)
        second_day["timestamp"] = "2026-08-18 09:10:00"
        snapshots.append(second_day)

        summary = build_week_summary(snapshots)

        self.assertEqual(summary["score_avg"], 50.0)
        self.assertEqual(summary["trading_days"], 2)
        self.assertEqual(summary["count"], 4)

    def test_builds_grounded_next_week_outlook(self):
        first = snapshot(48, raw_score=-3)
        first["timestamp"] = "2026-08-17 09:10:00"
        last = snapshot(58, raw_score=12)
        last["timestamp"] = "2026-08-21 15:30:00"

        summary = build_week_summary([first, last])
        outlook = summary["next_week_outlook"]

        self.assertIn("점", outlook["expected_range"])
        self.assertEqual(outlook["confidence"], "낮음")
        self.assertIn("과거 주간 스냅샷", outlook["disclaimer"])

    def test_weekly_html_renders_daily_flow_and_outlook_last(self):
        first = snapshot(48, raw_score=-3)
        first["timestamp"] = "2026-08-17 09:10:00"
        last = snapshot(58, raw_score=12)
        last["timestamp"] = "2026-08-21 15:30:00"
        last["watchpoint"] = "**VIX** 흐름을 확인하세요."
        summary = build_week_summary([first, last])
        payload = {
            "title": "주간 시장 리포트 | 테스트",
            "generated_at": "2026-08-22 09:00:00",
            "summary": summary,
            "daily_points": summary["daily_points"],
            "market_performance": summary["market_performance"],
            "risk_events": summary["risk_events"],
            "opportunity_events": summary["opportunity_events"],
            "next_week_outlook": summary["next_week_outlook"],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(weekly_report, "OUTPUT_DIR", Path(tmp_dir)):
                weekly_report.render_weekly_html(payload)
            html = (Path(tmp_dir) / "weekly.html").read_text(encoding="utf-8")

        self.assertIn("일별 센티먼트 흐름", html)
        self.assertIn("다음 주 조건부 전망", html)
        self.assertIn("<strong>VIX</strong>", html)
        self.assertLess(html.index("주요 이벤트"), html.index("다음 주 조건부 전망"))


class SentimentContractTests(unittest.TestCase):
    def test_raw_labels_match_display_score_boundaries(self):
        self.assertEqual(raw_score_label(20), "bullish")
        self.assertEqual(raw_score_label(19.9), "neutral")
        self.assertEqual(raw_score_label(-20), "bearish")

    def test_missing_data_has_zero_quality_score(self):
        quality = build_data_quality({}, [], [], {"scores": []})
        self.assertEqual(quality["score"], 0)

    def test_reliability_uses_forward_index_level_return(self):
        history = []
        for hour, price in enumerate([100, 100.2, 100.4, 100.6, 100.8, 101.0], start=9):
            history.append(
                {
                    "timestamp": f"2026-08-18 {hour:02d}:30:00",
                    "sentiment": {"raw_label": "bullish"},
                    "market_signals": {
                        "kospi": {"price": str(price)},
                        "kosdaq": {"price": str(price)},
                    },
                }
            )
        reliability = compute_reliability(history)
        self.assertEqual(reliability["evaluated"], 5)
        self.assertEqual(reliability["hit_rate"], "100.0%")
        self.assertEqual(reliability["status"], "검증 표본 부족")

    def test_low_directional_hit_rate_is_marked_as_limited(self):
        history = []
        for day in range(18, 23):
            for offset, hour in enumerate(range(9, 15)):
                price = 100 - day - offset * 0.3
                history.append(
                    {
                        "timestamp": f"2026-08-{day:02d} {hour:02d}:30:00",
                        "sentiment": {"raw_label": "bullish"},
                        "market_signals": {
                            "kospi": {"price": str(price)},
                            "kosdaq": {"price": str(price)},
                        },
                    }
                )

        reliability = compute_reliability(history)

        self.assertEqual(reliability["evaluated"], 25)
        self.assertEqual(reliability["hit_rate"], "0.0%")
        self.assertTrue(reliability["reference_limited"])
        self.assertEqual(reliability["status"], "방향성 참고 제한")

    def test_previous_snapshot_does_not_substitute_latest_wrong_slot(self):
        current = KST.localize(datetime(2026, 8, 18, 10, 30))
        history = [{"timestamp": "2026-08-18 09:30:00"}]
        self.assertIsNone(find_previous_snapshot(current, history))

    def test_repeated_events_are_not_scored_in_later_snapshots(self):
        events = [
            {"event_id": "news-1", "title": "기존", "published_at": "2026-08-18 10:05"},
            {"event_id": "news-2", "title": "신규", "published_at": "2026-08-18 10:10"},
            {"event_id": "news-2", "title": "중복", "published_at": "2026-08-18 10:10"},
        ]
        history = [{"events": {"news": [{"event_id": "news-1"}]}}]
        now = KST.localize(datetime(2026, 8, 18, 10, 30))
        self.assertEqual(
            [item["event_id"] for item in filter_unseen_events(events, history, "news", now)],
            ["news-2"],
        )

    def test_news_outside_current_observation_hour_is_excluded(self):
        events = [
            {"event_id": "old", "published_at": "2026-08-18 09:59"},
            {"event_id": "current", "published_at": "2026-08-18 10:01"},
        ]
        now = KST.localize(datetime(2026, 8, 18, 10, 30))
        self.assertEqual(
            [item["event_id"] for item in filter_unseen_events(events, [], "news", now)],
            ["current"],
        )

    def test_score_comparison_uses_latest_actual_execution(self):
        history = [
            {
                "timestamp": "2026-08-18 09:47:00",
                "sentiment": {"score": 62, "raw_score": 24},
            },
            {
                "timestamp": "2026-08-18 10:12:00",
                "sentiment": {"score": 58, "raw_score": 16},
            },
        ]
        current = {
            "timestamp": "2026-08-18 10:49:00",
            "sentiment": {"score": 70, "raw_score": 40},
        }

        result = build_score_comparison(current, history)

        self.assertEqual(result["previous_timestamp"], "2026-08-18 10:12:00")
        self.assertEqual(result["previous_score"], 58)
        self.assertEqual(result["delta"], 12)

    def test_live_pulse_exposes_latest_movement_and_top_driver(self):
        payload = {
            "sentiment": {
                "score": 62,
                "score_breakdown": {"market": 3, "news": 8, "dart": -2, "sector": 1},
            },
            "events": {"news_count": 4, "dart_count": 2},
            "comparison": {"previous_score": 55, "delta": 7},
            "reliability": {
                "status": "방향성 참고 제한",
                "status_class": "limited",
                "guidance": "원문을 함께 확인하세요.",
                "reference_limited": True,
            },
        }

        summary = build_live_pulse_summary(payload)

        self.assertEqual(summary["movement_text"], "직전 실행 대비 +7.0p")
        self.assertEqual(summary["movement_class"], "positive")
        self.assertEqual(summary["top_driver"], "뉴스")
        self.assertEqual(summary["top_driver_direction"], "우호 기여")
        self.assertTrue(summary["reference_limited"])

    def test_live_event_view_adds_impact_and_tag_labels(self):
        event = {
            "title": "실적 부진",
            "source": "테스트경제",
            "impact_score": -3,
            "tags": ["-부진", "-실적 부진"],
        }

        view = build_live_event_view(event, "news")

        self.assertEqual(view["impact_label"], "경계 신호")
        self.assertEqual(view["impact_class"], "negative")
        self.assertEqual(view["impact_score_text"], "-3")
        self.assertEqual(view["tag_labels"], ["부진", "실적 부진"])

    def test_material_dart_filter_and_semantic_deduplication(self):
        self.assertTrue(is_material_dart_event("[기재정정] 단일판매ㆍ공급계약체결"))
        self.assertFalse(is_material_dart_event("기업설명회(IR) 개최"))

        history = [
            {
                "events": {
                    "dart": [
                        {
                            "event_id": "old",
                            "corp_name": "테스트전자",
                            "title": "단일판매ㆍ공급계약체결",
                        }
                    ]
                }
            }
        ]
        events = [
            {
                "event_id": "new-correction",
                "corp_name": "테스트전자",
                "title": "[기재정정] 단일판매ㆍ공급계약체결",
                "published_at": "20260818",
            }
        ]
        now = KST.localize(datetime(2026, 8, 18, 10, 30))

        self.assertEqual(filter_unseen_events(events, history, "dart", now), [])

    def test_top_live_events_combines_sources_and_limits_to_five(self):
        events = {
            "news": [
                {"title": f"뉴스 {index}", "impact_score": index, "published_at": f"10:0{index}"}
                for index in range(1, 5)
            ],
            "dart": [
                {"corp_name": "기업", "title": f"공시 {index}", "impact_score": -index, "published_at": "20260818"}
                for index in range(1, 5)
            ],
        }

        top_events = build_top_live_events(events)

        self.assertEqual(len(top_events), 5)
        self.assertEqual(abs(top_events[0]["impact_score"]), 4)
        self.assertTrue({item["event_type_label"] for item in top_events} <= {"뉴스", "공시"})
        self.assertEqual(sum(item["event_type_label"] == "뉴스" for item in top_events), 3)
        self.assertEqual(sum(item["event_type_label"] == "공시" for item in top_events), 2)

    def test_news_scoring_recognizes_market_warning_and_recovery_language(self):
        scored = intraday.score_news_events(
            [
                {"title": "투자 유의 경고에 업황 둔화 우려"},
                {"title": "수출 회복과 실적 호조로 지수 급등"},
            ]
        )

        self.assertLess(scored[0]["impact_score"], 0)
        self.assertGreater(scored[1]["impact_score"], 0)

    def test_duplicate_execution_target_is_skipped(self):
        history = [{"execution": {"scheduled_target_kst": "2026-08-18 10:30:00"}}]
        self.assertTrue(has_execution_target(history, "2026-08-18 10:30:00"))
        self.assertFalse(has_execution_target(history, "2026-08-18 11:30:00"))


class HtmlSafetyTests(unittest.TestCase):
    def test_bold_filter_escapes_untrusted_html(self):
        rendered = str(bold_filter("**핵심** <img src=x onerror=alert(1)>"))
        self.assertIn("<strong>핵심</strong>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;img", rendered)

    def test_editorial_cover_and_news_context_are_rendered(self):
        metric = {
            "price": "100.00",
            "change": "▲ 1.00 (+1.00%)",
            "trend": "상승",
            "color": "#b91c1c",
        }
        indexes = {
            key: dict(metric)
            for key in ("kospi", "kosdaq", "ewy", "sp500", "dow", "nasdaq", "vix", "usdkrw", "us10y", "wti")
        }
        risk_trends = {
            key: {"text": "상승", "color": "#b91c1c"}
            for key in ("vix", "usdkrw", "us10y", "wti")
        }
        news = [
            {
                "title": "금리 변화 기사",
                "link": "https://example.com/news",
                "press": "테스트경제",
                "time": "09:00",
                "image_url": "https://example.com/news.jpg",
                "impact_scope": "Macro",
                "why_it_matters": "시장 할인율 변화와 연결됩니다.",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(main, "OUTPUT_DIR", Path(tmp_dir)):
                main.render_html(
                    "코스피·나스닥 엇갈린 온도차",
                    ["[한국 시장]", "**KOSPI** 점검", "[미국 시장]", "**NASDAQ** 점검"],
                    "cover_test.png",
                    indexes,
                    risk_trends,
                    3,
                    news,
                )
            html = (Path(tmp_dir) / "index.html").read_text(encoding="utf-8")

        self.assertIn("AI Editorial Cover", html)
        self.assertIn("코스피·나스닥 엇갈린 온도차", html)
        self.assertIn("왜 중요한가", html)
        self.assertIn("news.jpg", html)
        self.assertIn("EWY", html)
        self.assertIn("DOW", html)

    def test_live_dashboard_renders_latest_score_freshness_and_top_events(self):
        metric = {
            "price": "100.00",
            "change": "▲ 1.00 (+1.00%)",
            "trend": "상승",
            "color": "#b91c1c",
            "market_status": "장중",
            "as_of": "08-18 10:12 KST",
        }
        history = [
            {"timestamp": "2026-08-18 09:47:00", "sentiment": {"score": 55, "raw_score": 7.5}}
        ]
        current = {
            "timestamp": "2026-08-18 10:12:00",
            "sentiment": {"score": 62, "raw_score": 18},
        }
        comparison = build_score_comparison(current, history)
        payload = {
            "timestamp": "2026-08-18 10:12:00",
            "window_start": "2026-08-18 10:00:00",
            "window_end": "2026-08-18 10:59:59",
            "market_signals": {
                key: dict(metric)
                for key in ("kospi", "kosdaq", "ewy", "sp500", "dow", "nasdaq", "vix", "usdkrw", "us10y")
            },
            "events": {
                "news_count": 1,
                "dart_count": 1,
                "news": [
                    {
                        "title": "반도체 수주 확대",
                        "url": "https://example.com/news",
                        "source": "테스트경제",
                        "published_at": "2026-08-18 10:05",
                        "impact_score": 4,
                        "tags": ["+수주", "+확대"],
                    }
                ],
                "dart": [
                    {
                        "corp_name": "테스트전자",
                        "title": "공급계약체결",
                        "url": "https://example.com/dart",
                        "published_at": "20260818",
                        "impact_score": 3,
                        "tags": ["+공급계약"],
                    }
                ],
            },
            "sentiment": {
                "label": "우호",
                "score": 62,
                "raw_score": 18,
                "range_key": "bullish",
                "range_rule": "60~79.9는 우호 구간",
                "interpretation": "전반적으로 우호적입니다.",
                "confidence": 80,
                "data_completeness": 80,
                "score_breakdown": {"market": 3, "news": 8, "dart": 3, "sector": 1},
                "data_quality": {"basis": "지표 9/9, 이벤트 2건"},
            },
            "key_points": ["**KOSPI** 흐름을 확인합니다."],
            "watchpoint": "**VIX** 변화를 확인하세요.",
            "day_over_day": ["전일 대비 우호 흐름입니다."],
            "sector_rotation": {"strong": [], "weak": [], "basis": "가격 확인 결합"},
            "reliability": {
                "evaluated": 3,
                "hit_rate": "66.7%",
                "false_alarm_rate": "33.3%",
                "by_label": {"bullish": "100.0%", "bearish": "N/A", "neutral": "50.0%"},
                "basis": "후행 지수 수익률",
                "status": "방향성 추가 확인",
                "status_class": "caution",
                "guidance": "시장 지표와 함께 해석하세요.",
                "reference_limited": False,
            },
            "comparison": comparison,
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(intraday, "OUTPUT_DIR", Path(tmp_dir)):
                intraday.render_live_html(payload)
            html = (Path(tmp_dir) / "live.html").read_text(encoding="utf-8")

        self.assertIn("LIVE · ACTUAL SNAPSHOT", html)
        self.assertIn("직전 실행 대비 +7.0p", html)
        self.assertNotIn("실제 실행 흐름", html)
        self.assertIn("데이터 충실도", html)
        self.assertIn("방향성 추가 확인", html)
        self.assertIn("장중 · 08-18 10:12 KST", html)
        self.assertIn("오늘 핵심 이벤트", html)
        self.assertIn("우호 신호 +4", html)
        self.assertIn("반도체 수주 확대", html)
        self.assertIn("테스트전자 공급계약체결", html)


class MarketCoverageTests(unittest.TestCase):
    def test_fails_when_required_market_sources_are_missing(self):
        indexes = {
            "kospi": {"price": "2,650.00"},
            "kosdaq": {"price": "N/A"},
        }
        with self.assertRaisesRegex(RuntimeError, "below threshold"):
            require_market_coverage(indexes, minimum=2)

    def test_fails_when_required_domestic_index_is_missing(self):
        indexes = {
            "kospi": {"price": "N/A"},
            "kosdaq": {"price": "850.00"},
            "sp500": {"price": "7,000.00"},
        }
        with self.assertRaisesRegex(RuntimeError, "Required market data missing"):
            require_market_coverage(indexes, minimum=2, required=("kospi", "kosdaq"))


class MarketHeadlineTests(unittest.TestCase):
    def test_highlights_cross_market_temperature_gap(self):
        kospi = {"change": "▲ 20.00 (+1.20%)"}
        nasdaq = {"change": "▼ 80.00 (-0.80%)"}
        vix = {"price": "18.00"}
        self.assertEqual(
            main.build_market_headline(kospi, nasdaq, vix),
            "코스피·나스닥 엇갈린 온도차",
        )


class NewsCurationTests(unittest.TestCase):
    def test_preserves_source_and_adds_editorial_context(self):
        payload = {
            "selected_news": [
                {
                    "id": 1,
                    "impact_scope": "Macro",
                    "why_it_matters": "금리 변화가 지수 밸류에이션에 영향을 줄 수 있습니다.",
                }
            ]
        }
        completions = Mock()
        completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        articles = [
            {"title": "첫 기사", "link": "https://example.com/1", "press": "A"},
            {
                "title": "금리 기사",
                "link": "https://example.com/2",
                "press": "B",
                "description": "시장 금리가 변동했습니다.",
            },
        ]

        selected = main.select_top_news(articles, max_count=1, client=client)

        self.assertEqual(selected[0]["title"], "금리 기사")
        self.assertEqual(selected[0]["link"], "https://example.com/2")
        self.assertEqual(selected[0]["impact_scope"], "Macro")
        self.assertIn("밸류에이션", selected[0]["why_it_matters"])
        self.assertEqual(
            completions.create.call_args.kwargs["response_format"]["type"],
            "json_schema",
        )


class CoverGenerationTests(unittest.TestCase):
    def test_cover_prompt_changes_composition_with_current_market_mood(self):
        def metric(change: str, price: str = "100.00") -> dict:
            return {"price": price, "change": change}

        bullish = main.build_market_cover_prompt(
            "코스피·나스닥 동반 강세",
            metric("▲ 1.00 (+1.20%)"),
            metric("▲ 1.00 (+0.90%)"),
            metric("▲ 1.00 (+0.70%)"),
            metric("▲ 1.00 (+1.10%)"),
            metric("▼ 1.00 (-4.00%)", "16.00"),
            metric("▼ 1.00 (-0.30%)"),
            metric("▼ 1.00 (-0.20%)"),
            True,
        )
        defensive = main.build_market_cover_prompt(
            "한미 증시 동반 약세",
            metric("▼ 1.00 (-1.20%)"),
            metric("▼ 1.00 (-1.50%)"),
            metric("▼ 1.00 (-0.80%)"),
            metric("▼ 1.00 (-1.10%)"),
            metric("▲ 1.00 (+12.00%)", "28.00"),
            metric("▲ 1.00 (+0.80%)"),
            metric("▲ 1.00 (+0.70%)"),
            False,
        )

        self.assertIn("broad risk-on participation", bullish)
        self.assertIn("elevated volatility", defensive)
        self.assertIn("KOSPI +1.20%", bullish)
        self.assertNotEqual(bullish, defensive)

    def test_generates_cover_when_briefing_validation_falls_back(self):
        image_bytes = b"generated cover"
        client = SimpleNamespace(
            images=SimpleNamespace(
                generate=Mock(
                    return_value=SimpleNamespace(
                        data=[SimpleNamespace(b64_json=base64.b64encode(image_bytes).decode("ascii"))]
                    )
                )
            )
        )
        market_item = {
            "price": "2,650.00",
            "change": "▲ 10.00 (+0.38%)",
            "trend": "상승",
            "color": "#b91c1c",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.object(main, "OUTPUT_DIR", Path(tmp_dir)),
                patch.object(main, "OPENAI_API_KEY", "test-key"),
                patch.object(main, "GENERATE_AI_IMAGE", True),
                patch.object(main, "OpenAI", return_value=client),
                patch.object(
                    main,
                    "create_structured_completion",
                    side_effect=ValueError("briefing validation failed"),
                ),
            ):
                headline, _, cover_image = main.generate_ai_briefing(
                    market_item,
                    market_item,
                    market_item,
                    market_item,
                    market_item,
                    market_item,
                    market_item,
                    market_item,
                    market_item,
                    market_item,
                )

            self.assertEqual(headline, "변동성 경계 속 지수 방향 점검")
            self.assertTrue(cover_image.startswith("cover_"))
            self.assertEqual((Path(tmp_dir) / cover_image).read_bytes(), image_bytes)
            client.images.generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
