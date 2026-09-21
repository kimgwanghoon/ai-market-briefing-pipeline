"""Conservative event watchlist, not price targets or buy signals."""
import json
import math
import re
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

UNIVERSE = json.loads((Path(__file__).resolve().parents[2] / 'config/universe.json').read_text())
POSITIVE = ('수주', '호조', '개선', '증가', '승인', '확대', '회복', '돌파')
NEGATIVE = ('우려', '부진', '감소', '악화', '취소', '경고', '철회')


def fetch_quote(ticker):
    try:
        response = requests.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}',
                                params={'interval': '1d', 'range': '5d'},
                                headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        response.raise_for_status()
        meta = response.json()['chart']['result'][0]['meta']
        return {'price': meta['regularMarketPrice'], 'volume': meta['regularMarketVolume'],
                'as_of': datetime.fromtimestamp(meta['regularMarketTime'], ZoneInfo('Asia/Seoul')).isoformat(),
                'currency': meta.get('currency'), 'source': 'Yahoo Finance'}
    except (requests.RequestException, KeyError, TypeError, IndexError, ValueError):
        return None


def matches_name(name, title):
    return re.search(r'(?<![가-힣A-Za-z0-9])' + re.escape(name) + r'(?![가-힣A-Za-z0-9])', title, re.I) is not None


def build_watchlist(news, previous=None, quote_loader=fetch_quote, now=None):
    now = now or datetime.now(ZoneInfo('Asia/Seoul'))
    eligible = [e for e in news if str(e.get('link') or e.get('url') or '').startswith(('https://', 'http://'))]
    sectors = []
    for sector in dict.fromkeys(c['sector'] for c in UNIVERSE):
        members = [c for c in UNIVERSE if c['sector'] == sector]
        words = {w for c in members for w in c['keywords']} | {c['name'] for c in members}
        evidence = [e for e in eligible if any(w.lower() in e.get('title', '').lower() for w in words)]
        if evidence:
            negative = any(any(w in e['title'] for w in NEGATIVE) for e in evidence)
            sectors.append({'name': sector, 'stance': '주의' if negative else '관찰',
                            'basis': '뉴스·이벤트 기준 · 업종 수익률 판단 아님', 'evidence': evidence[:3]})
    previous_by_ticker = {s['ticker']: s for s in (previous or {}).get('stocks', [])}
    stocks = []
    for company in UNIVERSE:
        evidence = [e for e in eligible if matches_name(company['name'], e.get('title', ''))]
        if not evidence or any(any(w in e['title'] for w in NEGATIVE) for e in evidence):
            continue
        evidence = [e for e in evidence if any(w in e['title'] for w in POSITIVE)]
        if not evidence:
            continue
        q = quote_loader(company['ticker'])
        try:
            age = (now - datetime.fromisoformat(q['as_of'])).total_seconds()
            price, volume = float(q['price']), float(q['volume'])
            if (q.get('currency') != 'KRW' or not math.isfinite(price * volume) or
                    price <= 0 or volume <= 0 or price * volume < 1_000_000_000 or not 0 <= age <= 4 * 86400):
                continue
        except (TypeError, KeyError, ValueError):
            continue
        old = previous_by_ticker.get(company['ticker'])
        urls = {e.get('link') or e.get('url') for e in evidence}
        old_urls = {e.get('link') or e.get('url') for e in (old or {}).get('evidence', [])}
        stocks.append({**company, 'quote': q, 'turnover_estimate': round(price * volume),
                       'status': '신규 관찰' if not old else '관점 유지' if urls == old_urls else '근거 변경',
                       'reason': evidence[0]['title'], 'evidence': evidence[:3],
                       'invalidation': '관련 보도의 정정·계약 취소 또는 후속 공시에서 전제 변경 확인 시 재검토',
                       'basis': '뉴스 근거 및 거래대금 근사값 확인 · 목표주가 미산정'})
    stocks.sort(key=lambda s: (-len(s['evidence']), -s['turnover_estimate']))
    selected = []
    for stock in stocks:
        if sum(s['sector'] == stock['sector'] for s in selected) < 2:
            selected.append(stock)
        if len(selected) == 3:
            break
    return {'sectors': sorted(sectors, key=lambda s: -len(s['evidence']))[:3], 'stocks': selected,
            'method': '고정 국내 후보군에서 뉴스 근거·최근 가격·거래대금 근사 10억원 이상 확인. 매수 추천 순위 아님.',
            'empty_reason': '' if selected else '선정 기준을 충족하는 근거와 가격 데이터가 부족합니다.'}
