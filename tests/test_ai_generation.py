import json
import unittest
from types import SimpleNamespace

from ai_generation import (
    DAILY_BRIEFING_SCHEMA,
    create_structured_completion,
    has_only_grounded_numbers,
    render_grounded_claim,
    validate_grounded_claims,
)


class FakeCompletions:
    def __init__(self, message):
        self.message = message
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=self.message)])


class StructuredCompletionTests(unittest.TestCase):
    def test_requests_strict_json_schema_and_parses_object(self):
        payload = {
            "headline": {
                "text": "변동성 지표 점검",
                "claim_type": "observation",
                "evidence_ids": ["idx-vix"],
            },
            "korea_points": [
                {"text": text, "claim_type": "observation", "evidence_ids": ["idx-kospi"]}
                for text in ("a", "b", "c")
            ],
            "us_points": [
                {"text": text, "claim_type": "observation", "evidence_ids": ["idx-vix"]}
                for text in ("d", "e")
            ],
            "watchpoint": {
                "text": "오늘의 핵심 관전 포인트: a면 b, c면 d",
                "claim_type": "conditional_interpretation",
                "evidence_ids": ["idx-vix"],
            },
        }
        completions = FakeCompletions(SimpleNamespace(content=json.dumps(payload), refusal=None))
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        result = create_structured_completion(
            client,
            schema_name="daily_market_briefing",
            schema=DAILY_BRIEFING_SCHEMA,
            system_prompt="system",
            user_prompt="user",
        )

        self.assertEqual(result, payload)
        response_format = completions.kwargs["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])

    def test_refusal_raises_value_error(self):
        completions = FakeCompletions(SimpleNamespace(content=None, refusal="cannot comply"))
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        with self.assertRaisesRegex(ValueError, "refused"):
            create_structured_completion(
                client,
                schema_name="daily_market_briefing",
                schema=DAILY_BRIEFING_SCHEMA,
                system_prompt="system",
                user_prompt="user",
            )

    def test_rejects_number_absent_from_evidence(self):
        evidence = {"kospi": {"price": "2,650.25", "change": "+0.50%"}}
        self.assertTrue(has_only_grounded_numbers(["KOSPI **2,650.25**, 등락률 +0.50%"], evidence))
        self.assertFalse(has_only_grounded_numbers(["KOSPI **9,999**"], evidence))

    def test_rejects_cross_metric_number_swap(self):
        evidence = {
            "idx-kospi": {"price": "2,650"},
            "idx-vix": {"price": "15"},
        }
        claim = {"text": "KOSPI 15", "claim_type": "observation", "evidence_ids": ["idx-kospi"]}
        self.assertFalse(validate_grounded_claims([claim], evidence, {"KOSPI": "idx-kospi"}))

    def test_rejects_directional_claim_for_missing_metric(self):
        evidence = {"idx-vix": {"price": "N/A"}}
        claim = {
            "text": "VIX 상승세를 확인합니다",
            "claim_type": "observation",
            "evidence_ids": ["idx-vix"],
        }
        self.assertFalse(validate_grounded_claims([claim], evidence, {"VIX": "idx-vix"}))

    def test_news_claim_must_include_cited_title(self):
        evidence = {"news-1": {"title": "A사 실적 개선"}}
        claim = {
            "text": "B사 부도 위험 확대",
            "claim_type": "observation",
            "evidence_ids": ["news-1"],
        }
        self.assertFalse(validate_grounded_claims([claim], evidence, {}))

    def test_rejects_unsupported_certainty_even_with_valid_id(self):
        evidence = {"idx-kospi": {"price": "2,650", "trend": "상승"}}
        claim = {
            "text": "KOSPI 폭등 확정",
            "claim_type": "observation",
            "evidence_ids": ["idx-kospi"],
        }
        self.assertFalse(validate_grounded_claims([claim], evidence, {"KOSPI": "idx-kospi"}))

    def test_rendered_claim_uses_server_evidence_not_model_prose(self):
        evidence = {"news-1": {"title": "A사 실적 개선"}}
        claim = {
            "text": "A사 실적 개선, B사 유동성 위기",
            "claim_type": "observation",
            "evidence_ids": ["news-1"],
        }
        rendered = render_grounded_claim(claim, evidence, {})
        self.assertIn("A사 실적 개선", rendered)
        self.assertNotIn("B사", rendered)


if __name__ == "__main__":
    unittest.main()
