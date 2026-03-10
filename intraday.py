import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import pytz
import requests
from jinja2 import Environment, FileSystemLoader
from openai import OpenAI

from main import build_market_overview, get_batch_index_data, get_index_data, get_korean_index_data, resolve_pages_url

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "public"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
INTRADAY_DATA_DIR = OUTPUT_DIR / "data" / "intraday"
INTRADAY_DATA_DIR.mkdir(parents=True, exist_ok=True)

KST = pytz.timezone("Asia/Seoul")

OPENAI_API_KEY = os.getenv("AI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
DART_API_KEY = os.getenv("DART_API_KEY", "").strip()


def bold_filter(text: str) -> str:
    return re.sub(r"\*+([^*]+)\*+", r"<strong>\1</strong>", text)


def parse_snapshot_dt(snapshot: dict) -> datetime:
    try:
        return KST.localize(datetime.strptime(snapshot.get("timestamp", ""), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return datetime.now(KST)


def load_intraday_history(limit: int = 400) -> List[dict]:
    snapshots: List[dict] = []
    files = sorted(INTRADAY_DATA_DIR.glob("*.json"), reverse=True)
    for file_path in files:
        if file_path.name == "latest.json":
            continue
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        snapshots.append(data)
        if len(snapshots) >= limit:
            break
    return snapshots


def score_text(text: str, positive: Dict[str, int], negative: Dict[str, int]) -> Tuple[int, List[str]]:
    lowered = text.lower()
    score = 0
    tags: List[str] = []

    for word, weight in positive.items():
        if word in lowered:
            score += weight
            tags.append(f"+{word}")
    for word, weight in negative.items():
        if word in lowered:
            score -= weight
            tags.append(f"-{word}")

    return score, tags


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_price(value: str) -> float:
    cleaned = str(value).replace(",", "").strip()
    if cleaned in {"", "N/A", "-"}:
        return float("nan")
    try:
        return float(cleaned)
    except ValueError:
        return float("nan")


def parse_change_percent(change: str) -> float:
    match = re.search(r"\(([+-]?\d+(?:\.\d+)?)%\)", str(change))
    if not match:
        return float("nan")
    try:
        return float(match.group(1))
    except ValueError:
        return float("nan")


def aggregate_event_score(events: List[dict], limit: int = 8) -> float:
    ordered = sorted(events, key=lambda x: abs(float(x.get("impact_score", 0))), reverse=True)[:limit]
    total = 0.0
    for idx, event in enumerate(ordered):
        weight = max(0.35, 1 - idx * 0.1)
        total += float(event.get("impact_score", 0)) * weight
    return clamp(total, -18, 18)


def average(values: List[float]) -> float:
    valid = [value for value in values if value == value]
    if not valid:
        return float("nan")
    return sum(valid) / len(valid)


def percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = clamp(ratio, 0.0, 1.0) * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    weight = pos - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def robust_median_scale(values: List[float]) -> Tuple[float, float]:
    valid = [value for value in values if value == value]
    if not valid:
        return 0.0, 1.0
    med = percentile(valid, 0.5)
    deviations = [abs(value - med) for value in valid]
    mad = percentile(deviations, 0.5)
    scale = mad * 1.4826
    if scale < 0.35:
        mean = sum(valid) / len(valid)
        variance = sum((value - mean) ** 2 for value in valid) / max(1, len(valid) - 1)
        std = variance ** 0.5
        scale = std if std >= 0.35 else 1.0
    return med, scale


def raw_score_label(score: float) -> str:
    if score >= 16:
        return "bullish"
    if score <= -16:
        return "bearish"
    return "neutral"


def normalize_sentiment_score(raw_score: float) -> float:
    return round(clamp((raw_score + 100) / 2, 0, 100), 1)


def describe_display_score(display_score: float) -> dict:
    if display_score >= 80:
        return {
            "label": "강한 우호",
            "range_key": "strong_bullish",
            "range_rule": "80~100은 강한 우호 구간",
            "interpretation": "상승 심리와 위험선호가 강한 상태입니다.",
        }
    if display_score >= 60:
        return {
            "label": "우호",
            "range_key": "bullish",
            "range_rule": "60~79.9는 우호 구간",
            "interpretation": "전반적으로 우호적이지만 추가 확인이 필요한 상태입니다.",
        }
    if display_score >= 40:
        return {
            "label": "중립",
            "range_key": "neutral",
            "range_rule": "40~59.9는 중립 구간",
            "interpretation": "상승·하락 신호가 혼재한 상태입니다.",
        }
    if display_score >= 20:
        return {
            "label": "경계",
            "range_key": "caution",
            "range_rule": "20~39.9는 경계 구간",
            "interpretation": "약세 압력과 변동성 우려가 우세한 상태입니다.",
        }
    return {
        "label": "강한 경계",
        "range_key": "strong_caution",
        "range_rule": "0~19.9는 강한 경계 구간",
        "interpretation": "위험회피 심리가 강한 변동성 확대 상태입니다.",
    }


def display_score_color(display_score: float) -> str:
    if display_score >= 80:
        return "#fecaca"
    if display_score >= 60:
        return "#fee2e2"
    if display_score <= 19.9:
        return "#bfdbfe"
    if display_score <= 39.9:
        return "#dbeafe"
    return "#e2e8f0"


def get_snapshot_raw_score(item: dict) -> float:
    sentiment = item.get("sentiment", {})
    if "raw_score" in sentiment:
        return float(sentiment.get("raw_score", 0))
    return float(sentiment.get("score", 0))


def get_snapshot_display_score(item: dict) -> float:
    sentiment = item.get("sentiment", {})
    if "raw_score" in sentiment:
        return float(sentiment.get("score", 50))
    return normalize_sentiment_score(float(sentiment.get("score", 0)))


def format_score_breakdown(score_breakdown: dict) -> str:
    return (
        f"시장 {score_breakdown.get('market', 0)} / "
        f"뉴스 {score_breakdown.get('news', 0)} / "
        f"공시 {score_breakdown.get('dart', 0)} / "
        f"섹터 {score_breakdown.get('sector', 0)}"
    )


def build_confidence_tooltip(confidence: int, data_quality: dict) -> List[str]:
    if confidence >= 80:
        level_text = "데이터 커버리지가 높은 상태입니다."
    elif confidence >= 60:
        level_text = "기본 데이터는 충분하지만 일부 확인 신호가 제한적일 수 있습니다."
    elif confidence >= 40:
        level_text = "참고용 해석은 가능하지만 추가 확인이 필요합니다."
    else:
        level_text = "데이터가 부족해 보수적으로 해석해야 합니다."
    return [
        "신뢰도 안내",
        "신뢰도는 예측 적중률이 아니라, 현재 점수 계산에 사용된 데이터의 충실도를 의미합니다.",
        f"현재 신뢰도: {confidence}%",
        f"해석: {level_text}",
        f"기준: {data_quality.get('basis', '데이터 기준 없음')}",
        "주의: 이 값이 미래 방향 적중률을 직접 뜻하지는 않습니다.",
    ]


def bucket_time_to_slot(dt: datetime, slots: List[str], tolerance_minutes: int = 35) -> str | None:
    current_minutes = dt.hour * 60 + dt.minute
    best_slot = None
    best_gap = None
    for slot in slots:
        hour, minute = [int(part) for part in slot.split(":")]
        slot_minutes = hour * 60 + minute
        gap = abs(current_minutes - slot_minutes)
        if best_gap is None or gap < best_gap:
            best_slot = slot
            best_gap = gap
    if best_gap is None or best_gap > tolerance_minutes:
        return None
    return best_slot


def fetch_market_signals() -> Dict[str, dict]:
    return {
        "kospi": get_korean_index_data("KOSPI"),
        "kosdaq": get_korean_index_data("KOSDAQ"),
        "sp500": get_index_data("^GSPC"),
        "dow": get_index_data("^DJI"),
        "nasdaq": get_index_data("^IXIC"),
        "ewy": get_index_data("EWY"),
        "vix": get_index_data("^VIX"),
        "usdkrw": get_index_data("KRW=X"),
        "us10y": get_index_data("^TNX"),
    }


def fetch_naver_news(limit: int = 20) -> List[dict]:
    url = "https://finance.naver.com/news/mainnews.naver"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        text = requests.get(url, headers=headers, timeout=10).text
    except Exception:
        return []

    pattern = re.compile(
        r'<dd class="articleSubject">\s*<a href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<dd class="articleSummary">(.*?)<span class="press">(.*?)</span>\s*'
        r'<span class="wdate">(.*?)</span>',
        re.S,
    )

    events: List[dict] = []
    for href, title_html, _, press_html, wdate_html in pattern.findall(text):
        title = re.sub(r"<.*?>", "", title_html).strip()
        press = re.sub(r"<.*?>", "", press_html)
        press = re.sub(r"\s+", " ", press).replace("|", "").strip()
        wdate = re.sub(r"<.*?>", "", wdate_html).strip()
        if not title:
            continue

        link = href if href.startswith("http") else f"https://finance.naver.com{href}"
        events.append(
            {
                "source": press or "네이버증권",
                "title": title,
                "published_at": wdate,
                "url": link,
            }
        )
        if len(events) >= limit:
            break

    return events


def fetch_dart_events(limit: int = 30) -> List[dict]:
    if not DART_API_KEY:
        return []

    now = datetime.now(KST)
    target_date = now.strftime("%Y%m%d")
    try:
        response = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": DART_API_KEY,
                "bgn_de": target_date,
                "end_de": target_date,
                "last_reprt_at": "Y",
                "page_count": "100",
            },
            timeout=12,
        )
        payload = response.json()
    except Exception:
        return []

    if payload.get("status") != "000":
        return []

    items = payload.get("list", [])
    events: List[dict] = []
    for item in items[:limit]:
        rcept_no = item.get("rcept_no", "")
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
        events.append(
            {
                "corp_name": item.get("corp_name", ""),
                "title": item.get("report_nm", ""),
                "published_at": item.get("rcept_dt", ""),
                "url": url,
            }
        )

    return events


def score_news_events(events: List[dict]) -> List[dict]:
    positive = {
        "실적": 2,
        "최대": 1,
        "수주": 3,
        "흑자": 2,
        "상향": 2,
        "반등": 1,
        "매수": 1,
        "성장": 2,
        "증가": 1,
        "확대": 1,
    }
    negative = {
        "유상증자": 4,
        "적자": 3,
        "하향": 2,
        "급락": 3,
        "소송": 2,
        "횡령": 5,
        "리스크": 2,
        "감소": 1,
        "부진": 2,
        "축소": 1,
    }

    scored: List[dict] = []
    for event in events:
        score, tags = score_text(event.get("title", ""), positive, negative)
        event_copy = dict(event)
        event_copy["impact_score"] = score
        event_copy["tags"] = tags
        scored.append(event_copy)
    return scored


def score_dart_events(events: List[dict]) -> List[dict]:
    positive = {
        "매출액": 2,
        "영업이익": 2,
        "공급계약": 3,
        "자사주": 2,
        "배당": 2,
        "합병": 1,
        "투자판단": 1,
    }
    negative = {
        "유상증자": 4,
        "전환사채": 3,
        "신주인수권부사채": 3,
        "감사의견": 3,
        "불성실": 3,
        "정정": 0,
    }

    scored: List[dict] = []
    for event in events:
        target = f"{event.get('corp_name', '')} {event.get('title', '')}"
        score, tags = score_text(target, positive, negative)
        event_copy = dict(event)
        event_copy["impact_score"] = score
        event_copy["tags"] = tags
        scored.append(event_copy)
    return scored


def market_reaction_score(indexes: Dict[str, dict]) -> float:
    components = {
        "kospi": 1.4,
        "kosdaq": 1.5,
        "sp500": 0.9,
        "dow": 0.7,
        "nasdaq": 1.0,
        "ewy": 0.8,
        "vix": -1.2,
        "usdkrw": -1.0,
        "us10y": -0.8,
    }
    total = 0.0
    for key, weight in components.items():
        change_pct = parse_change_percent(indexes.get(key, {}).get("change", ""))
        if change_pct != change_pct:
            continue
        total += clamp(change_pct * weight, -3.0, 3.0)
    return round(clamp(total, -12, 12), 2)


def build_data_quality(indexes: Dict[str, dict], news: List[dict], darts: List[dict], sector_rotation: dict) -> dict:
    live_indexes = 0
    for item in indexes.values():
        pct_change = parse_change_percent(item.get("change", ""))
        if pct_change == pct_change:
            live_indexes += 1

    event_count = len(news) + len(darts)
    sector_confirmed = sum(1 for item in sector_rotation.get("scores", []) if item.get("price_confirmed"))
    coverage = clamp((live_indexes / 9) * 45 + min(event_count, 12) * 3 + min(sector_confirmed, 4) * 4, 35, 95)
    return {
        "live_indexes": live_indexes,
        "event_count": event_count,
        "sector_confirmed": sector_confirmed,
        "score": int(round(coverage)),
        "basis": f"지표 {live_indexes}/9, 이벤트 {event_count}건, 섹터확인 {sector_confirmed}건",
    }


def extract_snapshot_components(snapshot: dict) -> dict:
    sentiment = snapshot.get("sentiment", {})
    breakdown = sentiment.get("score_breakdown", {})
    market = breakdown.get("market", sentiment.get("market_score", 0))
    news = breakdown.get("news", sentiment.get("news_score", 0))
    dart = breakdown.get("dart", sentiment.get("dart_score", 0))
    sector = breakdown.get("sector", sentiment.get("sector_score", 0))
    return {
        "market": float(market or 0),
        "news": float(news or 0),
        "dart": float(dart or 0),
        "sector": float(sector or 0),
    }


def build_component_stats(history: List[dict]) -> dict:
    buckets = {"market": [], "news": [], "dart": [], "sector": []}
    for item in history:
        components = extract_snapshot_components(item)
        for key in buckets:
            value = float(components.get(key, 0))
            if value == value:
                buckets[key].append(value)

    stats = {}
    for key, values in buckets.items():
        if not values:
            stats[key] = {"median": 0.0, "scale": 1.0, "p02": -1.0, "p98": 1.0}
            continue
        med, scale = robust_median_scale(values)
        p02 = percentile(values, 0.02)
        p98 = percentile(values, 0.98)
        if p02 == p98:
            p02 = med - scale
            p98 = med + scale
        stats[key] = {
            "median": round(med, 4),
            "scale": round(scale, 4),
            "p02": round(p02, 4),
            "p98": round(p98, 4),
        }
    return stats


def normalize_component(value: float, stat: dict) -> float:
    lower = float(stat.get("p02", -1.0))
    upper = float(stat.get("p98", 1.0))
    bounded = clamp(value, lower, upper)
    median = float(stat.get("median", 0.0))
    scale = max(0.35, float(stat.get("scale", 1.0)))
    return round(clamp((bounded - median) / scale, -3.0, 3.0), 4)


def infer_forward_label(current: dict, next_item: dict) -> str | None:
    current_market = current.get("market_signals", {})
    next_market = next_item.get("market_signals", {})
    current_pct = average(
        [
            parse_change_percent(current_market.get("kospi", {}).get("change", "")),
            parse_change_percent(current_market.get("kosdaq", {}).get("change", "")),
        ]
    )
    next_pct = average(
        [
            parse_change_percent(next_market.get("kospi", {}).get("change", "")),
            parse_change_percent(next_market.get("kosdaq", {}).get("change", "")),
        ]
    )
    if current_pct != current_pct or next_pct != next_pct:
        return None
    move = next_pct - current_pct
    if move >= 0.15:
        return "bullish"
    if move <= -0.15:
        return "bearish"
    return "neutral"


def label_from_raw_score(score: float) -> str:
    if score >= 16:
        return "bullish"
    if score <= -16:
        return "bearish"
    return "neutral"


def evaluate_weight_set(history: List[dict], stats: dict, weights: dict) -> Tuple[float, dict]:
    if len(history) < 8:
        return 0.0, {"accuracy": 0.0, "false_alarm": 0.0, "samples": 0}

    ordered = sorted(history, key=parse_snapshot_dt)
    total = 0
    correct = 0
    false_alarm = 0
    for idx in range(len(ordered) - 1):
        current = ordered[idx]
        next_item = ordered[idx + 1]
        target = infer_forward_label(current, next_item)
        if target is None:
            continue

        comps = extract_snapshot_components(current)
        normalized = {
            key: normalize_component(float(comps[key]), stats[key])
            for key in ["market", "news", "dart", "sector"]
        }
        raw = 25 * (
            normalized["market"] * weights["market"]
            + normalized["news"] * weights["news"]
            + normalized["dart"] * weights["dart"]
            + normalized["sector"] * weights["sector"]
        )
        predicted = label_from_raw_score(raw)
        total += 1
        if predicted == target:
            correct += 1
        if predicted != "neutral" and predicted != target:
            false_alarm += 1

    if total == 0:
        return 0.0, {"accuracy": 0.0, "false_alarm": 0.0, "samples": 0}

    accuracy = correct / total
    false_rate = false_alarm / total
    metric = accuracy - false_rate * 0.25
    return metric, {"accuracy": round(accuracy, 4), "false_alarm": round(false_rate, 4), "samples": total}


def calibrate_weights(history: List[dict], stats: dict) -> dict:
    default_weights = {"market": 0.35, "news": 0.2, "dart": 0.25, "sector": 0.2}
    if len(history) < 24:
        return {
            "weights": default_weights,
            "metric": 0.0,
            "accuracy": 0.0,
            "false_alarm": 0.0,
            "samples": 0,
            "mode": "default_short_history",
        }

    best_weights = default_weights
    best_metric, best_detail = evaluate_weight_set(history, stats, default_weights)

    grid = [0.15, 0.2, 0.25, 0.3, 0.35, 0.4]
    for market in grid:
        for news in grid:
            for dart in grid:
                sector = 1.0 - market - news - dart
                if sector < 0.1 or sector > 0.4:
                    continue
                weights = {
                    "market": round(market, 3),
                    "news": round(news, 3),
                    "dart": round(dart, 3),
                    "sector": round(sector, 3),
                }
                metric, detail = evaluate_weight_set(history, stats, weights)
                if metric > best_metric:
                    best_metric = metric
                    best_weights = weights
                    best_detail = detail

    return {
        "weights": best_weights,
        "metric": round(best_metric, 4),
        "accuracy": best_detail.get("accuracy", 0.0),
        "false_alarm": best_detail.get("false_alarm", 0.0),
        "samples": best_detail.get("samples", 0),
        "mode": "calibrated_grid_search",
    }


def build_sentiment(indexes: Dict[str, dict], news: List[dict], darts: List[dict], sector_rotation: dict, calibration: dict) -> dict:
    news_score = aggregate_event_score(news)
    dart_score = aggregate_event_score(darts, limit=10)
    market_score = market_reaction_score(indexes)
    sector_score = average([float(item.get("final_score", 0)) for item in sector_rotation.get("scores", [])[:3]])
    if sector_score != sector_score:
        sector_score = 0.0

    stats = calibration.get("stats", {})
    weights = calibration.get("weights", {"market": 0.35, "news": 0.2, "dart": 0.25, "sector": 0.2})
    normalized = {
        "market": normalize_component(market_score, stats.get("market", {})),
        "news": normalize_component(news_score, stats.get("news", {})),
        "dart": normalize_component(dart_score, stats.get("dart", {})),
        "sector": normalize_component(sector_score, stats.get("sector", {})),
    }

    total = 25 * (
        normalized["market"] * float(weights.get("market", 0.35))
        + normalized["news"] * float(weights.get("news", 0.2))
        + normalized["dart"] * float(weights.get("dart", 0.25))
        + normalized["sector"] * float(weights.get("sector", 0.2))
    )
    total = clamp(total, -100, 100)
    raw_label = raw_score_label(total)
    display_score = normalize_sentiment_score(total)
    display_meta = describe_display_score(display_score)
    data_quality = build_data_quality(indexes, news, darts, sector_rotation)
    score_breakdown = {
        "market": round(market_score, 2),
        "news": round(news_score, 2),
        "dart": round(dart_score, 2),
        "sector": round(sector_score, 2),
    }

    return {
        "raw_score": round(total, 2),
        "score": display_score,
        "raw_label": raw_label,
        "label": display_meta["label"],
        "range_key": display_meta["range_key"],
        "range_rule": display_meta["range_rule"],
        "interpretation": display_meta["interpretation"],
        "confidence": data_quality["score"],
        "market_score": round(market_score, 2),
        "news_score": round(news_score, 2),
        "dart_score": round(dart_score, 2),
        "sector_score": round(sector_score, 2),
        "normalized_components": normalized,
        "weights": weights,
        "model_version": calibration.get("model_version", "v2.0-calibrated"),
        "calibration_metric": calibration.get("metric", 0.0),
        "calibration_samples": calibration.get("samples", 0),
        "score_breakdown": score_breakdown,
        "tooltip_breakdown": format_score_breakdown(score_breakdown),
        "data_quality": data_quality,
        "confidence_tooltip": build_confidence_tooltip(data_quality["score"], data_quality),
    }


def find_previous_snapshot(current_dt: datetime, history: List[dict]) -> dict | None:
    target = current_dt - timedelta(days=1)
    target_slot = target.strftime("%H:%M")
    closest = None
    closest_gap = None

    for item in history:
        item_dt = parse_snapshot_dt(item)
        if item_dt.strftime("%H:%M") != target_slot:
            continue
        gap = abs((item_dt - target).total_seconds())
        if closest is None or closest_gap is None or gap < closest_gap:
            closest = item
            closest_gap = gap

    if closest is not None:
        return closest
    return history[0] if history else None


def build_day_over_day_comments(current_payload: dict, history: List[dict]) -> List[str]:
    if not history:
        return [
            "전일 동일 시간 데이터가 없어 **당일 흐름 중심**으로 판단합니다.",
            "리스크 지표의 절대 수준과 방향을 우선 확인하세요.",
            "이벤트 누적이 쌓이면 비교 코멘트 정확도가 높아집니다.",
        ]

    current_dt = parse_snapshot_dt(current_payload)
    previous = find_previous_snapshot(current_dt, history)
    if previous is None:
        return ["전일 비교 데이터 확인이 필요합니다."]

    current_sentiment = current_payload.get("sentiment", {}).get("score", 0)
    previous_sentiment = previous.get("sentiment", {}).get("score", 0)
    sentiment_delta = round(current_sentiment - previous_sentiment, 2)

    current_vix = parse_price(current_payload.get("market_signals", {}).get("vix", {}).get("price", "N/A"))
    previous_vix = parse_price(previous.get("market_signals", {}).get("vix", {}).get("price", "N/A"))
    vix_delta = round(current_vix - previous_vix, 2) if current_vix == current_vix and previous_vix == previous_vix else "N/A"

    current_usd = parse_price(current_payload.get("market_signals", {}).get("usdkrw", {}).get("price", "N/A"))
    previous_usd = parse_price(previous.get("market_signals", {}).get("usdkrw", {}).get("price", "N/A"))
    usd_delta = round(current_usd - previous_usd, 2) if current_usd == current_usd and previous_usd == previous_usd else "N/A"

    current_events = current_payload.get("events", {})
    previous_events = previous.get("events", {})
    event_delta = (current_events.get("news_count", 0) + current_events.get("dart_count", 0)) - (
        previous_events.get("news_count", 0) + previous_events.get("dart_count", 0)
    )

    return [
        f"전일 동시간 대비 **센티먼트 {sentiment_delta:+.2f}p** 변화로 현재 점수는 {current_sentiment}점입니다.",
        f"리스크 축은 **VIX {vix_delta if vix_delta != 'N/A' else 'N/A'}**, **달러원 {usd_delta if usd_delta != 'N/A' else 'N/A'}** 만큼 변동했습니다.",
        f"이벤트 발생량은 전일 대비 **{event_delta:+d}건** 차이로 뉴스/공시 민감도를 점검할 구간입니다.",
    ]


def detect_sector_rotation(news: List[dict], darts: List[dict]) -> dict:
    sector_map = {
        "반도체": {
            "keywords": ["반도체", "메모리", "하이닉스", "삼성전자", "hbm", "soxx"],
            "tickers": {"삼성전자": "005930.KS", "SK하이닉스": "000660.KS"},
        },
        "2차전지": {
            "keywords": ["2차전지", "배터리", "전기차", "양극재", "음극재", "리튬"],
            "tickers": {"LG에너지솔루션": "373220.KS", "삼성SDI": "006400.KS"},
        },
        "바이오": {
            "keywords": ["바이오", "제약", "임상", "신약", "의약품"],
            "tickers": {"삼성바이오로직스": "207940.KS", "셀트리온": "068270.KS"},
        },
        "금융": {
            "keywords": ["은행", "보험", "증권", "금융", "배당"],
            "tickers": {"KB금융": "105560.KS", "신한지주": "055550.KS"},
        },
        "에너지": {
            "keywords": ["유가", "정유", "가스", "에너지", "원유"],
            "tickers": {"SK이노베이션": "096770.KS", "S-Oil": "010950.KS"},
        },
        "방산": {
            "keywords": ["방산", "미사일", "천궁", "수출", "군수"],
            "tickers": {"한화에어로스페이스": "012450.KS", "한국항공우주": "047810.KS"},
        },
    }

    ticker_map = {}
    for meta in sector_map.values():
        ticker_map.update(meta["tickers"])
    price_signals = get_batch_index_data(ticker_map)

    scored = []
    for sector, meta in sector_map.items():
        event_score = 0.0
        mentions = 0
        positive_count = 0
        negative_count = 0
        matched_titles = []

        for event in [*news, *darts]:
            text = f"{event.get('title', '')} {event.get('corp_name', '')}".lower()
            if not any(keyword.lower() in text for keyword in meta["keywords"]):
                continue
            impact = float(event.get("impact_score", 0))
            mentions += 1
            event_score += impact
            if impact > 0:
                positive_count += 1
            elif impact < 0:
                negative_count += 1
            if len(matched_titles) < 3:
                matched_titles.append(event.get("title", ""))

        pct_moves = []
        reps = []
        for label, ticker in meta["tickers"].items():
            pct = parse_change_percent(price_signals.get(label, {}).get("change", ""))
            if pct == pct:
                pct_moves.append(pct)
                reps.append(f"{label} {pct:+.2f}%")

        price_confirmation = average(pct_moves)
        if price_confirmation != price_confirmation:
            price_confirmation = 0.0
        breadth = positive_count - negative_count
        final_score = event_score * 0.65 + price_confirmation * 1.6 + breadth * 0.8
        confidence = int(clamp(35 + min(mentions, 4) * 10 + (10 if pct_moves else 0), 35, 90))

        scored.append(
            {
                "sector": sector,
                "mentions": mentions,
                "event_score": round(event_score, 2),
                "price_confirmation": round(price_confirmation, 2),
                "breadth": breadth,
                "final_score": round(final_score, 2),
                "confidence": confidence,
                "price_confirmed": bool(pct_moves),
                "representatives": reps,
                "matched_titles": matched_titles,
            }
        )

    ordered = sorted(scored, key=lambda x: x["final_score"], reverse=True)
    strong = [item for item in ordered if item["final_score"] >= 1][:3]
    weak = [item for item in sorted(scored, key=lambda x: x["final_score"]) if item["final_score"] <= -1][:3]
    return {
        "scores": ordered,
        "strong": strong,
        "weak": weak,
        "basis": "이벤트 점수 + 대표 종목 수익률 확인 결합",
    }


def compute_reliability(history: List[dict]) -> dict:
    if len(history) < 6:
        return {
            "evaluated": 0,
            "hit_rate": "N/A",
            "false_alarm_rate": "N/A",
            "by_label": {"bullish": "N/A", "bearish": "N/A", "neutral": "N/A"},
            "basis": "표본 부족",
        }

    ordered = sorted(history, key=parse_snapshot_dt)
    evaluated = 0
    hit = 0
    false_alarm = 0
    label_stats = {
        "bullish": {"evaluated": 0, "hit": 0},
        "bearish": {"evaluated": 0, "hit": 0},
        "neutral": {"evaluated": 0, "hit": 0},
    }

    for i in range(len(ordered) - 1):
        current = ordered[i].get("sentiment", {})
        current_market = ordered[i].get("market_signals", {})
        next_market = ordered[i + 1].get("market_signals", {})
        current_label = current.get("raw_label", current.get("label", "neutral"))
        current_pct = average(
            [
                parse_change_percent(current_market.get("kospi", {}).get("change", "")),
                parse_change_percent(current_market.get("kosdaq", {}).get("change", "")),
            ]
        )
        next_pct = average(
            [
                parse_change_percent(next_market.get("kospi", {}).get("change", "")),
                parse_change_percent(next_market.get("kosdaq", {}).get("change", "")),
            ]
        )
        if current_pct != current_pct or next_pct != next_pct:
            continue
        forward_move = next_pct - current_pct
        evaluated += 1
        label_stats.setdefault(current_label, {"evaluated": 0, "hit": 0})
        label_stats[current_label]["evaluated"] += 1

        if current_label == "bullish":
            if forward_move >= 0.15:
                hit += 1
                label_stats[current_label]["hit"] += 1
            else:
                false_alarm += 1
        elif current_label == "bearish":
            if forward_move <= -0.15:
                hit += 1
                label_stats[current_label]["hit"] += 1
            else:
                false_alarm += 1
        else:
            if abs(forward_move) < 0.2:
                hit += 1
                label_stats[current_label]["hit"] += 1
            else:
                false_alarm += 1

    if evaluated == 0:
        return {
            "evaluated": 0,
            "hit_rate": "N/A",
            "false_alarm_rate": "N/A",
            "by_label": {"bullish": "N/A", "bearish": "N/A", "neutral": "N/A"},
            "basis": "후행 수익률 계산 불가",
        }

    by_label = {}
    for label, stats in label_stats.items():
        if stats["evaluated"] == 0:
            by_label[label] = "N/A"
            continue
        by_label[label] = f"{round((stats['hit'] / stats['evaluated']) * 100, 1)}%"

    hit_rate = round((hit / evaluated) * 100, 1)
    false_alarm_rate = round((false_alarm / evaluated) * 100, 1)
    return {
        "evaluated": evaluated,
        "hit_rate": f"{hit_rate}%",
        "false_alarm_rate": f"{false_alarm_rate}%",
        "by_label": by_label,
        "basis": "현재 시그널 이후 다음 슬롯의 KOSPI/KOSDAQ 변동률 개선 여부 기준",
    }


def build_timeline_heatmap(current_payload: dict, history: List[dict]) -> dict:
    merged = [current_payload, *history]
    recent = sorted(merged, key=parse_snapshot_dt)[-60:]
    slots = ["08:30", "09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]

    day_map: Dict[str, Dict[str, float]] = {}
    for item in recent:
        dt = parse_snapshot_dt(item)
        day_key = dt.strftime("%m-%d")
        slot_key = bucket_time_to_slot(dt, slots)
        if slot_key is None:
            continue
        existing = day_map.setdefault(day_key, {}).get(slot_key)
        score = get_snapshot_raw_score(item)
        if existing is None or abs(score) >= abs(existing):
            day_map.setdefault(day_key, {})[slot_key] = score

    days = sorted(day_map.keys())[-5:]
    rows = []
    for day in days:
        row = {"day": day, "scores": []}
        for slot in slots:
            score = day_map[day].get(slot)
            if score is None:
                row["scores"].append({"slot": slot, "text": "-", "color": "#f8fafc", "state": "missing"})
                continue
            display_score = normalize_sentiment_score(score)
            row["scores"].append(
                {
                    "slot": slot,
                    "text": f"{display_score:.0f}",
                    "color": display_score_color(display_score),
                    "state": describe_display_score(display_score)["range_key"],
                }
            )
        rows.append(row)

    timeline = [
        {
            "time": parse_snapshot_dt(item).strftime("%m-%d %H:%M"),
            "score": get_snapshot_display_score(item),
            "label": describe_display_score(get_snapshot_display_score(item))["label"],
            "color": display_score_color(get_snapshot_display_score(item)),
        }
        for item in recent[-12:]
    ]
    return {
        "slots": slots,
        "rows": rows,
        "timeline": timeline,
        "legend": [
            {"name": "강한 우호 80+", "color": "#fecaca"},
            {"name": "우호 60~79", "color": "#fee2e2"},
            {"name": "중립 40~59", "color": "#e2e8f0"},
            {"name": "경계 20~39", "color": "#dbeafe"},
            {"name": "강한 경계 0~19", "color": "#bfdbfe"},
            {"name": "결측", "color": "#f8fafc"},
        ],
    }


def build_rule_points(indexes: Dict[str, dict], sentiment: dict, news: List[dict], darts: List[dict]) -> Tuple[List[str], str]:
    breakdown = sentiment.get("score_breakdown", {})
    point1 = (
        f"하이브리드 점수는 **{sentiment['score']}점({sentiment['label']})**으로, "
        f"시장 {breakdown.get('market', 0)}, 뉴스 {breakdown.get('news', 0)}, 공시 {breakdown.get('dart', 0)}, 섹터확인 {breakdown.get('sector', 0)}를 반영했습니다."
    )
    point2 = (
        f"리스크 축은 **VIX {indexes['vix']['price']} ({indexes['vix']['change']})**, "
        f"**달러원 {indexes['usdkrw']['price']} ({indexes['usdkrw']['change']})** 흐름을 우선 점검하세요."
    )

    top_news = sorted(news, key=lambda x: abs(x.get("impact_score", 0)), reverse=True)[:1]
    top_dart = sorted(darts, key=lambda x: abs(x.get("impact_score", 0)), reverse=True)[:1]

    if top_news:
        point3 = f"주요 뉴스: **{top_news[0]['title']}**"
    elif top_dart:
        point3 = f"주요 공시: **{top_dart[0].get('corp_name', '')} {top_dart[0]['title']}**"
    else:
        point3 = "뉴스/공시 이벤트가 제한적이어서 **시장 수급 신호** 비중을 높여 판단하세요."

    watchpoint = (
        "오늘의 **핵심 관전 포인트**: "
        f"**VIX**와 **달러원**이 동반 상승하면 방어 비중을 늘리고, "
        "두 지표가 안정되면 가격 확인이 붙는 주도 섹터 눌림 구간을 분할 점검하세요."
    )
    return [point1, point2, point3], watchpoint


def build_llm_points(indexes: Dict[str, dict], sentiment: dict, news: List[dict], darts: List[dict]) -> Tuple[List[str], str]:
    if not OPENAI_API_KEY:
        return build_rule_points(indexes, sentiment, news, darts)

    client = OpenAI(api_key=OPENAI_API_KEY)
    top_news = sorted(news, key=lambda x: abs(x.get("impact_score", 0)), reverse=True)[:3]
    top_dart = sorted(darts, key=lambda x: abs(x.get("impact_score", 0)), reverse=True)[:3]

    prompt = (
        "당신은 20년차 시장 애널리스트입니다. 아래 데이터를 바탕으로 장중 브리핑 3줄과 마지막 관전포인트 1줄을 작성하세요.\n"
        "- 모든 문장은 근거 기반으로 작성\n"
        "- 핵심 키워드에 **굵게** 표시\n"
        "- 마지막 줄은 반드시 '오늘의 핵심 관전 포인트:'로 시작\n"
        f"- sentiment: {sentiment}\n"
        f"- indexes: {indexes}\n"
        f"- top_news: {top_news}\n"
        f"- top_dart: {top_dart}\n\n"
        "출력 형식:\n"
        "- ...\n- ...\n- ...\n오늘의 핵심 관전 포인트: ..."
    )

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (res.choices[0].message.content or "").strip()
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        points = [line.lstrip("-").strip() for line in lines if line.startswith("-")][:3]
        watchpoint = next((line for line in lines if line.startswith("오늘의 핵심 관전 포인트")), "")
        if len(points) < 3 or not watchpoint:
            return build_rule_points(indexes, sentiment, news, darts)
        return points, watchpoint
    except Exception:
        return build_rule_points(indexes, sentiment, news, darts)


def save_intraday_snapshot(
    indexes: Dict[str, dict],
    news: List[dict],
    darts: List[dict],
    sentiment: dict,
    points: List[str],
    watchpoint: str,
    day_over_day: List[str],
    sector_rotation: dict,
    reliability: dict,
    heatmap: dict,
    calibration: dict,
) -> dict:
    now = datetime.now(KST)
    stamp = now.strftime("%Y-%m-%d-%H%M")
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    hour_end = hour_start.replace(minute=59, second=59)

    payload = {
        "schema_version": "1.2",
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "window_start": hour_start.strftime("%Y-%m-%d %H:%M:%S"),
        "window_end": hour_end.strftime("%Y-%m-%d %H:%M:%S"),
        "run_source": os.getenv("GITHUB_EVENT_NAME", "local"),
        "market_signals": indexes,
        "events": {
            "news": news,
            "dart": darts,
            "news_count": len(news),
            "dart_count": len(darts),
        },
        "sentiment": sentiment,
        "key_points": points,
        "watchpoint": watchpoint,
        "day_over_day": day_over_day,
        "sector_rotation": sector_rotation,
        "reliability": reliability,
        "heatmap": heatmap,
        "calibration": calibration,
        "validation_basis": reliability.get("basis", ""),
    }

    (INTRADAY_DATA_DIR / f"{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (INTRADAY_DATA_DIR / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def send_discord_intraday(payload: dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        return

    sentiment = payload["sentiment"]
    signals = payload["market_signals"]

    def idx(name: str) -> str:
        item = signals.get(name, {})
        return f"{item.get('price', 'N/A')} ({item.get('change', '-')})"

    summary = payload.get("key_points", [])
    description = "\n".join(summary[:2])
    watchpoint = payload.get("watchpoint", "")
    if watchpoint:
        description = f"{description}\n\n{watchpoint}"

    embed = {
        "title": f"⏱️ 장중 시장 분위기 {sentiment.get('label', '중립')} ({sentiment.get('score', 50)}점)",
        "description": description,
        "color": 5763714,
        "url": resolve_pages_url(),
        "fields": [
            {"name": "KOSPI", "value": idx("kospi"), "inline": True},
            {"name": "KOSDAQ", "value": idx("kosdaq"), "inline": True},
            {"name": "S&P 500", "value": idx("sp500"), "inline": True},
            {"name": "NASDAQ", "value": idx("nasdaq"), "inline": True},
            {"name": "VIX", "value": idx("vix"), "inline": True},
            {"name": "USD/KRW", "value": idx("usdkrw"), "inline": True},
        ],
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
    except Exception:
        pass


def render_live_html(payload: dict) -> None:
    env = Environment(loader=FileSystemLoader(str(BASE_DIR)))
    env.filters["bold"] = bold_filter
    template = env.get_template("template_live.html")

    sentiment = payload.get("sentiment", {})
    sentiment_view = {
        "label": sentiment.get("label", "중립"),
        "score": sentiment.get("score", 50),
        "raw_score": sentiment.get("raw_score", 0),
        "confidence": sentiment.get("confidence", 0),
        "breakdown": sentiment.get("score_breakdown", {"market": 0, "news": 0, "dart": 0, "sector": 0}),
        "data_quality_basis": sentiment.get("data_quality", {}).get("basis", ""),
        "range_rule": sentiment.get("range_rule", "40~59.9는 중립 구간"),
        "interpretation": sentiment.get("interpretation", "상승·하락 신호가 혼재한 상태입니다."),
        "tooltip_breakdown": sentiment.get("tooltip_breakdown", "시장 0 / 뉴스 0 / 공시 0 / 섹터 0"),
        "confidence_tooltip": sentiment.get("confidence_tooltip", []),
    }

    news_events = sorted(
        payload.get("events", {}).get("news", []),
        key=lambda x: abs(x.get("impact_score", 0)),
        reverse=True,
    )[:8]
    dart_events = sorted(
        payload.get("events", {}).get("dart", []),
        key=lambda x: abs(x.get("impact_score", 0)),
        reverse=True,
    )[:8]

    html = template.render(
        generated_at=payload.get("timestamp", ""),
        window_start=payload.get("window_start", ""),
        window_end=payload.get("window_end", ""),
        sentiment=sentiment_view,
        market_overview=build_market_overview(payload.get("market_signals", {})),
        market=payload.get("market_signals", {}),
        key_points=payload.get("key_points", []),
        watchpoint=payload.get("watchpoint", ""),
        day_over_day=payload.get("day_over_day", []),
        sector_rotation=payload.get("sector_rotation", {}),
        reliability=payload.get("reliability", {}),
        heatmap=payload.get("heatmap", {}),
        news_events=news_events,
        dart_events=dart_events,
        pages_url=resolve_pages_url(),
    )
    (OUTPUT_DIR / "live.html").write_text(html, encoding="utf-8")


def main() -> None:
    history = load_intraday_history(limit=400)
    stats = build_component_stats(history)
    calibration_result = calibrate_weights(history, stats)
    calibration = {
        "stats": stats,
        "weights": calibration_result.get("weights", {"market": 0.35, "news": 0.2, "dart": 0.25, "sector": 0.2}),
        "metric": calibration_result.get("metric", 0.0),
        "samples": calibration_result.get("samples", 0),
        "model_version": "v2.0-calibrated",
        "mode": calibration_result.get("mode", "default"),
    }

    indexes = fetch_market_signals()
    news_events = score_news_events(fetch_naver_news(limit=20))
    dart_events = score_dart_events(fetch_dart_events(limit=40))
    sector_rotation = detect_sector_rotation(news_events, dart_events)
    sentiment = build_sentiment(indexes, news_events, dart_events, sector_rotation, calibration)
    points, watchpoint = build_llm_points(indexes, sentiment, news_events, dart_events)
    preview_payload = {
        "timestamp": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "market_signals": indexes,
        "events": {"news": news_events, "dart": dart_events, "news_count": len(news_events), "dart_count": len(dart_events)},
        "sentiment": sentiment,
    }
    day_over_day = build_day_over_day_comments(preview_payload, history)
    reliability = compute_reliability(history)
    heatmap = build_timeline_heatmap(preview_payload, history)

    payload = save_intraday_snapshot(
        indexes,
        news_events,
        dart_events,
        sentiment,
        points,
        watchpoint,
        day_over_day,
        sector_rotation,
        reliability,
        heatmap,
        calibration,
    )
    render_live_html(payload)
    send_discord_intraday(payload)
    print("Generated:", INTRADAY_DATA_DIR / "latest.json")


if __name__ == "__main__":
    main()
