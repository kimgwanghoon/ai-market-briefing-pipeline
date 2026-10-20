"""Independent price-source sanity check, never substitutes a quote silently."""
from datetime import datetime
import math


def compare_quote(primary, secondary):
    result = {'primary_source': primary.get('source', '미기록'),
              'secondary_source': secondary.get('source', '미기록'),
              'status': 'not_comparable', 'reason': '출처 또는 시각·가격 부족'}
    try:
        a = float(str(primary['price']).replace(',', ''))
        b = float(str(secondary['price']).replace(',', ''))
        ta = datetime.fromisoformat(primary['source_timestamp'].replace('Z', '+00:00'))
        tb = datetime.fromisoformat(secondary['source_timestamp'].replace('Z', '+00:00'))
        if not all(math.isfinite(x) and x > 0 for x in (a,b)) or not ta.tzinfo or not tb.tzinfo:
            return result
        if not primary.get('source') or primary['source'] == secondary.get('source'):
            return result
        if abs((ta-tb).total_seconds()) > 120:
            result['reason'] = '출처별 관측시각 차이가 2분 초과하여 비교 보류'
            return result
        deviation = abs(a-b)/a*100
        result.update(status='consistent' if deviation <= .1 else 'different',
                      reason='2분 이내 관측 가격의 상대 차이 점검 · 동일 체결 검증 아님',
                      deviation_pct=round(deviation,4), secondary_price=b,
                      secondary_at=secondary['source_timestamp'])
    except (KeyError, ValueError, TypeError):
        pass
    return result


def crosscheck(indexes, loader):
    checks = {}
    for key, ticker in [('kospi', '^KS11'), ('kosdaq', '^KQ11')]:
        try:
            checks[key] = compare_quote(indexes.get(key, {}), loader(ticker))
        except Exception:
            checks[key] = {'status': 'unavailable', 'reason': '보조 출처 조회 실패 · 주 출처 유지'}
    return checks
