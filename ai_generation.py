import json
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any


TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini-2024-07-18").strip() or "gpt-4o-mini-2024-07-18"


DAILY_BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"$ref": "#/$defs/claim"},
        "korea_points": {
            "type": "array",
            "items": {"$ref": "#/$defs/claim"},
            "minItems": 3,
            "maxItems": 3,
        },
        "us_points": {
            "type": "array",
            "items": {"$ref": "#/$defs/claim"},
            "minItems": 2,
            "maxItems": 2,
        },
        "watchpoint": {"$ref": "#/$defs/claim"},
    },
    "required": ["headline", "korea_points", "us_points", "watchpoint"],
    "additionalProperties": False,
    "$defs": {
        "claim": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 260},
                "claim_type": {"type": "string", "enum": ["observation", "conditional_interpretation"]},
                "evidence_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
            "required": ["text", "claim_type", "evidence_ids"],
            "additionalProperties": False,
        }
    },
}


INTRADAY_BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "points": {
            "type": "array",
            "items": {"$ref": "#/$defs/claim"},
            "minItems": 3,
            "maxItems": 3,
        },
        "watchpoint": {"$ref": "#/$defs/claim"},
    },
    "required": ["points", "watchpoint"],
    "additionalProperties": False,
    "$defs": DAILY_BRIEFING_SCHEMA["$defs"],
}


def create_structured_completion(
    client: Any,
    *,
    schema_name: str,
    schema: dict,
    system_prompt: str,
    user_prompt: str,
) -> dict:
    response = client.chat.completions.create(
        model=TEXT_MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
    )
    message = response.choices[0].message
    refusal = getattr(message, "refusal", None)
    if refusal:
        raise ValueError(f"Model refused structured response: {refusal}")
    if not message.content:
        raise ValueError("Model returned an empty structured response")
    parsed = json.loads(message.content)
    if not isinstance(parsed, dict):
        raise ValueError("Structured response must be a JSON object")
    return parsed


def _numeric_tokens(value: Any) -> set[str]:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    normalized = set()
    for token in re.findall(r"(?<![A-Za-z0-9])[-+]?\d[\d,]*(?:\.\d+)?", text):
        try:
            normalized.add(str(Decimal(token.replace(",", "")).normalize()))
        except InvalidOperation:
            continue
    return normalized


def has_only_grounded_numbers(texts: list[str], evidence: Any) -> bool:
    allowed = _numeric_tokens(evidence)
    claimed = _numeric_tokens("\n".join(texts))
    return claimed.issubset(allowed)


def validate_grounded_claims(claims: list[dict], evidence: dict[str, Any], aliases: dict[str, str]) -> bool:
    directional_terms = ("상승", "하락", "반등", "강세", "약세", "확대", "축소")
    prohibited_terms = ("확정", "반드시", "무조건", "매수", "매도", "목표가", "폭등", "폭락")
    for claim in claims:
        text = str(claim.get("text", "")).strip()
        evidence_ids = claim.get("evidence_ids", [])
        if not text or not evidence_ids or any(evidence_id not in evidence for evidence_id in evidence_ids):
            return False
        if any(term in text for term in prohibited_terms):
            return False
        cited = {evidence_id: evidence[evidence_id] for evidence_id in evidence_ids}
        if not has_only_grounded_numbers([text], cited):
            return False
        linked_to_evidence = False
        for alias, required_id in aliases.items():
            if alias.lower() in text.lower() and required_id not in evidence_ids:
                return False
            if alias.lower() in text.lower() and required_id in evidence_ids:
                linked_to_evidence = True
        for item in cited.values():
            if isinstance(item, dict) and item.get("price") == "N/A":
                if any(term in text for term in directional_terms):
                    return False
        for evidence_id, item in cited.items():
            if not evidence_id.startswith(("news-", "dart-")) or not isinstance(item, dict):
                continue
            title = f"{item.get('corp_name', '')} {item.get('title', '')}".strip()
            if title and title not in text:
                return False
            if title and title in text:
                linked_to_evidence = True
        if not linked_to_evidence:
            return False
    return True


def render_grounded_claim(claim: dict, evidence: dict[str, Any], labels: dict[str, str]) -> str:
    observations = []
    events = []
    for evidence_id in claim["evidence_ids"]:
        item = evidence[evidence_id]
        if evidence_id.startswith(("news-", "dart-")):
            title = f"{item.get('corp_name', '')} {item.get('title', '')}".strip()
            if title:
                events.append(title)
            continue
        if evidence_id == "sentiment":
            observations.append(f"**센티먼트** {item.get('score', 50)}점 ({item.get('label', '중립')})")
            continue
        label = labels.get(evidence_id, evidence_id)
        observations.append(
            f"**{label}** {item.get('price', 'N/A')} ({item.get('change', '-')})"
        )

    parts = []
    if observations:
        observed = ", ".join(observations)
        if claim.get("claim_type") == "conditional_interpretation":
            parts.append(f"{observed}의 추가 변화가 나타나면 관련 지표와 함께 재확인하세요.")
        else:
            parts.append(f"{observed}로 관측됐습니다.")
    if events:
        parts.append("새 이벤트: " + ", ".join(f"**{title}**" for title in events))
    return " ".join(parts)


def render_focus_headline(claim: dict, labels: dict[str, str]) -> str:
    names = [labels[evidence_id] for evidence_id in claim["evidence_ids"] if evidence_id in labels]
    if not names:
        return "핵심 시장 지표 점검"
    return f"{'·'.join(names[:2])} 흐름 점검"
