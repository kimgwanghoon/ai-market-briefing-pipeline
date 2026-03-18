import logging
import os
import re
import json
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import pytz
import requests
import yfinance as yf
from jinja2 import Environment, FileSystemLoader
from openai import OpenAI

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


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


GENERATE_AI_IMAGE = env_flag("GENERATE_AI_IMAGE", default=True)


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


def bold_filter(text: str) -> str:
    return re.sub(r"\*+([^*]+)\*+", r"<strong>\1</strong>", text)


def get_korean_index_data(market_type: str) -> dict:
    url = f"https://m.stock.naver.com/api/index/{market_type}/basic"
    headers = {"User-Agent": "Mozilla/5.0"}
    default = {"price": "N/A", "change": "-", "color": "#6b7280", "trend": "보합"}

    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()

        price = data["closePrice"]
        diff = data["compareToPreviousClosePrice"]
        ratio = float(data["fluctuationsRatio"])
        trend_code = data["compareToPreviousPrice"]["code"]

        if trend_code in ["1", "2"]:
            color, sign, trend = "#ef4444", "▲", "상승"
        elif trend_code in ["4", "5"]:
            color, sign, trend = "#3b82f6", "▼", "하락"
        else:
            color, sign, trend = "#6b7280", "-", "보합"

        return {
            "price": price,
            "change": f"{sign} {str(diff).replace('-', '')} ({ratio:+.2f}%)",
            "color": color,
            "trend": trend,
        }
    except Exception as exc:
        logging.warning("get_korean_index_data(%s) failed: %s", market_type, exc)
        return default


def get_index_data(ticker: str) -> dict:
    default = {"price": "N/A", "change": "-", "color": "#6b7280", "trend": "보합"}

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
            closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
            closes = [float(v) for v in closes if v is not None]
            if len(closes) >= 2:
                today_close = closes[-1]
                yesterday_close = closes[-2]
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
            }
        except Exception as exc:
            logging.warning("get_index_data(%s) yfinance retry failed: %s", ticker, exc)
            continue

    return default


def get_batch_index_data(ticker_map: dict) -> dict:
    default = {"price": "N/A", "change": "-", "color": "#6b7280", "trend": "보합"}
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
                color, sign, trend = "#ef4444", "▲", "상승"
            elif diff < 0:
                color, sign, trend = "#3b82f6", "▼", "하락"
            else:
                color, sign, trend = "#6b7280", "-", "보합"

            results[key] = {
                "price": f"{today_close:,.2f}",
                "change": f"{sign} {abs(diff):.2f} ({pct_change:+.2f}%)",
                "color": color,
                "trend": trend,
            }
    except Exception:
        return results

    return results


def fallback_summary(
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
) -> Tuple[str, List[str]]:
    headline = "핵심 지수 점검"
    summary_items = [
        f"국장 지표는 코스피 {kospi['price']} ({kospi['change']}), 코스닥 {kosdaq['price']} ({kosdaq['change']})로 집계되었습니다.",
        f"미장 지표는 S&P500 {sp500['price']} ({sp500['change']}), 다우 {dow['price']} ({dow['change']}), 나스닥 {nasdaq['price']} ({nasdaq['change']}) 흐름입니다.",
        f"한국 야간지표 EWY는 {ewy['price']} ({ewy['change']})로, 국내 개장 심리에 영향을 줄 수 있습니다.",
        f"리스크 체온계는 VIX {vix['price']} ({vix['change']}), 달러/원 {usdkrw['price']} ({usdkrw['change']}), 미 10년물 {us10y['price']} ({us10y['change']}), WTI {wti['price']} ({wti['change']})입니다.",
        "오늘은 지수 레벨보다 변동성 확장 여부를 우선 확인하고, 급등 추격보다 분할 대응 전략이 유효합니다.",
    ]
    return headline, summary_items


def ensure_bold_keyword(text: str) -> str:
    if "**" in text:
        return text
    match = re.search(r"[A-Za-z0-9가-힣/%+-]+", text)
    if not match:
        return text
    token = match.group(0)
    return text.replace(token, f"**{token}**", 1)


def build_watchpoint_line(vix: dict, usdkrw: dict, us10y: dict) -> str:
    return (
        "오늘의 **핵심 관전 포인트**: "
        f"**VIX**({vix['price']})가 반등하면 단기 변동성 재확대를 염두에 두고, "
        f"**미10년물**({us10y['price']})과 **달러원**({usdkrw['price']})이 동반 상승하면 "
        "고밸류 추격 대신 방어주와 현금 비중을 병행 점검하세요."
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
        f"**코스피** {kospi['price']} ({kospi['change']}), **코스닥** {kosdaq['price']} ({kosdaq['change']})를 통해 장초 국내 수급 강도를 먼저 점검하세요.",
        f"야간 **EWY** {ewy['price']} ({ewy['change']})와 **달러원** {usdkrw['price']} ({usdkrw['change']}) 조합이 개장 직후 위험선호의 방향성을 가늠합니다.",
        "초반 급등주 추격보다 **거래대금**과 주도 섹터 확산 여부를 확인한 뒤 분할 대응이 합리적입니다.",
    ]
    us_items = [
        f"**S&P500** {sp500['price']} ({sp500['change']}), **다우** {dow['price']} ({dow['change']}), **나스닥** {nasdaq['price']} ({nasdaq['change']})의 상대 강도로 위험자산 선호를 확인하세요.",
        f"**VIX** {vix['price']} ({vix['change']}), **미10년물** {us10y['price']} ({us10y['change']}), **WTI** {wti['price']} ({wti['change']})를 함께 보며 변동성/금리/원자재 압력을 동시 점검하세요.",
        build_watchpoint_line(vix, usdkrw, us10y),
    ]
    return korea_items, us_items


def normalize_summary_items(
    items: List[str],
    fallback_korea_items: List[str],
    fallback_us_items: List[str],
    watchpoint_line: str,
) -> List[str]:
    korea_items: List[str] = []
    us_items: List[str] = []
    current_section = "korea"

    for raw in items:
        stripped = raw.strip()
        if not stripped:
            continue
        if "[한국 시장]" in stripped or stripped.startswith("한국 시장"):
            current_section = "korea"
            continue
        if "[미국 시장]" in stripped or stripped.startswith("미국 시장"):
            current_section = "us"
            continue

        cleaned = stripped.lstrip("-").strip()
        if not cleaned:
            continue
        cleaned = ensure_bold_keyword(cleaned)

        if current_section == "korea" and len(korea_items) < 3:
            korea_items.append(cleaned)
        elif current_section == "us" and len(us_items) < 3:
            us_items.append(cleaned)
        elif len(korea_items) < 3:
            korea_items.append(cleaned)
        elif len(us_items) < 3:
            us_items.append(cleaned)

    while len(korea_items) < 3:
        korea_items.append(fallback_korea_items[len(korea_items)])

    while len(us_items) < 3:
        us_items.append(fallback_us_items[len(us_items)])

    us_items = us_items[:3]
    if "핵심 관전 포인트" not in us_items[-1]:
        us_items[-1] = watchpoint_line
    us_items = [ensure_bold_keyword(item) for item in us_items]

    return ["[한국 시장]", *korea_items[:3], "[미국 시장]", *us_items]


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


def get_existing_cover_file() -> str:
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


def is_briefing_response_valid(text: str) -> bool:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not any("[한국 시장]" in line for line in lines):
        return False
    if not any("[미국 시장]" in line for line in lines):
        return False

    bullet_count = sum(1 for line in lines if line.startswith("-"))
    if bullet_count < 6:
        return False
    if "핵심 관전 포인트" not in text:
        return False
    return True


def parse_price_value(value: str) -> float:
    cleaned = str(value).replace(",", "").strip()
    if cleaned in {"", "N/A", "-"}:
        return float("nan")
    try:
        return float(cleaned)
    except ValueError:
        return float("nan")


def build_risk_trends(snapshot_history: List[dict], current_indexes: dict) -> dict:
    keys = ["vix", "usdkrw", "us10y", "wti"]
    labels = {"up": "상승", "down": "하락", "flat": "보합", "na": "데이터 확인 필요"}
    colors = {"up": "#ef4444", "down": "#3b82f6", "flat": "#6b7280", "na": "#6b7280"}

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
        ("sp500", "S&P 500"),
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
            }
        )
    return cards


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


def save_snapshot(headline: str, summary_items: List[str], cover_image: str, indexes: dict) -> None:
    timestamp = datetime.now(KST)
    payload = {
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "edition_title": EDITION_TITLE,
        "headline": headline,
        "summary_items": summary_items,
        "indexes": indexes,
        "cover_image": cover_image,
        "generate_ai_image": GENERATE_AI_IMAGE,
        "run_source": detect_run_source(),
    }
    stamp = timestamp.strftime("%Y-%m-%d-%H%M%S")
    try:
        (DATA_DIR / f"{stamp}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (DATA_DIR / "latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


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
    fallback_headline, fallback_items = fallback_summary(
        kospi,
        kosdaq,
        sp500,
        dow,
        nasdaq,
        ewy,
        vix,
        usdkrw,
        us10y,
        wti,
    )

    if not OPENAI_API_KEY:
        existing_cover = get_existing_cover_file()
        if existing_cover:
            return fallback_headline, fallback_items, existing_cover
        generate_cover_svg(OUTPUT_DIR / "cover.svg", fallback_headline)
        return fallback_headline, fallback_items, "cover.svg"

    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt_context = (
        "간밤의 미국 시장 주요 이슈와 오늘 아침 한국 시장의 개장 흐름 및 관전 포인트"
        if IS_MORNING
        else "오늘 한국 시장 마감 상황 요약 및 오늘 밤 미국 시장 관전 포인트"
    )

    text_prompt = f"""
목표:
- {prompt_context}를 개인투자자 대상 데일리 브리핑으로 작성하세요.

사용 가능한 팩트 데이터(이 범위 밖 정보는 추정/창작 금지):
- 한국 시장: 코스피 {kospi['price']} ({kospi['change']}), 코스닥 {kosdaq['price']} ({kosdaq['change']})
- 미국 시장: S&P500 {sp500['price']} ({sp500['change']}), 다우존스 {dow['price']} ({dow['change']}), 나스닥 {nasdaq['price']} ({nasdaq['change']})
- 한국 야간지표(EWY): {ewy['price']} ({ewy['change']} - {ewy['trend']})
- 리스크/거시: VIX {vix['price']} ({vix['change']}), 달러원 {usdkrw['price']} ({usdkrw['change']}), 미10년물 {us10y['price']} ({us10y['change']}), WTI {wti['price']} ({wti['change']})

작성 규칙:
1) 반드시 아래 출력 형식 그대로 작성하세요. 섹션명/불릿 기호/줄 수를 지키세요.
2) 문체는 20년차 투자 분석 전문 애널리스트 톤으로, 단정적 예언 대신 근거 기반 판단을 제시하세요.
3) 각 불릿은 55~75자 내외의 간결한 문장으로 작성하세요.
4) 매 문장에 실전 해석(수급/섹터/리스크/대응) 중 최소 1개를 포함하세요.
5) 핵심 키워드는 각 불릿마다 1개 이상 **굵게** 표시하세요.
6) [한국 시장] 3개 + [미국 시장] 3개, 총 6개 불릿을 작성하세요.
7) 각 섹션에서 최소 1개 불릿은 숫자(지수/등락률/변동폭)를 포함하세요.
8) 데이터가 N/A이거나 불명확하면 숫자를 만들지 말고 "데이터 확인 필요"라고 명시하세요.
9) 과장, 투자확정 표현(예: 반드시 오른다)은 금지합니다.
10) 마지막 불릿은 반드시 "오늘의 핵심 관전 포인트" 한 줄로 마무리하세요.
11) 관전 포인트에는 최소 2개 조건형 트리거를 포함하세요. 예: "A면 B, C면 D".

출력 형식:
[한국 시장]
- ...
- ...
- ...
[미국 시장]
- ...
- ...
- ...
""".strip()

    try:
        text_response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "당신은 여의도 경력 20년차 투자 분석 전문 애널리스트입니다. "
                        "입력된 데이터만 사용해, 근거 기반의 실전형 해석과 대응 포인트를 제시하세요. "
                        "과장 없이 리스크와 기회를 함께 짚고, 마지막 문장은 반드시 조건형 시나리오로 작성하세요."
                    ),
                },
                {"role": "user", "content": text_prompt},
            ],
        )
        llm_summary_raw = text_response.choices[0].message.content.strip()
        if not is_briefing_response_valid(llm_summary_raw):
            retry_prompt = (
                "이전 응답이 형식 규칙을 지키지 못했습니다. "
                "반드시 [한국 시장] 3개 불릿, [미국 시장] 3개 불릿으로 다시 작성하고, "
                "마지막 불릿은 '오늘의 핵심 관전 포인트'로 시작해 조건형 시나리오 2개를 포함하세요.\n\n"
                f"원본 요청:\n{text_prompt}\n\n"
                f"이전 응답:\n{llm_summary_raw}"
            )
            retry_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "당신은 형식을 정확히 지키는 금융 브리핑 에디터입니다. "
                            "지정된 포맷과 불릿 수를 반드시 준수하세요."
                        ),
                    },
                    {"role": "user", "content": retry_prompt},
                ],
            )
            llm_summary_raw = retry_response.choices[0].message.content.strip()
        
        lines = llm_summary_raw.split("\n")
        watchpoint_line = build_watchpoint_line(vix, usdkrw, us10y)
        fallback_korea_items, fallback_us_items = build_fallback_section_items(
            kospi,
            kosdaq,
            sp500,
            dow,
            nasdaq,
            ewy,
            vix,
            usdkrw,
            us10y,
            wti,
        )
        summary_items = normalize_summary_items(
            lines,
            fallback_korea_items,
            fallback_us_items,
            watchpoint_line,
        )

        headline_prompt = (
            "다음 브리핑 요약을 바탕으로 한국어 헤드라인 1개를 작성하세요. "
            "길이는 12~18자, 공백 포함입니다. "
            "강한 명사 중심으로 쓰고, 과장/감탄/특수기호는 금지합니다. "
            "출력은 헤드라인 한 줄만 작성하세요.\n\n"
            f"내용: {llm_summary_raw}"
        )
        headline_response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": headline_prompt}],
        )
        headline = headline_response.choices[0].message.content.strip()

        kospi_chg = parse_change_percent(kospi.get("change", ""))
        vix_val = parse_price_value(vix.get("price", "0"))
        if kospi_chg == kospi_chg and kospi_chg > 0.5:
            _chart_dir = "rising strongly upward with confident momentum"
            _figure = "a powerful bull figure charging forward and upward"
            _palette = "vibrant greens, warm golds, bright sky blue"
            _mood = "optimistic, energetic upward momentum, morning sunlight"
        elif kospi_chg == kospi_chg and kospi_chg < -0.5:
            _chart_dir = "falling sharply downward under heavy pressure"
            _figure = "a bear figure pressing down with weight and gravity"
            _palette = "deep reds, cool steel blues, dramatic dark contrast"
            _mood = "tense, declining pressure, heavy atmosphere, storm clouds"
        else:
            _chart_dir = "moving sideways with mixed and uncertain signals"
            _figure = "a bull and bear figure in cautious standoff, neither dominating"
            _palette = "muted grey-blues, soft amber accents, balanced neutral tones"
            _mood = "balanced uncertainty, quiet observation, overcast diffused light"
        _tension = " Extreme volatility, sharp spikes in the chart." if (vix_val == vix_val and vix_val > 25) else ""

        image_prompt = (
            f"Professional editorial illustration for a Korean stock market daily briefing. "
            f"Main visual: A bold stock chart line {_chart_dir}, rendered as the dominant graphic element. "
            f"Background: Yeouido financial district skyline silhouette at dusk or dawn. "
            f"Foreground: {_figure}, abstract circuit-board patterns blending into financial charts. "
            f"Mood: {_mood}.{_tension} "
            f"Color palette: {_palette}. "
            f"Style: modern flat editorial illustration, cinematic depth, professional magazine cover quality. "
            f"No text, no numbers, no labels anywhere in the image."
        )

        image_file = get_existing_cover_file() or "cover.svg"
        if GENERATE_AI_IMAGE:
            try:
                image_response = client.images.generate(
                    model="dall-e-3",
                    prompt=image_prompt,
                    size="1024x1024",
                    quality="standard",
                    n=1,
                )
                image_url = image_response.data[0].url
                img_data = requests.get(image_url, timeout=30).content
                (OUTPUT_DIR / "cover.png").write_bytes(img_data)
                image_file = "cover.png"
            except Exception:
                if not get_existing_cover_file():
                    generate_cover_svg(OUTPUT_DIR / "cover.svg", headline)
                    image_file = "cover.svg"
        else:
            if not get_existing_cover_file():
                generate_cover_svg(OUTPUT_DIR / "cover.svg", headline)
                image_file = "cover.svg"

        return headline, summary_items, image_file
    except Exception:
        generate_cover_svg(OUTPUT_DIR / "cover.svg", fallback_headline)
        return fallback_headline, fallback_items, "cover.svg"


def render_html(
    headline: str,
    summary_items: List[str],
    cover_image: str,
    indexes: dict,
    risk_trends: dict,
    snapshot_count: int,
) -> None:
    env = Environment(loader=FileSystemLoader(str(BASE_DIR)))
    env.filters["bold"] = bold_filter
    template = env.get_template("template.html")

    html_output = template.render(
        edition_title=EDITION_TITLE,
        current_time=CURRENT_TIME_STR,
        comic_headline=headline,
        summary_items=summary_items,
        cover_image=cover_image,
        market_overview=build_market_overview(indexes),
        risk_trends=risk_trends,
        snapshot_count=snapshot_count,
        **indexes,
    )

    (OUTPUT_DIR / "index.html").write_text(html_output, encoding="utf-8")


def send_discord_alert(headline: str, summary_items: List[str], indexes: dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        return

    body_text = "\n".join([re.sub(r"\*+", "", s) for s in summary_items])
    pages_url = resolve_pages_url()

    embed_fields = [
        {"name": "KOSPI", "value": f"{indexes['kospi']['price']} ({indexes['kospi']['change']})", "inline": True},
        {"name": "KOSDAQ", "value": f"{indexes['kosdaq']['price']} ({indexes['kosdaq']['change']})", "inline": True},
        {"name": "EWY", "value": f"{indexes['ewy']['price']} ({indexes['ewy']['change']})", "inline": True},
        {"name": "S&P 500", "value": f"{indexes['sp500']['price']} ({indexes['sp500']['change']})", "inline": True},
        {"name": "Dow", "value": f"{indexes['dow']['price']} ({indexes['dow']['change']})", "inline": True},
        {"name": "NASDAQ", "value": f"{indexes['nasdaq']['price']} ({indexes['nasdaq']['change']})", "inline": True},
        {"name": "VIX", "value": f"{indexes['vix']['price']} ({indexes['vix']['change']})", "inline": True},
        {"name": "USD/KRW", "value": f"{indexes['usdkrw']['price']} ({indexes['usdkrw']['change']})", "inline": True},
        {"name": "US10Y", "value": f"{indexes['us10y']['price']} ({indexes['us10y']['change']})", "inline": True},
        {"name": "WTI", "value": f"{indexes['wti']['price']} ({indexes['wti']['change']})", "inline": True},
    ]

    payload = {
        "embeds": [
            {
                "title": f"🚨 {EDITION_TITLE}",
                "color": 5763714,
                "fields": embed_fields,
                "description": f"**📰 {headline}**\n\n{body_text}",
                "url": pages_url,
            }
        ]
    }

    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception:
        pass


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

    render_html(headline, summary_items, cover_image, indexes, risk_trends, snapshot_count)
    send_discord_alert(headline, summary_items, indexes)
    save_snapshot(headline, summary_items, cover_image, indexes)

    print("Generated:", OUTPUT_DIR / "index.html")


if __name__ == "__main__":
    main()
