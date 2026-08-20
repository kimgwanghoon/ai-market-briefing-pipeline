import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pytz
import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

from intraday import score_dart_events, score_news_events
from main import bold_filter, resolve_pages_url


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "public"
INTRADAY_DATA_DIR = OUTPUT_DIR / "data" / "intraday"
WEEKLY_DATA_DIR = OUTPUT_DIR / "data" / "reports"
WEEKLY_DATA_DIR.mkdir(parents=True, exist_ok=True)

KST = pytz.timezone("Asia/Seoul")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()


def parse_snapshot_dt(item: dict) -> datetime:
    return KST.localize(datetime.strptime(item["timestamp"], "%Y-%m-%d %H:%M:%S"))


def load_week_snapshots(days: int = 7) -> List[dict]:
    cutoff = datetime.now(KST) - timedelta(days=days)
    snapshots: List[dict] = []
    for file_path in sorted(INTRADAY_DATA_DIR.glob("*.json"), reverse=True):
        if file_path.name == "latest.json":
            continue
        try:
            item = json.loads(file_path.read_text(encoding="utf-8"))
            item_dt = parse_snapshot_dt(item)
        except Exception:
            continue
        if item_dt < cutoff:
            continue
        snapshots.append(item)
    return sorted(snapshots, key=parse_snapshot_dt)


def get_display_score(item: dict) -> float:
    sentiment = item.get("sentiment", {})
    if "raw_score" in sentiment:
        return float(sentiment.get("score", 50))
    return round(max(0, min(100, (float(sentiment.get("score", 0)) + 100) / 2)), 1)


def score_label(score: float) -> str:
    if score >= 80:
        return "강한 우호"
    if score >= 60:
        return "우호"
    if score >= 40:
        return "중립"
    if score >= 20:
        return "경계"
    return "강한 경계"


def parse_price(value: object) -> float | None:
    try:
        cleaned = str(value).replace(",", "").strip()
        if not cleaned or cleaned.upper() == "N/A":
            return None
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def build_daily_points(snapshots: List[dict]) -> List[dict]:
    grouped: Dict[str, List[dict]] = {}
    for item in sorted(snapshots, key=parse_snapshot_dt):
        day = parse_snapshot_dt(item).strftime("%m-%d")
        grouped.setdefault(day, []).append(item)

    points = []
    for day, items in grouped.items():
        scores = [get_display_score(item) for item in items]
        average_score = round(sum(scores) / len(scores), 1)
        change = round(scores[-1] - scores[0], 1)
        points.append(
            {
                "day": day,
                "score_avg": average_score,
                "score_open": round(scores[0], 1),
                "score_close": round(scores[-1], 1),
                "score_change": change,
                "change_text": f"{change:+.1f}p" if change else "0.0p",
                "change_class": "positive" if change > 0 else "negative" if change < 0 else "flat",
                "label": score_label(average_score),
                "count": len(items),
            }
        )
    return points


def build_market_performance(snapshots: List[dict]) -> List[dict]:
    markets = [
        ("kospi", "KOSPI"),
        ("kosdaq", "KOSDAQ"),
        ("sp500", "S&P 500"),
        ("nasdaq", "NASDAQ"),
        ("vix", "VIX"),
        ("usdkrw", "USD/KRW"),
    ]
    performance = []
    for key, label in markets:
        observations = []
        for item in sorted(snapshots, key=parse_snapshot_dt):
            market_item = item.get("market_signals", {}).get(key, {})
            price = parse_price(market_item.get("price"))
            if price is not None:
                observations.append((price, str(market_item.get("price", "N/A"))))
        if len(observations) < 2 or observations[0][0] == 0:
            performance.append(
                {"key": key, "label": label, "start": "N/A", "end": "N/A", "change_pct": None, "change_text": "N/A", "change_class": "flat"}
            )
            continue
        start_value, start_text = observations[0]
        end_value, end_text = observations[-1]
        change_pct = round(((end_value - start_value) / start_value) * 100, 2)
        performance.append(
            {
                "key": key,
                "label": label,
                "start": start_text,
                "end": end_text,
                "change_pct": change_pct,
                "change_text": f"{change_pct:+.2f}%" if change_pct else "0.00%",
                "change_class": "positive" if change_pct > 0 else "negative" if change_pct < 0 else "flat",
            }
        )
    return performance


def build_ranked_events(snapshots: List[dict]) -> dict:
    deduped: Dict[str, dict] = {}
    for item in snapshots:
        observed_at = item.get("timestamp", "")
        for event_type, scorer in (("news", score_news_events), ("dart", score_dart_events)):
            for event in item.get("events", {}).get(event_type, []):
                event_id = str(event.get("event_id") or event.get("url") or event.get("title") or "")
                if not event_id or event_id in deduped:
                    continue
                rescored = scorer([event])[0]
                impact = float(rescored.get("impact_score", 0) or 0)
                title = rescored.get("title", "")
                if event_type == "dart":
                    title = f"{rescored.get('corp_name', '')} {title}".strip()
                deduped[event_id] = {
                    "title": title,
                    "url": rescored.get("url", ""),
                    "source": rescored.get("source", "네이버증권") if event_type == "news" else "DART",
                    "observed_at": observed_at,
                    "impact_score": impact,
                    "impact_text": f"{impact:+g}" if impact else "0",
                    "event_type": event_type,
                }

    risks = sorted((event for event in deduped.values() if event["impact_score"] < 0), key=lambda event: abs(event["impact_score"]), reverse=True)[:5]
    opportunities = sorted((event for event in deduped.values() if event["impact_score"] > 0), key=lambda event: event["impact_score"], reverse=True)[:5]
    return {"risks": risks, "opportunities": opportunities}


def build_next_week_outlook(summary: dict) -> dict:
    score_avg = float(summary.get("score_avg", 50))
    score_end = float(summary.get("score_end", score_avg))
    score_change = float(summary.get("score_change", 0))
    score_range = max(0.0, float(summary.get("score_max", score_avg)) - float(summary.get("score_min", score_avg)))

    momentum_adjustment = max(-4.0, min(4.0, score_change * 0.15))
    projected_center = max(0.0, min(100.0, score_end * 0.55 + score_avg * 0.45 + momentum_adjustment))
    range_width = max(6.0, min(15.0, score_range * 0.35))
    expected_low = round(max(0.0, projected_center - range_width), 1)
    expected_high = round(min(100.0, projected_center + range_width), 1)

    if projected_center >= 65:
        bias = "우호 흐름 우세"
    elif projected_center >= 55:
        bias = "중립 상단에서 우호 전환 시도"
    elif projected_center >= 45:
        bias = "중립권 등락 가능성 우세"
    elif projected_center >= 35:
        bias = "중립 하단에서 경계 신호 우세"
    else:
        bias = "경계 흐름 지속 가능성"

    trading_days = int(summary.get("trading_days", 0))
    sample_count = int(summary.get("count", 0))
    if trading_days >= 5 and sample_count >= 20:
        confidence = "보통"
    elif trading_days >= 4 and sample_count >= 12:
        confidence = "제한적"
    else:
        confidence = "낮음"

    opportunities = summary.get("opportunity_events", [])
    risks = summary.get("risk_events", [])
    rationale = [
        f"주간 일평균 센티먼트 {score_avg:.1f}점, 마지막 관측 {score_end:.1f}점입니다.",
        f"주초 대비 주말 변화는 {score_change:+.1f}p, 주간 고저 차이는 {score_range:.1f}p입니다.",
        f"규칙 기반 주요 이벤트는 우호 {len(opportunities)}건, 경계 {len(risks)}건입니다.",
    ]

    upside_conditions = ["VIX와 USD/KRW가 동반 안정되고 KOSPI·NASDAQ이 함께 개선되는 경우"]
    if opportunities:
        upside_conditions.append(f"주요 우호 이벤트가 후속 가격 흐름으로 확인되는 경우: {summary.get('top_opportunity', '')}")
    downside_conditions = ["VIX·USD/KRW 상승과 국내외 지수 약세가 동시에 나타나는 경우"]
    if risks:
        downside_conditions.append(f"주요 경계 이벤트의 영향이 확대되는 경우: {summary.get('top_risk', '')}")

    return {
        "bias": bias,
        "expected_low": expected_low,
        "expected_high": expected_high,
        "expected_range": f"{expected_low:.1f}~{expected_high:.1f}점",
        "confidence": confidence,
        "rationale": rationale,
        "upside_conditions": upside_conditions,
        "downside_conditions": downside_conditions,
        "disclaimer": "과거 주간 스냅샷을 기반으로 한 조건부 시나리오이며 투자수익이나 방향을 보장하지 않습니다.",
    }


def build_week_summary(snapshots: List[dict]) -> dict:
    if not snapshots:
        return {
            "score_avg": 0,
            "score_max": 0,
            "score_min": 0,
            "count": 0,
            "label": "데이터 부족",
            "top_risk": "데이터 부족",
            "top_opportunity": "데이터 부족",
            "top_watchpoint": "데이터 부족",
            "trading_days": 0,
            "score_start": 0,
            "score_end": 0,
            "score_change": 0,
            "score_change_text": "0.0p",
            "score_change_class": "flat",
            "period_start": "-",
            "period_end": "-",
            "daily_points": [],
            "market_performance": [],
            "risk_events": [],
            "opportunity_events": [],
            "next_week_outlook": {
                "bias": "데이터 부족",
                "expected_range": "산출 불가",
                "confidence": "낮음",
                "rationale": ["집계 가능한 장중 스냅샷이 없습니다."],
                "upside_conditions": [],
                "downside_conditions": [],
                "disclaimer": "충분한 데이터가 확보된 뒤 시나리오를 제공합니다.",
            },
        }

    ordered = sorted(snapshots, key=parse_snapshot_dt)
    scores = [get_display_score(item) for item in ordered]
    daily_points = build_daily_points(ordered)
    score_avg = round(sum(point["score_avg"] for point in daily_points) / len(daily_points), 1)
    score_max = round(max(scores), 2)
    score_min = round(min(scores), 2)
    score_start = round(scores[0], 1)
    score_end = round(scores[-1], 1)
    score_change = round(score_end - score_start, 1)
    ranked_events = build_ranked_events(ordered)
    top_risk = ranked_events["risks"][0]["title"] if ranked_events["risks"] else "유의미한 하방 이벤트 부족"
    top_opportunity = ranked_events["opportunities"][0]["title"] if ranked_events["opportunities"] else "유의미한 상방 이벤트 부족"

    watchpoint = ""
    for item in reversed(snapshots):
        watchpoint = item.get("watchpoint", "")
        if watchpoint:
            break
    watchpoint = re.sub(
        r"^오늘의\s+(?:\*\*)?핵심 관전 포인트(?:\*\*)?\s*:\s*",
        "",
        watchpoint,
    )

    summary = {
        "score_avg": score_avg,
        "score_max": score_max,
        "score_min": score_min,
        "count": len(ordered),
        "trading_days": len(daily_points),
        "label": score_label(score_avg),
        "score_start": score_start,
        "score_end": score_end,
        "score_change": score_change,
        "score_change_text": f"{score_change:+.1f}p" if score_change else "0.0p",
        "score_change_class": "positive" if score_change > 0 else "negative" if score_change < 0 else "flat",
        "period_start": parse_snapshot_dt(ordered[0]).strftime("%Y-%m-%d"),
        "period_end": parse_snapshot_dt(ordered[-1]).strftime("%Y-%m-%d"),
        "top_risk": top_risk,
        "top_opportunity": top_opportunity,
        "top_watchpoint": watchpoint or "핵심 관전 포인트 데이터 없음",
        "daily_points": daily_points,
        "market_performance": build_market_performance(ordered),
        "risk_events": ranked_events["risks"],
        "opportunity_events": ranked_events["opportunities"],
    }
    summary["next_week_outlook"] = build_next_week_outlook(summary)
    return summary


def save_weekly_report(summary: dict, snapshots: List[dict]) -> dict:
    now = datetime.now(KST)
    year, week, _ = now.isocalendar()
    title = f"주간 시장 리포트 | {summary.get('period_start', '-')} ~ {summary.get('period_end', '-')}"
    payload = {
        "schema_version": "1.1",
        "title": title,
        "report_id": f"{year}-W{week:02d}",
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "daily_points": summary.get("daily_points", []),
        "market_performance": summary.get("market_performance", []),
        "risk_events": summary.get("risk_events", []),
        "opportunity_events": summary.get("opportunity_events", []),
        "next_week_outlook": summary.get("next_week_outlook", {}),
        "recent_samples": [
            {
                "timestamp": item.get("timestamp", ""),
                "score": item.get("sentiment", {}).get("score", 0),
                "label": item.get("sentiment", {}).get("label", "neutral"),
            }
            for item in snapshots[-10:]
        ],
    }
    report_name = f"weekly-{year}-W{week:02d}.json"
    (WEEKLY_DATA_DIR / report_name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (WEEKLY_DATA_DIR / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def render_weekly_html(payload: dict) -> None:
    env = Environment(
        loader=FileSystemLoader(str(BASE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["bold"] = bold_filter
    template = env.get_template("template_weekly.html")
    html = template.render(
        title=payload.get("title", "주간 시장 리포트"),
        generated_at=payload.get("generated_at", ""),
        summary=payload.get("summary", {}),
        daily_points=payload.get("daily_points", []),
        market_performance=payload.get("market_performance", []),
        risk_events=payload.get("risk_events", []),
        opportunity_events=payload.get("opportunity_events", []),
        next_week_outlook=payload.get("next_week_outlook", {}),
        pages_url=resolve_pages_url(),
    )
    (OUTPUT_DIR / "weekly.html").write_text(html, encoding="utf-8")


def send_weekly_discord(payload: dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        return

    summary = payload.get("summary", {})
    embed = {
        "title": payload.get("title", "주간 시장 리포트"),
        "description": (
            f"평균 점수 **{summary.get('score_avg', 0)}점 ({summary.get('label', '중립')})**\n"
            f"주초→주말: {summary.get('score_start', 0)} → {summary.get('score_end', 0)} ({summary.get('score_change_text', '0.0p')})\n"
            f"다음 주: {summary.get('next_week_outlook', {}).get('bias', '데이터 부족')}"
        ),
        "color": 5763714,
        "url": f"{resolve_pages_url()}/weekly.html",
        "fields": [
            {"name": "집계 샘플", "value": str(summary.get("count", 0)), "inline": True},
            {"name": "예상 센티먼트", "value": summary.get("next_week_outlook", {}).get("expected_range", "산출 불가"), "inline": True},
            {"name": "핵심 관전 포인트", "value": summary.get("top_watchpoint", "-")[:1000], "inline": False},
        ],
    }
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Discord notification failed: {exc}")


def main() -> None:
    snapshots = load_week_snapshots(days=7)
    minimum_samples = int(os.getenv("MIN_WEEKLY_SAMPLES", "6"))
    if len(snapshots) < minimum_samples:
        raise RuntimeError(f"Weekly sample coverage below threshold: {len(snapshots)} (minimum {minimum_samples})")
    summary = build_week_summary(snapshots)
    payload = save_weekly_report(summary, snapshots)
    render_weekly_html(payload)
    send_weekly_discord(payload)
    print("Generated:", OUTPUT_DIR / "weekly.html")


if __name__ == "__main__":
    main()
