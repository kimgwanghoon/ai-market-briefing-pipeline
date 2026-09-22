"""Observation provenance and immutable report evidence. No network or AI calls."""
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


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
                    old.get('price_basis') != current.get('price_basis')):
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


def event_insight(item):
    """Conditional language grounded only in the supplied headline."""
    title = item.get('title', '')
    rules = [
        (('유상증자',), '주의', '신주 발행에 따른 희석 가능성이 있습니다.', '발행 규모·가격, 조달 목적과 자금 사용처를 확인하세요.'),
        (('공급계약', '수주'), '관찰', '계약 이행 시 매출 기여 가능성이 있습니다.', '계약 금액·기간, 매출 대비 비중과 해지 조건을 확인하세요.'),
        (('자기주식',), '관찰', '자사주 관련 결정은 주주환원 정책에 영향을 줄 수 있습니다.', '취득·처분·소각 구분과 실제 이행 여부를 확인하세요.'),
    ]
    for words, impact, interpretation, watchpoint in rules:
        if any(word in title for word in words):
            return {'impact': impact, 'fact': title, 'interpretation': interpretation,
                    'watchpoint': watchpoint, 'basis': '제목 기반 조건부 해석 · 원문 검증 필요'}
    return {'impact': '판단 보류', 'fact': title,
            'interpretation': '제목만으로 시장 영향의 방향과 규모를 확정할 수 없습니다.',
            'watchpoint': '원문 수치와 후속 공시가 현재 판단을 뒷받침하는지 확인하세요.',
            'basis': '원문 추가 확인 필요'}


def enrich_events(payload):
    for items in [payload.get('news_items', []), *[payload.get('events', {}).get(k, []) for k in ('news', 'dart')]]:
        for item in items:
            item['research_insight'] = event_insight(item)
    return payload
