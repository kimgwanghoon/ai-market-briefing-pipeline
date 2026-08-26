import logging
import os
import re
import json
import base64
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

import pytz
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from openai import OpenAI

from ai_generation import (
    DAILY_BRIEFING_SCHEMA,
    create_structured_completion,
    has_only_grounded_numbers,
    render_grounded_claim,
    validate_grounded_claims,
)
from discord_notifications import build_daily_embed, post_discord_webhook

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "public"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = OUTPUT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.getenv("AI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GITHUB_PAGES_URL = os.getenv("GITHUB_PAGES_URL", "")
UP_COLOR = "#b91c1c"
DOWN_COLOR = "#1d4ed8"
NEUTRAL_COLOR = "#4b5563"


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


GENERATE_AI_IMAGE = env_flag("GENERATE_AI_IMAGE", default=True)
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1-mini").strip() or "gpt-image-1-mini"


def resolve_pages_url() -> str:
    custom = GITHUB_PAGES_URL.strip()
    if custom:
        return custom

    owner = os.getenv("GITHUB_REPOSITORY_OWNER", "").strip().lower()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    repo_name = repository.split("/")[-1].strip() if repository else ""

    if owner and repo_name:
        return f"https://{owner}.github.io/{repo_name}"
    return "https://github.com"

KST = pytz.timezone("Asia/Seoul")
CURRENT_TIME_STR = ""
IS_MORNING = True
EDITION_TITLE = ""


def format_quote_time(value: object) -> str:
    if value in (None, "", 0):
        return "기준시각 확인 필요"
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            parsed = datetime.fromtimestamp(int(value), tz=pytz.UTC).astimezone(KST)
        elif hasattr(value, "to_pydatetime"):
            parsed = value.to_pydatetime()
            if parsed.tzinfo is None:
                parsed = KST.localize(parsed)
            parsed = parsed.astimezone(KST)
        else:
            text = str(value).strip().replace("T", " ").replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = KST.localize(parsed)
            parsed = parsed.astimezone(KST)
        return parsed.strftime("%m-%d %H:%M KST")
    except (TypeError, ValueError, OSError):
        return str(value).replace("T", " ")[:16] or "기준시각 확인 필요"


def market_status_label(raw_state: object, fallback: str) -> str:
    state = str(raw_state or "").strip().upper()
    mapping = {
        "OPEN": "장중",
        "REGULAR": "장중",
        "PRE": "장전",
        "PREPRE": "장전",
        "POST": "장후",
        "POSTPOST": "장후",
        "CLOSE": fallback,
        "CLOSED": fallback,
    }
    return mapping.get(state, fallback)


def yahoo_market_status(meta: dict, ticker: str) -> str:
    fallback = "최근 환율" if ticker == "KRW=X" else "최근 종가"
    explicit = market_status_label(meta.get("marketState"), "")
    if explicit:
        return explicit
    regular = meta.get("currentTradingPeriod", {}).get("regular", {})
    try:
        now_epoch = int(datetime.now(tz=pytz.UTC).timestamp())
        if int(regular.get("start", 0)) <= now_epoch <= int(regular.get("end", 0)):
            return "장중" if ticker != "KRW=X" else "환율 거래중"
    except (TypeError, ValueError):
        pass
    return fallback


def bold_filter(text: str) -> Markup:
    escaped = escape(str(text))
    return Markup(re.sub(r"\*+([^*]+)\*+", r"<strong>\1</strong>", str(escaped)))


def get_korean_index_data(market_type: str) -> dict:
    url = f"https://m.stock.naver.com/api/index/{market_type}/basic"
    headers = {"User-Agent": "Mozilla/5.0"}
    default = {
        "price": "N/A",
        "change": "-",
        "color": NEUTRAL_COLOR,
        "trend": "보합",
        "market_status": "데이터 없음",
        "as_of": "기준시각 확인 필요",
    }

    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()

        price = data["closePrice"]
        diff = data["compareToPreviousClosePrice"]
        ratio = float(data["fluctuationsRatio"])
        trend_code = data["compareToPreviousPrice"]["code"]

        if trend_code in ["1", "2"]:
            color, sign, trend = UP_COLOR, "▲", "상승"
        elif trend_code in ["4", "5"]:
            color, sign, trend = DOWN_COLOR, "▼", "하락"
        else:
            color, sign, trend = NEUTRAL_COLOR, "-", "보합"

        return {
            "price": price,
            "change": f"{sign} {str(diff).replace('-', '')} ({ratio:+.2f}%)",
            "color": color,
            "trend": trend,
            "market_status": market_status_label(data.get("marketStatus"), "최근 종가"),
            "as_of": format_quote_time(data.get("localTradedAt") or data.get("tradeDateTime")),
        }
    except Exception as exc:
        logging.warning("get_korean_index_data(%s) failed: %s", market_type, exc)
        return default


def get_index_data(ticker: str) -> dict:
    default = {
        "price": "N/A",
        "change": "-",
        "color": NEUTRAL_COLOR,
        "trend": "보합",
        "market_status": "데이터 없음",
        "as_of": "기준시각 확인 필요",
    }

    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        res = requests.get(
            url,
            params={"range": "7d", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        res.raise_for_status()
        chart = res.json().get("chart", {})
        result = chart.get("result") or []
        if result:
            meta = result[0].get("meta", {})
            closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
            closes = [float(v) for v in closes if v is not None]
            if len(closes) >= 2:
                today_close = closes[-1]
                yesterday_close = closes[-2]
                diff = today_close - yesterday_close
                pct_change = (diff / yesterday_close) * 100

                if diff > 0:
                    color, sign, trend = UP_COLOR, "▲", "상승"
                elif diff < 0:
                    color, sign, trend = DOWN_COLOR, "▼", "하락"
                else:
                    color, sign, trend = NEUTRAL_COLOR, "-", "보합"

                return {
                    "price": f"{today_close:,.2f}",
                    "change": f"{sign} {abs(diff):.2f} ({pct_change:+.2f}%)",
                    "color": color,
                    "trend": trend,
                    "market_status": yahoo_market_status(meta, ticker),
                    "as_of": format_quote_time(meta.get("regularMarketTime")),
                }
    except Exception as exc:
        logging.warning("get_index_data(%s) Yahoo Finance API failed: %s", ticker, exc)

    for wait_seconds in (0, 1, 2):
        if wait_seconds:
            time.sleep(wait_seconds)
        try:
            data = yf.Ticker(ticker).history(period="7d")
            close = data.get("Close")
            if close is None:
                continue
            close = close.dropna()
            if len(close) < 2:
                continue

            today_close = float(close.iloc[-1])
            yesterday_close = float(close.iloc[-2])
            diff = today_close - yesterday_close
            pct_change = (diff / yesterday_close) * 100

            if diff > 0:
                color, sign, trend = "#ef4444", "▲", "상승"
            elif diff < 0:
                color, sign, trend = "#3b82f6", "▼", "하락"
            else:
                color, sign, trend = "#6b7280", "-", "보합"

            return {
                "price": f"{today_close:,.2f}",
                "change": f"{sign} {abs(diff):.2f} ({pct_change:+.2f}%)",
                "color": color,
                "trend": trend,
                "market_status": "최근 환율" if ticker == "KRW=X" else "최근 종가",
                "as_of": format_quote_time(close.index[-1]),
            }
        except Exception as exc:
            logging.warning("get_index_data(%s) yfinance retry failed: %s", ticker, exc)
            continue

    return default


def get_batch_index_data(ticker_map: dict) -> dict:
    default = {"price": "N/A", "change": "-", "color": NEUTRAL_COLOR, "trend": "보합"}
    results = {key: default.copy() for key in ticker_map}

    symbols = list(ticker_map.values())
    if not symbols:
        return results

    try:
        data = yf.download(
            tickers=" ".join(symbols),
            period="7d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=12,
        )
        if data.empty:
            return results

        for key, ticker in ticker_map.items():
            try:
                close = data[ticker]["Close"].dropna()
            except Exception:
                continue

            if len(close) < 2:
                continue

            today_close = float(close.iloc[-1])
            yesterday_close = float(close.iloc[-2])
            diff = today_close - yesterday_close
            pct_change = (diff / yesterday_close) * 100

            if diff > 0:
                color, sign, trend = UP_COLOR, "▲", "상승"
            elif diff < 0:
                color, sign, trend = DOWN_COLOR, "▼", "하락"
            else:
                color, sign, trend = NEUTRAL_COLOR, "-", "보합"

            results[key] = {
                "price": f"{today_close:,.2f}",
                "change": f"{sign} {abs(diff):.2f} ({pct_change:+.2f}%)",
                "color": color,
                "trend": trend,
            }
    except Exception:
        return results

    return results


def build_watchpoint_line(vix: dict, usdkrw: dict, us10y: dict) -> str:
    if all(item.get("price") == "N/A" for item in (vix, usdkrw, us10y)):
        return (
            "오늘의 **핵심 관전 포인트**: 시장 데이터 수집이 복구되면 **VIX**와 **달러원**의 방향을 확인하고, "
            "복구되지 않으면 수치 해석을 보류하세요."
        )
    return (
        "오늘의 **핵심 관전 포인트**: "
        f"**VIX**({vix['price']})가 반등하면 변동성 확대 여부를 확인하고, "
        f"**미10년물**({us10y['price']})과 **달러원**({usdkrw['price']})이 동반 상승하면 "
        "금리·환율 부담이 이어지는지 추가 지표로 확인하세요."
    )


def build_fallback_section_items(
    kospi: dict,
    kosdaq: dict,
    sp500: dict,
    dow: dict,
    nasdaq: dict,
    ewy: dict,
    vix: dict,
    usdkrw: dict,
    us10y: dict,
    wti: dict,
) -> Tuple[List[str], List[str]]:
    korea_items = [
        f"**코스피** {kospi['price']} ({kospi['change']}), **코스닥** {kosdaq['price']} ({kosdaq['change']})로 집계됐으며 결측값은 확인이 필요합니다.",
        f"**EWY** {ewy['price']} ({ewy['change']})와 **달러원** {usdkrw['price']} ({usdkrw['change']})의 관측 방향을 함께 확인하세요.",
        "수급과 거래대금 데이터는 제공되지 않았으므로 **지수 관측값** 이상의 원인 해석은 보류합니다.",
    ]
    us_items = [
        f"**S&P500** {sp500['price']} ({sp500['change']}), **다우** {dow['price']} ({dow['change']}), **나스닥** {nasdaq['price']} ({nasdaq['change']})로 집계됐습니다.",
        f"**VIX** {vix['price']} ({vix['change']}), **미10년물** {us10y['price']} ({us10y['change']}), **WTI** {wti['price']} ({wti['change']})의 동행 여부를 확인하세요.",
        build_watchpoint_line(vix, usdkrw, us10y),
    ]
    return korea_items, us_items


def generate_cover_svg(path: Path, title: str) -> None:
    safe_title = (
        title.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    svg = f"""<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"1200\" height=\"630\" viewBox=\"0 0 1200 630\">
<defs>
  <linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"1\">
    <stop offset=\"0%\" stop-color=\"#f8fafc\"/>
    <stop offset=\"100%\" stop-color=\"#e2e8f0\"/>
  </linearGradient>
</defs>
<rect width=\"1200\" height=\"630\" fill=\"url(#bg)\"/>
<circle cx=\"170\" cy=\"110\" r=\"120\" fill=\"#dbeafe\"/>
<circle cx=\"1120\" cy=\"560\" r=\"160\" fill=\"#fee2e2\"/>
<text x=\"80\" y=\"260\" font-family=\"Pretendard, sans-serif\" font-size=\"46\" font-weight=\"700\" fill=\"#0f172a\">Daily Market Briefing</text>
<text x=\"80\" y=\"330\" font-family=\"Pretendard, sans-serif\" font-size=\"30\" font-weight=\"600\" fill=\"#1e293b\">{safe_title}</text>
<text x=\"80\" y=\"390\" font-family=\"Pretendard, sans-serif\" font-size=\"24\" fill=\"#334155\">Generated fallback cover</text>
</svg>
"""
    path.write_text(svg, encoding="utf-8")


def _clean_old_covers() -> None:
    for f in OUTPUT_DIR.glob("cover_*.png"):
        f.unlink(missing_ok=True)
    for f in OUTPUT_DIR.glob("cover.png"):
        f.unlink(missing_ok=True)


def get_existing_cover_file() -> str:
    pngs = sorted(OUTPUT_DIR.glob("cover_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    if pngs:
        return pngs[0].name
    if (OUTPUT_DIR / "cover.png").exists():
        return "cover.png"
    if (OUTPUT_DIR / "cover.svg").exists():
        return "cover.svg"
    return ""


def detect_run_source() -> str:
    event_name = os.getenv("GITHUB_EVENT_NAME", "").strip().lower()
    if event_name in {"push", "schedule", "workflow_dispatch"}:
        return event_name
    return "local"


def parse_price_value(value: str) -> float:
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


def build_market_headline(kospi: dict, nasdaq: dict, vix: dict) -> str:
    kospi_change = parse_change_percent(kospi.get("change", ""))
    nasdaq_change = parse_change_percent(nasdaq.get("change", ""))
    vix_value = parse_price_value(vix.get("price", ""))

    if kospi_change == kospi_change and nasdaq_change == nasdaq_change:
        if kospi_change > 0.5 and nasdaq_change > 0.5:
            return "코스피·나스닥 동반 강세"
        if kospi_change < -0.5 and nasdaq_change < -0.5:
            return "한미 증시 동반 약세"
        if kospi_change * nasdaq_change < 0:
            return "코스피·나스닥 엇갈린 온도차"
    if vix_value == vix_value and vix_value > 25:
        return "변동성 경계 속 지수 방향 점검"
    return "지수 혼조 속 금리·환율 점검"


def build_market_cover_prompt(
    headline: str,
    kospi: dict,
    kosdaq: dict,
    sp500: dict,
    nasdaq: dict,
    vix: dict,
    usdkrw: dict,
    us10y: dict,
    is_morning: bool,
) -> str:
    changes = {
        "KOSPI": parse_change_percent(kospi.get("change", "")),
        "KOSDAQ": parse_change_percent(kosdaq.get("change", "")),
        "S&P 500": parse_change_percent(sp500.get("change", "")),
        "NASDAQ": parse_change_percent(nasdaq.get("change", "")),
        "VIX change": parse_change_percent(vix.get("change", "")),
        "USD/KRW": parse_change_percent(usdkrw.get("change", "")),
        "US 10Y": parse_change_percent(us10y.get("change", "")),
    }
    vix_level = parse_price_value(vix.get("price", ""))

    def mean_for(*keys: str) -> float:
        values = [changes[key] for key in keys if changes[key] == changes[key]]
        return sum(values) / len(values) if values else float("nan")

    domestic = mean_for("KOSPI", "KOSDAQ")
    global_market = mean_for("S&P 500", "NASDAQ")
    vix_change = changes["VIX change"]
    fx_change = changes["USD/KRW"]
    yield_change = changes["US 10Y"]

    if (vix_level == vix_level and vix_level >= 25) or (vix_change == vix_change and vix_change >= 8):
        scenario = "elevated volatility and defensive positioning"
        composition = (
            "a dense field of translucent planes bent by crosswinds, with a narrow but intact path of light "
            "through the center; use asymmetric depth and visible tension"
        )
        palette = "charcoal navy, cold steel blue, restrained crimson, sparse white light"
        mood = "alert and defensive without panic or disaster imagery"
    elif domestic == domestic and global_market == global_market and domestic > 0.4 and global_market > 0.25:
        scenario = "broad risk-on participation across Korean and US equities"
        composition = (
            "an expansive rising corridor of layered light and open geometric planes accelerating toward a "
            "bright horizon; emphasize breadth, lift and available space"
        )
        palette = "deep navy, luminous teal, restrained emerald, warm gold highlights"
        mood = "confident and energetic with controlled optimism"
    elif domestic == domestic and global_market == global_market and domestic < -0.4 and global_market < -0.25:
        scenario = "broad risk-off pressure across Korean and US equities"
        composition = (
            "compressed descending planes, receding light and a guarded central anchor; create downward weight "
            "without literal arrows, charts or crash imagery"
        )
        palette = "deep navy, muted crimson, cool grey, narrow silver highlights"
        mood = "serious, cautious and tightly controlled"
    elif domestic == domestic and global_market == global_market and domestic - global_market > 0.8:
        scenario = "Korean equities outperforming a weaker US backdrop"
        composition = (
            "a clearly divided visual field: a warmer rising current inspired by Seoul on one side and a cooler, "
            "subdued global current on the other, meeting at an off-center boundary"
        )
        palette = "warm teal and gold contrasted with cool navy and desaturated blue"
        mood = "constructive locally but globally unresolved"
    elif domestic == domestic and global_market == global_market and global_market - domestic > 0.8:
        scenario = "US equities outperforming a weaker Korean backdrop"
        composition = (
            "a split-depth scene with a bright distant global current advancing while the nearer Seoul-inspired "
            "field remains subdued and compressed"
        )
        palette = "bright cyan and gold in the distance, slate blue and muted red in the foreground"
        mood = "globally constructive with local caution"
    elif ((fx_change == fx_change and fx_change > 0.5) or (yield_change == yield_change and yield_change > 0.5)):
        scenario = "equities facing tighter rate or currency conditions"
        composition = (
            "converging metallic arcs tightening around an illuminated core, balanced by a distant open exit; "
            "suggest financial pressure through spacing and material tension"
        )
        palette = "midnight blue, graphite, muted amber and restrained copper"
        mood = "analytical, constrained and watchful"
    else:
        scenario = "mixed sideways markets with no dominant directional consensus"
        composition = (
            "two opposing translucent currents meeting at a quiet central horizon, with suspended planes and "
            "balanced negative space that communicate unresolved direction"
        )
        palette = "deep navy, slate blue, muted teal and subtle amber"
        mood = "calm, analytical and undecided"

    valid_drivers = {key: value for key, value in changes.items() if value == value}
    dominant_driver = max(valid_drivers, key=lambda key: abs(valid_drivers[key])) if valid_drivers else "mixed signals"

    def format_signal(key: str) -> str:
        value = changes[key]
        return f"{value:+.2f}%" if value == value else "unavailable"

    vix_level_text = f"{vix_level:g}" if vix_level == vix_level else "unavailable"
    signal_context = (
        f"KOSPI {format_signal('KOSPI')}, KOSDAQ {format_signal('KOSDAQ')}, "
        f"S&P 500 {format_signal('S&P 500')}, NASDAQ {format_signal('NASDAQ')}, "
        f"VIX level {vix_level_text}"
    )
    signal_context += (
        f", VIX change {format_signal('VIX change')}, USD/KRW {format_signal('USD/KRW')}, "
        f"US 10Y {format_signal('US 10Y')}"
    )
    session_light = (
        "crisp early-morning light with a subtle dawn glow"
        if is_morning
        else "refined evening light with subtle city illumination"
    )

    return (
        "Create a premium square editorial cover illustration for a Korean daily market briefing. "
        "The image must visualize the current measured market mood, not generic finance decoration. "
        f"Headline for semantic direction only, never render it as text: {headline}. "
        f"Current signals for art direction only, never render these values: {signal_context}. "
        f"Scenario: {scenario}. Dominant changing signal: {dominant_driver}. "
        f"Composition: {composition}. Mood: {mood}. Lighting: {session_light}. Palette: {palette}. "
        "Make the spatial structure visibly different from a generic finance cover and let the scenario control "
        "the direction, density, balance and focal point. Use sophisticated editorial illustration, clean geometry, "
        "restrained cinematic depth, realistic light, generous negative space and a strong hierarchy. Keep important "
        "details away from the outer edges for responsive cropping. Do not default to the same city skyline, flowing "
        "data ribbons or centered tunnel composition on every run. "
        "Do not depict bulls, bears, people, mascots, coins, money, rockets, candlestick charts, literal screens, "
        "fake interfaces or cliché Wall Street imagery. Absolutely no text, letters, numbers, ticker symbols, logos, "
        "flags, labels, captions, borders, signatures or watermarks anywhere in the image."
    )


def require_market_coverage(indexes: dict, minimum: int, required: tuple[str, ...] = ()) -> int:
    available = 0
    for item in indexes.values():
        price = parse_price_value(item.get("price", "N/A"))
        if price == price:
            available += 1
    if available < minimum:
        raise RuntimeError(f"Market data coverage below threshold: {available}/{len(indexes)} (minimum {minimum})")
    missing_required = []
    for key in required:
        price = parse_price_value(indexes.get(key, {}).get("price", "N/A"))
        if price != price:
            missing_required.append(key)
    if missing_required:
        raise RuntimeError(f"Required market data missing: {', '.join(missing_required)}")
    return available


def build_risk_trends(snapshot_history: List[dict], current_indexes: dict) -> dict:
    keys = ["vix", "usdkrw", "us10y", "wti"]
    labels = {"up": "상승", "down": "하락", "flat": "보합", "na": "데이터 확인 필요"}
    colors = {"up": UP_COLOR, "down": DOWN_COLOR, "flat": NEUTRAL_COLOR, "na": NEUTRAL_COLOR}

    previous_indexes = {}
    if snapshot_history:
        previous_indexes = snapshot_history[0].get("indexes", {})

    trends = {}
    for key in keys:
        current = parse_price_value(current_indexes.get(key, {}).get("price", "N/A"))
        previous = parse_price_value(previous_indexes.get(key, {}).get("price", "N/A"))
        if current != current or previous != previous:
            trend_key = "na"
        elif current > previous:
            trend_key = "up"
        elif current < previous:
            trend_key = "down"
        else:
            trend_key = "flat"

        trends[key] = {"text": labels[trend_key], "color": colors[trend_key]}

    return trends


def build_market_overview(indexes: dict) -> List[dict]:
    selected = [
        ("kospi", "KOSPI"),
        ("kosdaq", "KOSDAQ"),
        ("ewy", "EWY"),
        ("sp500", "S&P 500"),
        ("dow", "DOW"),
        ("nasdaq", "NASDAQ"),
        ("usdkrw", "USD/KRW"),
        ("vix", "VIX"),
    ]
    cards = []
    for key, label in selected:
        item = indexes.get(key, {})
        cards.append(
            {
                "key": key,
                "label": label,
                "price": item.get("price", "N/A"),
                "change": item.get("change", "-"),
                "color": item.get("color", "#64748b"),
                "market_status": item.get("market_status", "기준 확인 필요"),
                "as_of": item.get("as_of", "기준시각 확인 필요"),
            }
        )
    return cards


def _news_cutoff(now_kst: datetime) -> datetime:
    weekday = now_kst.weekday()
    if now_kst.hour < 12:
        if weekday == 0:
            return (now_kst - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            return (now_kst - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    else:
        return now_kst.replace(hour=9, minute=0, second=0, microsecond=0)


def crawl_naver_news(now_kst: datetime, max_articles: int = 30) -> List[dict]:
    cutoff = _news_cutoff(now_kst)
    headers = {"User-Agent": "Mozilla/5.0"}
    articles: List[dict] = []

    for page in range(1, 4):
        url = f"https://news.naver.com/breakingnews/section/101/258?page={page}"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()
        except Exception:
            continue

        soup = BeautifulSoup(res.text, "html.parser")
        for item in soup.select("li.sa_item"):
            title_el = item.select_one("a.sa_text_title")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")

            press_el = item.select_one("div.sa_text_press")
            press = press_el.get_text(strip=True) if press_el else ""

            time_el = item.select_one("div.sa_text_datetime b")
            time_str = time_el.get_text(strip=True) if time_el else ""

            summary_el = item.select_one("div.sa_text_lede")
            description = summary_el.get_text(" ", strip=True) if summary_el else ""
            image_el = item.select_one("img")
            image_url = ""
            if image_el:
                image_url = image_el.get("data-src") or image_el.get("src") or ""

            article_dt = None
            for fmt in ("%Y.%m.%d. %H:%M", "%Y.%m.%d."):
                try:
                    article_dt = KST.localize(datetime.strptime(time_str, fmt))
                    break
                except ValueError:
                    continue

            if article_dt and article_dt < cutoff:
                continue

            articles.append({
                "title": title,
                "link": link,
                "press": press,
                "time": time_str,
                "dt": article_dt,
                "description": description,
                "image_url": image_url,
            })

        if len(articles) >= max_articles:
            break

    return articles[:max_articles]


def select_top_news(articles: List[dict], max_count: int = 5, client=None) -> List[dict]:
    fallback = []
    for article in articles[:max_count]:
        item = dict(article)
        item.setdefault("impact_scope", "Market")
        item.setdefault("why_it_matters", "")
        fallback.append(item)
    if not articles or (not OPENAI_API_KEY and client is None):
        return fallback

    candidates = [
        {
            "id": i,
            "press": article.get("press", ""),
            "title": article.get("title", ""),
            "description": article.get("description", "")[:320],
        }
        for i, article in enumerate(articles)
    ]

    news_client = client or OpenAI(api_key=OPENAI_API_KEY)
    try:
        resp = news_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "한국 투자자를 위한 증권·경제 뉴스 편집자입니다. 후보에 있는 사실만 사용하세요. "
                        "금리·정책·실적·섹터 회전·수급·지정학·지수 영향도가 큰 뉴스를 우선하고, "
                        "중복 이슈와 광고성·생활경제성 기사는 제외하세요. why_it_matters는 제목과 "
                        "description에서 확인되는 범위 안에서만 작성하고 새로운 숫자나 원인을 만들지 마세요."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"다음 후보에서 최대 {max_count}개를 고르세요. impact_scope는 "
                        "Macro, US, Korea, Sector 중 하나입니다.\n"
                        + json.dumps(candidates, ensure_ascii=False)
                    ),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "market_news_selection",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "selected_news": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": max_count,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "impact_scope": {
                                            "type": "string",
                                            "enum": ["Macro", "US", "Korea", "Sector"],
                                        },
                                        "why_it_matters": {
                                            "type": "string",
                                            "minLength": 1,
                                            "maxLength": 120,
                                        },
                                    },
                                    "required": ["id", "impact_scope", "why_it_matters"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["selected_news"],
                        "additionalProperties": False,
                    },
                },
            },
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
        selected = []
        seen_ids = set()
        for choice in parsed.get("selected_news", []):
            idx = choice.get("id")
            if not isinstance(idx, int) or not 0 <= idx < len(articles) or idx in seen_ids:
                continue
            seen_ids.add(idx)
            item = dict(articles[idx])
            item["impact_scope"] = choice["impact_scope"]
            why_it_matters = re.sub(r"\s+", " ", choice["why_it_matters"]).strip()
            if not has_only_grounded_numbers([why_it_matters], candidates[idx]):
                why_it_matters = ""
            item["why_it_matters"] = why_it_matters
            selected.append(item)
        if selected:
            return selected[:max_count]
    except Exception as exc:
        logging.warning("select_top_news LLM failed: %s", exc)

    return fallback


def load_recent_snapshots(limit: int = 7) -> List[dict]:
    snapshots: List[dict] = []
    files = sorted(DATA_DIR.glob("*.json"), reverse=True)
    for path in files:
        if path.name == "latest.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        snapshots.append(data)
        if len(snapshots) >= limit:
            break
    return snapshots


def save_snapshot(
    headline: str,
    summary_items: List[str],
    cover_image: str,
    indexes: dict,
    news_items: List[dict] | None = None,
) -> None:
    timestamp = datetime.now(KST)
    payload = {
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "edition_title": EDITION_TITLE,
        "headline": headline,
        "summary_items": summary_items,
        "indexes": indexes,
        "news_items": news_items or [],
        "cover_image": cover_image,
        "generate_ai_image": GENERATE_AI_IMAGE,
        "run_source": detect_run_source(),
    }
    stamp = timestamp.strftime("%Y-%m-%d-%H%M%S")
    (DATA_DIR / f"{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (DATA_DIR / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def generate_ai_briefing(
    kospi: dict,
    kosdaq: dict,
    sp500: dict,
    dow: dict,
    nasdaq: dict,
    ewy: dict,
    vix: dict,
    usdkrw: dict,
    us10y: dict,
    wti: dict,
) -> Tuple[str, List[str], str]:
    fallback_headline = build_market_headline(kospi, nasdaq, vix)

    watchpoint_line = build_watchpoint_line(vix, usdkrw, us10y)
    fallback_korea_items, fallback_us_items = build_fallback_section_items(
        kospi, kosdaq, sp500, dow, nasdaq, ewy, vix, usdkrw, us10y, wti
    )
    fallback_items = ["[한국 시장]", *fallback_korea_items, "[미국 시장]", *fallback_us_items]

    if not OPENAI_API_KEY:
        existing_cover = get_existing_cover_file()
        if existing_cover:
            return fallback_headline, fallback_items, existing_cover
        generate_cover_svg(OUTPUT_DIR / "cover.svg", fallback_headline)
        return fallback_headline, fallback_items, "cover.svg"

    client = OpenAI(api_key=OPENAI_API_KEY, timeout=30, max_retries=1)

    prompt_context = (
        "간밤의 미국 시장 주요 이슈와 오늘 아침 한국 시장의 개장 흐름 및 관전 포인트"
        if IS_MORNING
        else "오늘 한국 시장 마감 상황 요약 및 오늘 밤 미국 시장 관전 포인트"
    )

    evidence = {
        "idx-kospi": kospi,
        "idx-kosdaq": kosdaq,
        "idx-sp500": sp500,
        "idx-dow": dow,
        "idx-nasdaq": nasdaq,
        "idx-ewy": ewy,
        "idx-vix": vix,
        "idx-usdkrw": usdkrw,
        "idx-us10y": us10y,
        "idx-wti": wti,
    }
    text_prompt = f"""
목표: {prompt_context}를 개인투자자 대상 데일리 브리핑으로 작성하세요.

사용 가능한 팩트 데이터(이 범위 밖 정보는 추정/창작 금지):
- 한국 시장: 코스피 {kospi['price']} ({kospi['change']}), 코스닥 {kosdaq['price']} ({kosdaq['change']})
- 미국 시장: S&P500 {sp500['price']} ({sp500['change']}), 다우존스 {dow['price']} ({dow['change']}), 나스닥 {nasdaq['price']} ({nasdaq['change']})
- 한국 야간지표(EWY): {ewy['price']} ({ewy['change']} - {ewy['trend']})
- 리스크/거시: VIX {vix['price']} ({vix['change']}), 달러원 {usdkrw['price']} ({usdkrw['change']}), 미10년물 {us10y['price']} ({us10y['change']}), WTI {wti['price']} ({wti['change']})

작성 규칙:
1) korea_points 3개, us_points 2개, watchpoint 1개를 작성하세요.
2) 관측값과 해석을 분리하고, 제공된 수치에서 직접 확인되지 않는 원인·뉴스·수급 주체를 만들지 마세요.
3) 각 포인트는 55~90자이며 수급/섹터/리스크/대응 중 관련 있는 실전 해석을 포함하세요.
4) 각 포인트의 핵심 키워드 1개 이상을 **굵게** 표시하세요.
5) 한국/미국 포인트에 각각 최소 1개의 제공된 숫자를 포함하세요.
6) N/A 값은 숫자를 추정하지 말고 "데이터 확인 필요"로 표현하세요.
7) 단정적 예측, 매수·매도 지시, 목표가 제시는 금지합니다.
8) watchpoint는 "오늘의 핵심 관전 포인트:"로 시작하고, 관측 가능한 조건형 트리거 2개를 포함하세요.
9) headline은 공백 포함 12~24자의 한국어 한 줄로, 시장의 방향 차이·긴장감·온도차 중 실제 데이터로 확인되는 특징을 압축하세요.
10) headline, 각 point, watchpoint에 claim_type 및 실제 근거의 evidence_ids를 포함하세요. 지표명과 수치는 반드시 같은 evidence ID를 인용하세요.
11) 모든 text에는 인용한 근거 중 최소 한 개의 정확한 지표명(KOSPI, KOSDAQ, S&P 500, DOW, NASDAQ, EWY, VIX, USD/KRW, 미10년물, WTI)을 반드시 포함하세요.
12) headline에는 정확한 지표명을 포함하고, 과장·특수기호·이모지·매수·매도 표현을 사용하지 마세요.

근거 ID: idx-kospi, idx-kosdaq, idx-sp500, idx-dow, idx-nasdaq, idx-ewy, idx-vix, idx-usdkrw, idx-us10y, idx-wti
근거 데이터(JSON): {json.dumps(evidence, ensure_ascii=False)}
""".strip()

    def generate_market_cover(cover_headline: str) -> str:
        image_prompt = build_market_cover_prompt(
            cover_headline,
            kospi,
            kosdaq,
            sp500,
            nasdaq,
            vix,
            usdkrw,
            us10y,
            IS_MORNING,
        )

        if not GENERATE_AI_IMAGE:
            existing_cover = get_existing_cover_file()
            if existing_cover:
                return existing_cover
            generate_cover_svg(OUTPUT_DIR / "cover.svg", cover_headline)
            return "cover.svg"

        try:
            image_response = client.images.generate(
                model=IMAGE_MODEL,
                prompt=image_prompt,
                size="1024x1024",
                quality="low",
                n=1,
            )
            img_data = base64.b64decode(image_response.data[0].b64_json)
            _clean_old_covers()
            now_kst = datetime.now(KST)
            cover_name = f"cover_{now_kst.strftime('%Y%m%d_%H%M')}.png"
            (OUTPUT_DIR / cover_name).write_bytes(img_data)
            return cover_name
        except Exception as exc:
            print(f"AI cover fallback: {type(exc).__name__}: {exc}")
            existing_cover = get_existing_cover_file()
            if existing_cover:
                return existing_cover
            generate_cover_svg(OUTPUT_DIR / "cover.svg", cover_headline)
            return "cover.svg"

    try:
        briefing = create_structured_completion(
            client,
            schema_name="daily_market_briefing",
            schema=DAILY_BRIEFING_SCHEMA,
            system_prompt=(
                "당신은 금융 데이터 편집자입니다. 사용자에게 제공된 팩트만 사용하세요. "
                "관측되지 않은 사건, 인과관계, 수급 주체를 추정하지 말고 사실과 조건부 해석을 구분하세요. "
                "출력은 지정된 JSON Schema를 준수하며 투자 권유가 아닌 정보성 브리핑이어야 합니다."
            ),
            user_prompt=text_prompt,
        )
        claims = [briefing["headline"], *briefing["korea_points"], *briefing["us_points"], briefing["watchpoint"]]
        aliases = {
            "코스피": "idx-kospi", "KOSPI": "idx-kospi", "코스닥": "idx-kosdaq", "KOSDAQ": "idx-kosdaq",
            "S&P500": "idx-sp500", "S&P 500": "idx-sp500", "다우": "idx-dow", "나스닥": "idx-nasdaq",
            "NASDAQ": "idx-nasdaq", "EWY": "idx-ewy", "VIX": "idx-vix", "달러원": "idx-usdkrw",
            "USD/KRW": "idx-usdkrw", "미10년물": "idx-us10y", "WTI": "idx-wti",
        }
        evidence_labels = {
            "idx-kospi": "KOSPI", "idx-kosdaq": "KOSDAQ", "idx-sp500": "S&P 500",
            "idx-dow": "DOW", "idx-nasdaq": "NASDAQ", "idx-ewy": "EWY", "idx-vix": "VIX",
            "idx-usdkrw": "USD/KRW", "idx-us10y": "미 10년물", "idx-wti": "WTI",
        }
        if not validate_grounded_claims(claims, evidence, aliases):
            raise ValueError("AI briefing contains a claim not grounded in its cited evidence")
        headline = re.sub(r"[*#`]+", "", briefing["headline"]["text"]).strip()
        if not 8 <= len(headline) <= 40:
            headline = fallback_headline
        korea_points = [render_grounded_claim(item, evidence, evidence_labels) for item in briefing["korea_points"]]
        us_points = [render_grounded_claim(item, evidence, evidence_labels) for item in briefing["us_points"]]
        watchpoint_body = render_grounded_claim(briefing["watchpoint"], evidence, evidence_labels)
        watchpoint = f"오늘의 **핵심 관전 포인트**: {watchpoint_body}" if watchpoint_body else watchpoint_line
        summary_items = ["[한국 시장]", *korea_points, "[미국 시장]", *us_points, watchpoint]
        image_file = generate_market_cover(headline)
        return headline, summary_items, image_file
    except Exception as exc:
        print(f"AI briefing fallback: {type(exc).__name__}: {exc}")
        image_file = generate_market_cover(fallback_headline)
        return fallback_headline, fallback_items, image_file


def render_html(
    headline: str,
    summary_items: List[str],
    cover_image: str,
    indexes: dict,
    risk_trends: dict,
    snapshot_count: int,
    news_items: List[dict] = None,
) -> None:
    env = Environment(
        loader=FileSystemLoader(str(BASE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["bold"] = bold_filter
    template = env.get_template("template.html")

    korea_items: List[str] = []
    us_items: List[str] = []
    current_section = ""
    for item in summary_items:
        if item == "[한국 시장]":
            current_section = "korea"
        elif item == "[미국 시장]":
            current_section = "us"
        elif current_section == "korea":
            korea_items.append(item)
        elif current_section == "us":
            us_items.append(item)

    html_output = template.render(
        edition_title=EDITION_TITLE,
        current_time=CURRENT_TIME_STR,
        comic_headline=headline,
        korea_items=korea_items,
        us_items=us_items,
        cover_image=cover_image,
        market_overview=build_market_overview(indexes),
        risk_trends=risk_trends,
        snapshot_count=snapshot_count,
        news_items=news_items or [],
        **indexes,
    )

    (OUTPUT_DIR / "index.html").write_text(html_output, encoding="utf-8")


def send_discord_alert(
    headline: str,
    summary_items: List[str],
    indexes: dict,
    news_items: List[dict] | None = None,
) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    embed = build_daily_embed(
        EDITION_TITLE,
        CURRENT_TIME_STR,
        headline,
        summary_items,
        indexes,
        news_items or [],
        resolve_pages_url(),
    )
    post_discord_webhook(DISCORD_WEBHOOK_URL, embed)


def main() -> None:
    global CURRENT_TIME_STR, IS_MORNING, EDITION_TITLE
    now_kst = datetime.now(KST)
    CURRENT_TIME_STR = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    IS_MORNING = now_kst.hour < 12
    EDITION_TITLE = (
        "Morning Briefing: 간밤의 미장 & 국장 프리뷰"
        if IS_MORNING
        else "Evening Briefing: 오늘 국장 마감 & 미장 프리뷰"
    )

    previous_snapshots = load_recent_snapshots(limit=7)

    indexes = {
        "kospi": get_korean_index_data("KOSPI"),
        "kosdaq": get_korean_index_data("KOSDAQ"),
        "sp500": get_index_data("^GSPC"),
        "dow": get_index_data("^DJI"),
        "nasdaq": get_index_data("^IXIC"),
        "ewy": get_index_data("EWY"),
        "vix": get_index_data("^VIX"),
        "usdkrw": get_index_data("KRW=X"),
        "us10y": get_index_data("^TNX"),
        "wti": get_index_data("CL=F"),
    }
    require_market_coverage(
        indexes,
        minimum=int(os.getenv("MIN_MARKET_COVERAGE", "6")),
        required=("kospi", "kosdaq"),
    )

    headline, summary_items, cover_image = generate_ai_briefing(
        indexes["kospi"],
        indexes["kosdaq"],
        indexes["sp500"],
        indexes["dow"],
        indexes["nasdaq"],
        indexes["ewy"],
        indexes["vix"],
        indexes["usdkrw"],
        indexes["us10y"],
        indexes["wti"],
    )

    risk_trends = build_risk_trends(previous_snapshots, indexes)
    snapshot_count = len(previous_snapshots) + 1

    raw_news = crawl_naver_news(now_kst)
    news_items = select_top_news(raw_news, max_count=5)
    for item in news_items:
        item.pop("dt", None)

    render_html(headline, summary_items, cover_image, indexes, risk_trends, snapshot_count, news_items)
    send_discord_alert(headline, summary_items, indexes, news_items)
    save_snapshot(headline, summary_items, cover_image, indexes, news_items)

    print("Generated:", OUTPUT_DIR / "index.html")


if __name__ == "__main__":
    main()
