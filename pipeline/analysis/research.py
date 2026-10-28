"""Observation provenance and immutable report evidence. No network or AI calls."""
import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def classify_event(item):
    """Classify evidence without turning a company headline into a market call."""
    text = f"{item.get('corp_name', '')} {item.get('title', '')}".lower()
    if any(word in text for word in ('kospi', 'kosdaq', '미 증시', '연준', 'fomc', '환율', '국채', '유가', '관세')):
        return '시장'
    if any(word in text for word in ('반도체', '바이오', '자동차', '전력', '방산', '은행', '조선', '2차전지', '인터넷')):
        return '업종'
    return '기업'


def compact_fact(value, limit=300):
    """Keep article evidence readable; avoid dumping a raw scraped paragraph."""
    cleaned = ' '.join(str(value or '').split())
    if not cleaned:
        return ''
    sentences = re.split(r'(?<=[.!?다])\s+', cleaned)
    useful = [sentence for sentence in sentences if len(sentence) >= 24 and not any(
        marker in sentence.lower() for marker in ('무단전재', 'copyright', '사진=', '기자=')
    )]
    result = ' '.join(useful[:2]) or cleaned
    return result[:limit].rstrip() + ('…' if len(result) > limit else '')


def source_time(value):
    if value is None or value == '':
        return None
    try:
        if isinstance(value, (float, int)):
            dt = datetime.fromtimestamp(value, KST)
        else:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (dt.replace(tzinfo=KST) if dt.tzinfo is None else dt.astimezone(KST)).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def retain_identical_close(markets, history):
    """Keep an already observed identical close when the vendor revises its time.

    Never reuse changing/live prices or a prior day's quote. Preserve the old
    collection time and disclose both the reuse and the received older time.
    The normal regression/conflict guard still runs after this narrow repair.
    """
    result = {key: dict(value) for key, value in markets.items()}
    for key, current in markets.items():
        stamp = source_time(current.get('source_timestamp'))
        if not stamp or current.get('market_status') != '최근 종가' or current.get('price') in (None,'N/A'):
            continue
        candidates = []
        for report in history:
            old = (report.get('indexes') or report.get('market_signals') or {}).get(key, {})
            old_stamp = source_time(old.get('source_timestamp'))
            if (old_stamp and old_stamp > stamp and old_stamp[:10] == stamp[:10]
                    and old.get('market_status') == '최근 종가'
                    and all(old.get(field) == current.get(field) for field in ('source','price_basis','price','change'))
                    and old.get('source') and old.get('price_basis')):
                candidates.append((old_stamp, old))
        if candidates:
            _, old = max(candidates, key=lambda candidate:candidate[0])
            result[key] = {**old, 'received_source_timestamp':stamp,
                           'source_warning':'제공처 기준시각이 후퇴해 같은 날짜·출처의 동일 종가 관측값을 유지했습니다. 수치와 기존 수집시각은 변경하지 않았습니다.'}
    return result


def exclude_invalid_observations(markets, history=()):
    """Quarantine rejected values; callers must check coverage after exclusion."""
    result = {}
    for symbol, quote in markets.items():
        try:
            snapshot({symbol: quote}, history)
            result[symbol] = dict(quote)
        except ValueError as exc:
            result[symbol] = {
                'price': 'N/A', 'change': '-', 'trend': '확인 불가',
                'color': '#64748b', 'market_status': '검증 제외',
                'as_of': '유효한 기준시각 확인 필요',
                'source': quote.get('source'),
                'collected_at': quote.get('collected_at'),
                'source_warning': '제공처 시세의 기준시각 또는 수치가 기존 관측과 충돌해 이번 분석에서 제외했습니다.',
                'rejected_observation': {'reason': str(exc), 'quote': dict(quote)},
            }
    return result


def snapshot(markets, history=()):
    """Refuse source regressions/conflicts before scoring, preserving history."""
    for symbol, current in markets.items():
        stamp = source_time(current.get('source_timestamp'))
        if not stamp:
            continue
        current_dt = datetime.fromisoformat(stamp)
        collected = source_time(current.get('collected_at'))
        if collected and current_dt > datetime.fromisoformat(collected):
            raise ValueError(f'Future source timestamp: {symbol}')
        for report in history:
            old = (report.get('market_signals') or report.get('indexes') or {}).get(symbol, {})
            old_stamp = source_time(old.get('source_timestamp'))
            if (not old_stamp or old.get('source') != current.get('source') or
                    old.get('price_basis') != current.get('price_basis') or
                    ('개장 전' in old.get('market_status', '') or old.get('market_status') == '장전') !=
                    ('개장 전' in current.get('market_status', '') or current.get('market_status') == '장전')):
                continue
            old_dt = datetime.fromisoformat(old_stamp)
            if current_dt < old_dt:
                raise ValueError(f'Regressing source timestamp: {symbol}')
            if current_dt == old_dt and old.get('price') != current.get('price'):
                raise ValueError(f'Conflicting source observation: {symbol}')
    identity = {k: {field: v.get(field) for field in
                ('price', 'change', 'source', 'source_timestamp', 'price_basis')}
                for k, v in sorted(markets.items())}
    return {'id': hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
            'schema': 'market-observation-v1', 'cross_source_validation': 'not_performed'}


def filing_needs_review(title):
    return any(word in str(title) for word in ('기각', '철회', '취소', '정정'))


def event_insight(item):
    """Use a collected article body when available; otherwise remain explicit about the limit."""
    title = item.get('title', '')
    if filing_needs_review(title) and (item.get('corp_name') or item.get('event_type') == 'dart' or str(item.get('content_status', '')).startswith('filing')):
        return {'impact': '판단 보류', 'fact': title,
                'interpretation': '기각·철회·취소·정정의 대상과 변경 내용을 확인해야 하므로 제목의 단어만으로 호재·악재를 판단하지 않습니다.',
                'watchpoint': '원공시와 변경 공시의 대상·금액·효력 발생 여부를 비교하세요.',
                'basis': '공시 문맥 추가 확인 필요'}
    excerpt = compact_fact(item.get('article_excerpt', ''), limit=340)
    event_scope = classify_event(item)
    if excerpt:
        return {
            'impact': '원문 확인',
            'fact': excerpt,
            'interpretation': '기사 본문에서 확인된 내용을 바탕으로 정리했습니다. 시장 전체 영향은 지수·후속 뉴스와 함께 확인해야 합니다.',
            'watchpoint': '기사에 제시된 수치·일정과 후속 시장 반응이 이어지는지 확인하세요.',
            'basis': f"{'공시 원문' if item.get('content_status') == 'filing_verified' else '원문 본문'} 확인 · {event_scope} 영향은 조건부 해석",
        }
    listing_summary = compact_fact(item.get('listing_summary', ''), limit=300)
    if listing_summary:
        return {
            'impact': '요약 확인',
            'fact': listing_summary,
            'interpretation': '뉴스 목록에 제공된 요약을 바탕으로 표시했습니다. 원문 본문을 확인하기 전에는 영향의 방향과 규모를 단정하지 않습니다.',
            'watchpoint': '원문 기사와 후속 시장 반응을 함께 확인하세요.',
            'basis': '뉴스 목록 요약 확인 · 원문 본문 검증 필요',
        }
    rules = [
        (('유상증자',), '주의', '신주 발행에 따른 희석 가능성이 있습니다.', '발행 규모·가격, 조달 목적과 자금 사용처를 확인하세요.'),
        (('공급계약', '수주'), '관찰', '계약 이행 시 매출 기여 가능성이 있습니다.', '계약 금액·기간, 매출 대비 비중과 해지 조건을 확인하세요.'),
        (('자기주식',), '관찰', '자사주 관련 결정은 주주환원 정책에 영향을 줄 수 있습니다.', '취득·처분·소각 구분과 실제 이행 여부를 확인하세요.'),
    ]
    for words, impact, interpretation, watchpoint in rules:
        if any(word in title for word in words):
            return {'impact': impact, 'fact': title, 'interpretation': interpretation,
                    'watchpoint': watchpoint, 'basis': '제목 기반 조건부 해석 · 원문 검증 필요'}
    source = '공시 제목' if item.get('content_status') == 'filing_title_only' else '제목'
    return {'impact': '판단 보류', 'fact': title,
            'interpretation': '제목만으로 시장 영향의 방향과 규모를 확정할 수 없습니다.',
            'watchpoint': '원문 수치와 후속 공시가 현재 판단을 뒷받침하는지 확인하세요.',
            'basis': f'{source} 기반 · 원문 추가 확인 필요'}


def enrich_events(payload):
    sources = [payload.get('news_items', []), *[payload.get('events', {}).get(k, []) for k in ('news', 'dart')]]
    for items in sources:
        for item in items:
            item['scope'] = classify_event(item)
            item['research_insight'] = event_insight(item)
    groups = {'article_verified': 0, 'filing_verified': 0, 'listing_summary': 0, 'title_only': 0}
    seen = set()
    for items in sources:
        for item in items:
            identity = item.get('event_id') or item.get('url') or item.get('link') or item.get('title')
            if identity in seen:
                continue
            seen.add(identity)
            status = item.get('content_status', '')
            if status == 'article_verified': groups['article_verified'] += 1
            elif status == 'filing_verified': groups['filing_verified'] += 1
            elif status == 'listing_summary': groups['listing_summary'] += 1
            else: groups['title_only'] += 1
    payload['evidence_quality'] = groups
    total = sum(groups.values())
    verified = groups['article_verified'] + groups['filing_verified']
    payload['evidence_assessment'] = {
        'total': total, 'verified': verified,
        'status': '근거 부족' if not total or verified == 0 else '부분 확인' if verified < total else '본문 확보',
        'guidance': ('본문 근거가 없어 뉴스 기반 원인·전망 판단을 보류합니다.' if not verified else
                     f'{total}건 중 {verified}건의 본문을 확보했습니다. 본문 확보는 사실의 독립 검증이나 예측 정확도를 뜻하지 않습니다.'),
    }
    return payload
