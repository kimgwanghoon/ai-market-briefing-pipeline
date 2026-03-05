import os
import re
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

OPENAI_API_KEY = os.getenv("AI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GITHUB_PAGES_URL = os.getenv("GITHUB_PAGES_URL", "")

KST = pytz.timezone("Asia/Seoul")
NOW_KST = datetime.now(KST)
CURRENT_TIME_STR = NOW_KST.strftime("%Y-%m-%d %H:%M:%S")
IS_MORNING = NOW_KST.hour < 12
EDITION_TITLE = (
    "Morning Briefing: 간밤의 미장 & 국장 프리뷰"
    if IS_MORNING
    else "Evening Briefing: 오늘 국장 마감 & 미장 프리뷰"
)


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
    except Exception:
        return default


def get_index_data(ticker: str) -> dict:
    default = {"price": "N/A", "change": "-", "color": "#6b7280", "trend": "보합"}
    try:
        data = yf.Ticker(ticker).history(period="5d")
        if len(data) < 2:
            return default

        today_close = float(data["Close"].iloc[-1])
        yesterday_close = float(data["Close"].iloc[-2])
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
    except Exception:
        return default


def fallback_summary(kospi: dict, kosdaq: dict, sp500: dict, dow: dict, nasdaq: dict, ewy: dict) -> Tuple[str, List[str]]:
    headline = "핵심 지수 점검"
    summary_items = [
        f"국장 지표는 코스피 {kospi['price']} ({kospi['change']}), 코스닥 {kosdaq['price']} ({kosdaq['change']})로 집계되었습니다.",
        f"미장 지표는 S&P500 {sp500['price']} ({sp500['change']}), 다우 {dow['price']} ({dow['change']}), 나스닥 {nasdaq['price']} ({nasdaq['change']}) 흐름입니다.",
        f"한국 야간지표 EWY는 {ewy['price']} ({ewy['change']})로, 국내 개장 심리에 영향을 줄 수 있습니다.",
        "오늘은 지수 레벨보다 변동성 확장 여부를 우선 확인하고, 급등 추격보다 분할 대응 전략이 유효합니다.",
    ]
    return headline, summary_items


def normalize_summary_items(items: List[str], fallback_items: List[str]) -> List[str]:
    cleaned = [item.strip() for item in items if item and item.strip()]
    if len(cleaned) < 5:
        return fallback_items[:8]
    return cleaned[:8]


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


def generate_ai_briefing(kospi: dict, kosdaq: dict, sp500: dict, dow: dict, nasdaq: dict, ewy: dict) -> Tuple[str, List[str], str]:
    fallback_headline, fallback_items = fallback_summary(kospi, kosdaq, sp500, dow, nasdaq, ewy)

    if not OPENAI_API_KEY:
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

작성 규칙:
1) 반드시 아래 출력 형식 그대로 작성하세요. 섹션명/불릿 기호/줄 수를 지키세요.
2) 각 불릿은 45자 내외의 짧은 문장으로 작성하세요.
3) 핵심 키워드는 각 불릿마다 1개 이상 **굵게** 표시하세요.
4) [한국 시장] 3개 + [미국 시장] 3개, 총 6개 불릿을 작성하세요.
5) 각 섹션에서 최소 1개 불릿은 숫자(지수/등락률/변동폭)를 포함하세요.
6) 데이터가 N/A이거나 불명확하면 숫자를 만들지 말고 "데이터 확인 필요"라고 명시하세요.
7) 과장, 투자확정 표현(예: 반드시 오른다)은 금지합니다.

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
                        "당신은 한국/미국 주식 데일리 브리핑 에디터입니다. "
                        "입력된 데이터만 사용해 간결하고 실행 가능한 관전 포인트를 작성하세요."
                    ),
                },
                {"role": "user", "content": text_prompt},
            ],
        )
        llm_summary_raw = text_response.choices[0].message.content.strip()
        
        lines = llm_summary_raw.split("\n")
        summary_items = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("[") and "]" in stripped:
                summary_items.append(stripped)
            else:
                summary_items.append(stripped.lstrip("-").strip())
        
        summary_items = normalize_summary_items(summary_items, fallback_items)

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

        image_prompt = f"""
Webtoon style 4-panel comic about Korean stock market: {headline}
Style: clean line art, soft colors, expressive bull and bear characters.
Layout: 2x2 grid.
IMPORTANT: Do NOT include any text in the image. No text bubbles.
Show: bull and bear characters, up/down arrows, charts, stock market icons, Korean flag colors.
""".strip()

        image_file = "cover.svg"
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
            generate_cover_svg(OUTPUT_DIR / "cover.svg", headline)

        return headline, summary_items, image_file
    except Exception:
        generate_cover_svg(OUTPUT_DIR / "cover.svg", fallback_headline)
        return fallback_headline, fallback_items, "cover.svg"


def render_html(headline: str, summary_items: List[str], cover_image: str, indexes: dict) -> None:
    env = Environment(loader=FileSystemLoader(str(BASE_DIR)))
    env.filters["bold"] = bold_filter
    template = env.get_template("template.html")

    html_output = template.render(
        edition_title=EDITION_TITLE,
        current_time=CURRENT_TIME_STR,
        comic_headline=headline,
        summary_items=summary_items,
        cover_image=cover_image,
        **indexes,
    )

    (OUTPUT_DIR / "index.html").write_text(html_output, encoding="utf-8")


def send_discord_alert(headline: str, summary_items: List[str], indexes: dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        return

    body_text = "\n".join([re.sub(r"\*+", "", s) for s in summary_items])

    embed_fields = [
        {"name": "KOSPI", "value": f"{indexes['kospi']['price']} ({indexes['kospi']['change']})", "inline": True},
        {"name": "KOSDAQ", "value": f"{indexes['kosdaq']['price']} ({indexes['kosdaq']['change']})", "inline": True},
        {"name": "EWY", "value": f"{indexes['ewy']['price']} ({indexes['ewy']['change']})", "inline": True},
        {"name": "S&P 500", "value": f"{indexes['sp500']['price']} ({indexes['sp500']['change']})", "inline": True},
        {"name": "Dow", "value": f"{indexes['dow']['price']} ({indexes['dow']['change']})", "inline": True},
        {"name": "NASDAQ", "value": f"{indexes['nasdaq']['price']} ({indexes['nasdaq']['change']})", "inline": True},
    ]

    payload = {
        "embeds": [
            {
                "title": f"🚨 {EDITION_TITLE}",
                "color": 5763714,
                "fields": embed_fields,
                "description": f"**📰 {headline}**\n\n{body_text}",
                "url": GITHUB_PAGES_URL or "https://github.com",
            }
        ]
    }

    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception:
        pass


def main() -> None:
    indexes = {
        "kospi": get_korean_index_data("KOSPI"),
        "kosdaq": get_korean_index_data("KOSDAQ"),
        "sp500": get_index_data("^GSPC"),
        "dow": get_index_data("^DJI"),
        "nasdaq": get_index_data("^IXIC"),
        "ewy": get_index_data("EWY"),
    }

    headline, summary_items, cover_image = generate_ai_briefing(
        indexes["kospi"],
        indexes["kosdaq"],
        indexes["sp500"],
        indexes["dow"],
        indexes["nasdaq"],
        indexes["ewy"],
    )

    render_html(headline, summary_items, cover_image, indexes)
    send_discord_alert(headline, summary_items, indexes)

    print("Generated:", OUTPUT_DIR / "index.html")


if __name__ == "__main__":
    main()
