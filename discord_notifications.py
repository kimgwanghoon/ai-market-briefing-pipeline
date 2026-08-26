import logging
import re
import time
from typing import Iterable, List

import requests


POSITIVE_COLOR = 0xDC2626
NEGATIVE_COLOR = 0x2563EB
NEUTRAL_COLOR = 0x64748B


def truncate(value: object, limit: int, suffix: str = "…") -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def strip_bold(value: object) -> str:
    return re.sub(r"\*+", "", str(value or "")).strip()


def score_color(score: object) -> int:
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return NEUTRAL_COLOR
    if numeric >= 60:
        return POSITIVE_COLOR
    if numeric < 40:
        return NEGATIVE_COLOR
    return NEUTRAL_COLOR


def _change_percent(item: dict) -> float | None:
    match = re.search(r"\(([+-]?\d+(?:\.\d+)?)%\)", str(item.get("change", "")))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def market_color(indexes: dict) -> int:
    values = [
        value
        for value in (_change_percent(indexes.get(key, {})) for key in ("kospi", "nasdaq"))
        if value is not None
    ]
    if not values:
        return NEUTRAL_COLOR
    average = sum(values) / len(values)
    if average > 0.3:
        return POSITIVE_COLOR
    if average < -0.3:
        return NEGATIVE_COLOR
    return NEUTRAL_COLOR


def page_url(base_url: str, page: str = "") -> str:
    base = str(base_url or "https://github.com").rstrip("/")
    return f"{base}/{page.lstrip('/')}" if page else base


def market_line(label: str, item: dict) -> str:
    price = item.get("price", "N/A")
    change = item.get("change", "-")
    return truncate(f"**{label}** {price} · {change}", 180)


def market_group(indexes: dict, items: Iterable[tuple[str, str]]) -> str:
    return "\n".join(market_line(label, indexes.get(key, {})) for key, label in items)


def bullet_lines(items: Iterable[object], max_items: int = 3, limit: int = 1000) -> str:
    lines = [f"• {strip_bold(item)}" for item in list(items)[:max_items] if str(item or "").strip()]
    return truncate("\n".join(lines) or "• 확인할 요약이 없습니다.", limit)


def markdown_link(label: object, url: object, limit: int = 150) -> str:
    safe_label = truncate(label, limit).replace("[", "\\[").replace("]", "\\]")
    safe_url = str(url or "").strip().replace(")", "%29")
    if safe_url.startswith(("https://", "http://")):
        return f"[{safe_label}]({safe_url})"
    return safe_label


def _split_summary(summary_items: List[str]) -> tuple[List[str], List[str]]:
    sections = {"korea": [], "us": []}
    current = "korea"
    for item in summary_items:
        if item == "[한국 시장]":
            current = "korea"
        elif item == "[미국 시장]":
            current = "us"
        else:
            sections[current].append(item)
    return sections["korea"], sections["us"]


def _event_rank(event: dict) -> tuple[float, str]:
    return (
        abs(float(event.get("impact_score", 0) or 0)),
        str(event.get("published_at", "")),
    )


def _balanced_event_selection(events: List[dict], max_items: int = 3) -> List[dict]:
    """Prefer two news items and one disclosure, then backfill empty slots."""
    ranked_news = sorted(
        (event for event in events if event.get("event_type_label") == "뉴스"),
        key=_event_rank,
        reverse=True,
    )
    ranked_dart = sorted(
        (event for event in events if event.get("event_type_label") == "공시"),
        key=_event_rank,
        reverse=True,
    )

    news_quota = min(2, max_items)
    dart_quota = max_items - news_quota
    selected = [*ranked_news[:news_quota], *ranked_dart[:dart_quota]]

    if len(selected) < max_items:
        selected_ids = {id(event) for event in selected}
        remaining = sorted(
            (event for event in events if id(event) not in selected_ids),
            key=_event_rank,
            reverse=True,
        )
        selected.extend(remaining[: max_items - len(selected)])

    return selected[:max_items]


def _event_lines(events: List[dict], max_items: int = 3) -> str:
    ranked = _balanced_event_selection(events, max_items=max_items)
    lines = []
    for event in ranked:
        score = float(event.get("impact_score", 0) or 0)
        marker = "🔴" if score >= 2 else "🔵" if score <= -2 else "⚪"
        title = event.get("display_title") or event.get("title", "제목 없음")
        kind = event.get("event_type_label", "이벤트")
        lines.append(f"{marker} `{kind}` {markdown_link(title, event.get('url'))} ({score:+g})")
    return truncate("\n".join(lines) or "이번 실행에서 새로 반영된 주요 이벤트가 없습니다.", 1000)


def build_daily_embed(
    edition_title: str,
    generated_at: str,
    headline: str,
    summary_items: List[str],
    indexes: dict,
    news_items: List[dict],
    base_url: str,
) -> dict:
    korea_summary, us_summary = _split_summary(summary_items)
    news_lines = [
        f"• {markdown_link(item.get('title', '제목 없음'), item.get('link'))} · {item.get('press', '출처 확인')}"
        for item in news_items[:3]
    ]
    edition_icon = "🌅" if "Morning" in edition_title else "🌆"
    return {
        "title": truncate(f"{edition_icon} {edition_title}", 256),
        "description": truncate(f"### {headline}\n`{generated_at} KST`", 4096),
        "color": market_color(indexes),
        "url": page_url(base_url),
        "fields": [
            {
                "name": "🇰🇷 국내 시장",
                "value": market_group(indexes, (("kospi", "KOSPI"), ("kosdaq", "KOSDAQ"), ("ewy", "EWY"))),
                "inline": True,
            },
            {
                "name": "🇺🇸 미국 시장",
                "value": market_group(indexes, (("sp500", "S&P 500"), ("dow", "DOW"), ("nasdaq", "NASDAQ"))),
                "inline": True,
            },
            {
                "name": "⚠️ 리스크 레이더",
                "value": market_group(indexes, (("vix", "VIX"), ("usdkrw", "USD/KRW"), ("us10y", "US10Y"), ("wti", "WTI"))),
                "inline": False,
            },
            {"name": "한국 핵심 요약", "value": bullet_lines(korea_summary), "inline": False},
            {"name": "미국 핵심 요약", "value": bullet_lines(us_summary), "inline": False},
            {
                "name": "📰 주요 뉴스",
                "value": truncate("\n".join(news_lines) or "선정된 주요 뉴스가 없습니다.", 1000),
                "inline": False,
            },
        ],
        "footer": {"text": "제목을 누르면 전체 브리핑으로 이동합니다."},
    }


def build_intraday_embed(payload: dict, base_url: str) -> dict:
    sentiment = payload.get("sentiment", {})
    signals = payload.get("market_signals", {})
    try:
        score = float(sentiment.get("score", 50))
    except (TypeError, ValueError):
        score = 50.0
    comparison = payload.get("comparison", {})
    delta = comparison.get("delta")
    if delta is None and comparison.get("previous_score") is not None:
        delta = round(score - float(comparison["previous_score"]), 1)
    movement = "직전 실행 비교 없음" if delta is None else f"직전 대비 {float(delta):+.1f}p"
    completeness = sentiment.get("data_completeness", sentiment.get("confidence", 0))
    reliability = payload.get("reliability", {})
    events = payload.get("events", {})
    combined_events = [
        *[
            {**event, "event_type_label": "뉴스", "display_title": event.get("title", "")}
            for event in events.get("news", [])
        ],
        *[
            {
                **event,
                "event_type_label": "공시",
                "display_title": f"{event.get('corp_name', '')} {event.get('title', '')}".strip(),
            }
            for event in events.get("dart", [])
        ],
    ]
    generated_at = payload.get("execution", {}).get("actual_timestamp_kst") or payload.get("timestamp", "")
    warning = "\n⚠️ 방향성은 참고용으로 제한됩니다." if reliability.get("reference_limited") else ""
    return {
        "title": truncate(
            f"⏱️ {generated_at[11:16] or '--:--'} 장중 브리핑 · {sentiment.get('label', '중립')} {score:g}점",
            256,
        ),
        "description": truncate(
            f"**{movement}** · 데이터 충실도 **{completeness}%**\n"
            f"검증 상태: **{reliability.get('status', '검증 표본 부족')}**{warning}",
            4096,
        ),
        "color": score_color(score),
        "url": page_url(base_url, "live.html"),
        "fields": [
            {
                "name": "🇰🇷 국내 시장",
                "value": market_group(signals, (("kospi", "KOSPI"), ("kosdaq", "KOSDAQ"))),
                "inline": True,
            },
            {
                "name": "🌐 해외·리스크",
                "value": market_group(signals, (("nasdaq", "NASDAQ"), ("vix", "VIX"), ("usdkrw", "USD/KRW"))),
                "inline": True,
            },
            {"name": "핵심 변화", "value": bullet_lines(payload.get("key_points", []), max_items=2), "inline": False},
            {
                "name": f"🗞️ 이번 실행 이벤트 · 뉴스 {events.get('news_count', 0)} / 공시 {events.get('dart_count', 0)}",
                "value": _event_lines(combined_events),
                "inline": False,
            },
            {"name": "👀 지금 볼 것", "value": truncate(strip_bold(payload.get("watchpoint", "-")), 1000), "inline": False},
        ],
        "footer": {"text": truncate(f"실행 {generated_at} KST · 제목을 누르면 라이브 화면으로 이동합니다.", 2048)},
    }


def _ranked_event_lines(events: List[dict], marker: str, max_items: int = 3) -> str:
    lines = [
        f"{marker} {markdown_link(event.get('title', '제목 없음'), event.get('url'))} ({float(event.get('impact_score', 0) or 0):+g})"
        for event in events[:max_items]
    ]
    return truncate("\n".join(lines) or "해당 이벤트가 없습니다.", 1000)


def build_weekly_embed(payload: dict, base_url: str) -> dict:
    summary = payload.get("summary", {})
    outlook = payload.get("next_week_outlook") or summary.get("next_week_outlook", {})
    performance = payload.get("market_performance") or summary.get("market_performance", [])
    performance_lines = [
        f"**{item.get('label', '-')}** {item.get('change_text', 'N/A')}"
        for item in performance
    ]
    upside = outlook.get("upside_conditions", [])
    downside = outlook.get("downside_conditions", [])
    return {
        "title": truncate(f"📅 {payload.get('title', '주간 시장 리포트')}", 256),
        "description": truncate(
            f"평균 **{summary.get('score_avg', 0)}점 · {summary.get('label', '중립')}**\n"
            f"주초→주말 **{summary.get('score_start', 0)} → {summary.get('score_end', 0)}** "
            f"({summary.get('score_change_text', '0.0p')}) · {summary.get('trading_days', 0)}거래일/{summary.get('count', 0)}표본",
            4096,
        ),
        "color": score_color(summary.get("score_avg", 50)),
        "url": page_url(base_url, "weekly.html"),
        "fields": [
            {"name": "📊 주간 시장 변화", "value": truncate(" · ".join(performance_lines) or "산출 불가", 1000), "inline": False},
            {"name": "🔴 주요 기회", "value": _ranked_event_lines(payload.get("opportunity_events", []), "🔴"), "inline": False},
            {"name": "🔵 주요 위험", "value": _ranked_event_lines(payload.get("risk_events", []), "🔵"), "inline": False},
            {
                "name": "🔭 다음 주 조건부 전망",
                "value": truncate(
                    f"**{outlook.get('bias', '데이터 부족')}**\n"
                    f"예상 범위: **{outlook.get('expected_range', '산출 불가')}** · 신뢰도 {outlook.get('confidence', '낮음')}\n"
                    f"상방 조건: {strip_bold(upside[0]) if upside else '-'}\n"
                    f"하방 조건: {strip_bold(downside[0]) if downside else '-'}",
                    1000,
                ),
                "inline": False,
            },
            {"name": "👀 핵심 관전 포인트", "value": truncate(strip_bold(summary.get("top_watchpoint", "-")), 1000), "inline": False},
        ],
        "footer": {"text": "조건부 시나리오이며 투자수익이나 방향을 보장하지 않습니다."},
    }


def post_discord_webhook(webhook_url: str, embed: dict, max_attempts: int = 3) -> bool:
    if not str(webhook_url or "").strip():
        return False

    payload = {
        "username": "AI Market Briefing",
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
    }
    for attempt in range(max_attempts):
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            if 200 <= response.status_code < 300:
                return True
            if response.status_code == 429 and attempt + 1 < max_attempts:
                try:
                    retry_after = float(response.json().get("retry_after", 1))
                except (TypeError, ValueError, requests.RequestException):
                    retry_after = 1
                time.sleep(max(0.1, min(retry_after, 5)))
                continue
            if response.status_code >= 500 and attempt + 1 < max_attempts:
                time.sleep(0.5 * (attempt + 1))
                continue
            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                logging.warning("Discord notification rejected with status %s: %s", response.status_code, exc)
                return False
        except requests.RequestException as exc:
            if attempt + 1 >= max_attempts:
                logging.warning("Discord notification failed after %s attempts: %s", max_attempts, exc)
                return False
            time.sleep(0.5 * (attempt + 1))
    return False
