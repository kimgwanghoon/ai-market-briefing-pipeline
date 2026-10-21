"""Session-specific editorial context. Rules, not price predictions."""
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def edition_context(indexes, history, now):
    morning = now.hour < 12
    def quote(key, label):
        value = indexes.get(key, {})
        return f"{label} {value.get('price', '미확인')} ({value.get('change', '미확인')}) · {value.get('as_of', '기준시각 미제공')}"
    context = {
        'session': 'morning' if morning else 'evening',
        'title': '미국장 결과와 국내장 준비' if morning else '국내장 복기와 미국장 준비',
        'facts': [quote('sp500', 'S&P 500'), quote('nasdaq', 'NASDAQ')] if morning else [quote('kospi', 'KOSPI'), quote('kosdaq', 'KOSDAQ')],
        'upside': ('국내 개장 후 KOSPI·KOSDAQ이 최근 종가 위에서 유지되고 USD/KRW 상승 압력이 줄면 위험 선호의 국내 확산 여부를 확인합니다.' if morning else
                   '미국 개장 후 S&P 500·NASDAQ 상승과 VIX 안정이 함께 확인되면 위험 선호가 이어지는지 확인합니다.'),
        'downside': ('국내 지수가 최근 종가를 밑돌고 USD/KRW 상승이 동반되면 미국장 분위기의 국내 확산 가정을 재검토합니다.' if morning else
                     '미국 지수 하락과 VIX 상승이 동반되면 국내 마감 분위기를 미국장에 연장하는 가정을 재검토합니다.'),
        'limits': '조건부 시나리오이며 상승 확률·목표가가 아닙니다. 뉴스에 등장하는 주요 미국 종목은 최근 정규장 가격을 함께 확인합니다. 시간외·선물·공식 경제일정은 미수집 상태입니다. 지표별 기준시각을 확인하세요.',
        'review': [],
    }
    if not morning:
        for old in history:
            try:
                stamp = datetime.fromisoformat(old.get('timestamp', '').replace('Z', '+00:00'))
                stamp = stamp.replace(tzinfo=KST) if stamp.tzinfo is None else stamp.astimezone(KST)
            except (ValueError, TypeError):
                continue
            if stamp.date() != now.date() or stamp.hour >= 12:
                continue
            context['morning_report_id'] = old.get('id')
            context['review'].append('아침 관점: ' + old.get('daily_overview', {}).get('interpretation', '관점 미기록'))
            for key, label in [('kospi', 'KOSPI'), ('kosdaq', 'KOSDAQ')]:
                before = old.get('indexes', {}).get(key, {})
                after = indexes.get(key, {})
                context['review'].append(f"{label}: 아침 참고값 {before.get('price', '미확인')} → 마감 관측값 {after.get('price', '미확인')} · 당일 변화 {after.get('change', '미확인')}")
            context['review'].append('관측값 비교이며 뉴스의 인과관계나 전망 적중을 증명하지 않습니다.')
            break
        if not context['review']:
            context['review'] = ['동일 날짜의 아침 보고서가 없어 아침 관점 대비 복기는 보류합니다.']
    return context
