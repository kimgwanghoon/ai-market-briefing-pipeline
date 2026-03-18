import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import pytz
import requests
from jinja2 import Environment, FileSystemLoader

from main import resolve_pages_url


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


def build_week_summary(snapshots: List[dict]) -> dict:
    if not snapshots:
        return {
            "score_avg": 0,
            "score_max": 0,
            "score_min": 0,
            "count": 0,
            "label": "데이터 부족",
            "top_risk": "데이터 부족",
            "top_watchpoint": "데이터 부족",
        }

    scores = [float(item.get("sentiment", {}).get("score", 0)) for item in snapshots]
    score_avg = round(sum(scores) / len(scores), 2)
    score_max = round(max(scores), 2)
    score_min = round(min(scores), 2)

    if score_avg >= 60:
        label = "우호"
    elif score_avg < 40:
        label = "경계"
    else:
        label = "중립"

    risk_candidates = []
    for item in snapshots:
        for event in item.get("events", {}).get("news", [])[:5]:
            impact = abs(float(event.get("impact_score", 0)))
            risk_candidates.append((impact, event.get("title", "")))
        for event in item.get("events", {}).get("dart", [])[:5]:
            impact = abs(float(event.get("impact_score", 0)))
            title = f"{event.get('corp_name', '')} {event.get('title', '')}".strip()
            risk_candidates.append((impact, title))

    risk_candidates.sort(key=lambda x: x[0], reverse=True)
    top_risk = risk_candidates[0][1] if risk_candidates else "유의미한 이벤트 부족"

    watchpoint = ""
    for item in reversed(snapshots):
        watchpoint = item.get("watchpoint", "")
        if watchpoint:
            break

    return {
        "score_avg": score_avg,
        "score_max": score_max,
        "score_min": score_min,
        "count": len(snapshots),
        "label": label,
        "top_risk": top_risk,
        "top_watchpoint": watchpoint or "핵심 관전 포인트 데이터 없음",
    }


def save_weekly_report(summary: dict, snapshots: List[dict]) -> dict:
    now = datetime.now(KST)
    year, week, _ = now.isocalendar()
    title = f"주간 시장 리포트 | {year}-W{week:02d}"
    payload = {
        "title": title,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
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
    env = Environment(loader=FileSystemLoader(str(BASE_DIR)))
    template = env.get_template("template_weekly.html")
    html = template.render(
        title=payload.get("title", "주간 시장 리포트"),
        generated_at=payload.get("generated_at", ""),
        summary=payload.get("summary", {}),
        samples=payload.get("recent_samples", []),
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
            f"최고/최저: {summary.get('score_max', 0)} / {summary.get('score_min', 0)}\n"
            f"주요 리스크: {summary.get('top_risk', '-')}") ,
        "color": 5763714,
        "url": f"{resolve_pages_url()}/weekly.html",
        "fields": [
            {"name": "집계 샘플", "value": str(summary.get("count", 0)), "inline": True},
            {"name": "핵심 관전 포인트", "value": summary.get("top_watchpoint", "-")[:1000], "inline": False},
        ],
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
    except Exception:
        pass


def main() -> None:
    snapshots = load_week_snapshots(days=7)
    summary = build_week_summary(snapshots)
    payload = save_weekly_report(summary, snapshots)
    render_weekly_html(payload)
    send_weekly_discord(payload)
    print("Generated:", OUTPUT_DIR / "weekly.html")


if __name__ == "__main__":
    main()
