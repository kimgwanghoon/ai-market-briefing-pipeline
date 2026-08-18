import unittest
from datetime import datetime

from main import bold_filter, require_market_coverage
from intraday import (
    KST,
    build_data_quality,
    compute_reliability,
    filter_unseen_events,
    find_previous_snapshot,
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


class HtmlSafetyTests(unittest.TestCase):
    def test_bold_filter_escapes_untrusted_html(self):
        rendered = str(bold_filter("**핵심** <img src=x onerror=alert(1)>"))
        self.assertIn("<strong>핵심</strong>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;img", rendered)


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


if __name__ == "__main__":
    unittest.main()
