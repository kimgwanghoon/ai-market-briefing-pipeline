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

from main import get_index_data, get_korean_index_data, resolve_pages_url

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
        "최대": 2,
        "수주": 3,
        "흑자": 2,
        "상향": 2,
        "반등": 1,
        "매수": 1,
        "성장": 2,
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
    }
    negative = {
        "유상증자": 4,
        "전환사채": 3,
        "신주인수권부사채": 3,
        "감사의견": 3,
        "불성실": 3,
        "정정": 1,
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
    score = 0.0
    for key in ["kospi", "kosdaq", "sp500", "dow", "nasdaq", "ewy"]:
        trend = indexes.get(key, {}).get("trend")
        if trend == "상승":
            score += 2
        elif trend == "하락":
            score -= 2

    vix_trend = indexes.get("vix", {}).get("trend")
    usd_trend = indexes.get("usdkrw", {}).get("trend")
    rate_trend = indexes.get("us10y", {}).get("trend")

    if vix_trend == "상승":
        score -= 2
    elif vix_trend == "하락":
        score += 1

    if usd_trend == "상승":
        score -= 2
    elif usd_trend == "하락":
        score += 1

    if rate_trend == "상승":
        score -= 1
    elif rate_trend == "하락":
        score += 1

    return score


def build_sentiment(indexes: Dict[str, dict], news: List[dict], darts: List[dict]) -> dict:
    news_score = sum(event.get("impact_score", 0) for event in news)
    dart_score = sum(event.get("impact_score", 0) for event in darts)
    market_score = market_reaction_score(indexes)

    total = market_score * 3 + news_score * 0.7 + dart_score * 1.0
    total = clamp(total, -100, 100)

    if total >= 18:
        label = "bullish"
    elif total <= -18:
        label = "bearish"
    else:
        label = "neutral"

    confidence = int(clamp(45 + abs(total) * 0.7, 35, 95))
    return {
        "score": round(total, 2),
        "label": label,
        "confidence": confidence,
        "market_score": round(market_score, 2),
        "news_score": round(news_score, 2),
        "dart_score": round(dart_score, 2),
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
        if closest is None or gap < closest_gap:
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
    sector_keywords = {
        "반도체": ["반도체", "메모리", "하이닉스", "삼성전자", "soxx"],
        "2차전지": ["2차전지", "배터리", "전기차", "양극재", "음극재"],
        "바이오": ["바이오", "제약", "임상", "신약"],
        "금융": ["은행", "보험", "증권", "금융", "배당"],
        "에너지": ["유가", "정유", "가스", "에너지"],
        "방산": ["방산", "미사일", "천궁", "수출"],
    }

    sector_scores = {name: 0 for name in sector_keywords}
    for event in [*news, *darts]:
        text = f"{event.get('title', '')} {event.get('corp_name', '')}".lower()
        impact = float(event.get("impact_score", 0))
        for sector, keywords in sector_keywords.items():
            if any(keyword.lower() in text for keyword in keywords):
                sector_scores[sector] += impact if impact != 0 else 0.5

    ordered = sorted(sector_scores.items(), key=lambda x: x[1], reverse=True)
    strong = [{"sector": name, "score": round(score, 2)} for name, score in ordered[:2]]
    weak = [{"sector": name, "score": round(score, 2)} for name, score in ordered[-2:]]
    return {
        "scores": [{"sector": name, "score": round(score, 2)} for name, score in ordered],
        "strong": strong,
        "weak": weak,
    }


def compute_reliability(history: List[dict]) -> dict:
    if len(history) < 6:
        return {"evaluated": 0, "hit_rate": "N/A", "false_alarm_rate": "N/A"}

    ordered = sorted(history, key=parse_snapshot_dt)
    evaluated = 0
    hit = 0
    false_alarm = 0

    for i in range(len(ordered) - 1):
        current = ordered[i].get("sentiment", {})
        next_item = ordered[i + 1].get("sentiment", {})
        current_label = current.get("label", "neutral")
        next_score = float(next_item.get("score", 0))
        evaluated += 1

        if current_label == "bullish":
            if next_score >= 0:
                hit += 1
            else:
                false_alarm += 1
        elif current_label == "bearish":
            if next_score <= 0:
                hit += 1
            else:
                false_alarm += 1
        else:
            if -10 <= next_score <= 10:
                hit += 1

    if evaluated == 0:
        return {"evaluated": 0, "hit_rate": "N/A", "false_alarm_rate": "N/A"}

    hit_rate = round((hit / evaluated) * 100, 1)
    false_alarm_rate = round((false_alarm / evaluated) * 100, 1)
    return {"evaluated": evaluated, "hit_rate": f"{hit_rate}%", "false_alarm_rate": f"{false_alarm_rate}%"}


def build_timeline_heatmap(current_payload: dict, history: List[dict]) -> dict:
    merged = [current_payload, *history]
    recent = sorted(merged, key=parse_snapshot_dt)[-40:]
    slots = ["08:30", "09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]

    day_map: Dict[str, Dict[str, float]] = {}
    for item in recent:
        dt = parse_snapshot_dt(item)
        day_key = dt.strftime("%m-%d")
        slot_key = dt.strftime("%H:%M")
        if slot_key not in slots:
            continue
        day_map.setdefault(day_key, {})[slot_key] = float(item.get("sentiment", {}).get("score", 0))

    days = sorted(day_map.keys())[-5:]
    rows = []
    for day in days:
        row = {"day": day, "scores": []}
        for slot in slots:
            score = day_map[day].get(slot)
            if score is None:
                row["scores"].append({"slot": slot, "text": "-", "color": "#e2e8f0"})
                continue
            if score >= 18:
                color = "#fecaca"
            elif score <= -18:
                color = "#bfdbfe"
            else:
                color = "#e2e8f0"
            row["scores"].append({"slot": slot, "text": f"{score:.0f}", "color": color})
        rows.append(row)

    timeline = [
        {
            "time": parse_snapshot_dt(item).strftime("%m-%d %H:%M"),
            "score": float(item.get("sentiment", {}).get("score", 0)),
        }
        for item in recent[-12:]
    ]
    return {"slots": slots, "rows": rows, "timeline": timeline}


def build_rule_points(indexes: Dict[str, dict], sentiment: dict, news: List[dict], darts: List[dict]) -> Tuple[List[str], str]:
    label_kr = {"bullish": "우호", "neutral": "중립", "bearish": "경계"}[sentiment["label"]]
    point1 = (
        f"하이브리드 점수는 **{sentiment['score']}점({label_kr})**으로, "
        f"시장 반응 점수 {sentiment['market_score']}와 이벤트 점수 {sentiment['news_score'] + sentiment['dart_score']:.1f}를 반영했습니다."
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
        "두 지표가 안정되면 주도 섹터 눌림 구간을 분할 점검하세요."
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
        raw = res.choices[0].message.content.strip()
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
) -> dict:
    now = datetime.now(KST)
    stamp = now.strftime("%Y-%m-%d-%H%M")
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    hour_end = hour_start.replace(minute=59, second=59)

    payload = {
        "schema_version": "1.1",
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
    label_map = {"bullish": "우호", "neutral": "중립", "bearish": "경계"}

    def idx(name: str) -> str:
        item = signals.get(name, {})
        return f"{item.get('price', 'N/A')} ({item.get('change', '-')})"

    summary = payload.get("key_points", [])
    description = "\n".join(summary[:2])
    watchpoint = payload.get("watchpoint", "")
    if watchpoint:
        description = f"{description}\n\n{watchpoint}"

    embed = {
        "title": f"⏱️ 장중 시장 분위기 {label_map.get(sentiment['label'], '중립')} ({sentiment['score']}점)",
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
    label_map = {"bullish": "우호", "neutral": "중립", "bearish": "경계"}
    sentiment_view = {
        "label": label_map.get(sentiment.get("label", "neutral"), "중립"),
        "score": sentiment.get("score", 0),
        "confidence": sentiment.get("confidence", 0),
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
    indexes = fetch_market_signals()
    news_events = score_news_events(fetch_naver_news(limit=20))
    dart_events = score_dart_events(fetch_dart_events(limit=40))
    sentiment = build_sentiment(indexes, news_events, dart_events)
    points, watchpoint = build_llm_points(indexes, sentiment, news_events, dart_events)
    preview_payload = {
        "timestamp": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "market_signals": indexes,
        "events": {"news": news_events, "dart": dart_events, "news_count": len(news_events), "dart_count": len(dart_events)},
        "sentiment": sentiment,
    }
    day_over_day = build_day_over_day_comments(preview_payload, history)
    sector_rotation = detect_sector_rotation(news_events, dart_events)
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
    )
    render_live_html(payload)
    send_discord_intraday(payload)
    print("Generated:", INTRADAY_DATA_DIR / "latest.json")


if __name__ == "__main__":
    main()
