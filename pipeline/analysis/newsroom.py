"""Bounded multi-section discovery and event-level editorial selection."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

SECTIONS = {'258':'국내 시장', '262':'해외 시장', '261':'기업·산업', '263':'경제·정책', '259':'금융'}


def normalized_title(item):
    title = re.sub(r'\[[^]]*\]', '', item.get('title', '')).lower()
    title = re.sub(r'\s+-\s+[^-]+$', '', title)
    return re.sub(r'[^a-z0-9가-힣]', '', title)


def has_listing_summary(item):
    text = normalized_title({'title': item.get('listing_summary') or item.get('description') or ''})
    title = normalized_title(item)
    # RSS descriptions frequently repeat only the headline and publisher.
    return bool(text and len(text.replace(title, '', 1)) >= 30)


def category(item):
    text = item.get('title', '').lower()
    for name, words in [('정책·금리', ('연준','fomc','금리','관세','정부','정책','물가')),
                        ('기업 실적', ('실적','영업이익','매출','가이던스')),
                        ('기업 사건', ('계약','수주','인수','합병','증자','자사주')),
                        ('업종', ('반도체','광통신','배터리','바이오','자동차','방산'))]:
        if any(w in text for w in words):
            return name
    return '시장·거시'


def recap_key(item):
    text = item.get('title', '').lower()
    if category(item) in ('기업 실적','기업 사건','정책·금리'):
        return None
    if not any(w in text for w in ('증시','나스닥','코스피','반도체주','랠리')):
        return None
    region = '국내' if any(w in text for w in ('코스피','코스닥','국내')) else '해외'
    direction = '상승' if any(w in text for w in ('상승','급등','랠리','반등')) else '하락' if any(w in text for w in ('하락','급락')) else None
    theme = 'ai' if any(w in text for w in ('ai','인공지능','반도체','앤트로픽')) else '일반'
    day = str(item.get('published_at') or item.get('time') or '')[:10]
    # Broad directional headlines are not proof of the same event. Only
    # group dated, thematic recaps from the same session.
    return (day, region, direction, theme) if direction and theme != '일반' and len(day) == 10 else None


def cluster_news(items):
    clusters = []
    for item in items:
        title = normalized_title(item)
        if not title:
            continue
        url = item.get('url') or item.get('link')
        grams = {title[i:i+3] for i in range(max(1,len(title)-2))}
        match = None
        for group in clusters:
            base = group[0]
            a = normalized_title(base)
            other = {a[i:i+3] for i in range(max(1,len(a)-2))}
            similarity = len(grams & other) / max(1, len(grams | other))
            day = str(item.get('published_at') or item.get('time') or '')[:10]
            base_day = str(base.get('published_at') or base.get('time') or '')[:10]
            same_session = not day or not base_day or day == base_day
            if (url and url == (base.get('url') or base.get('link'))) or (same_session and (title == a or similarity >= .58)) or (recap_key(item) and recap_key(item) == recap_key(base)):
                match = group
                break
        if match is None:
            clusters.append([item])
        else:
            match.append(item)
    result = []
    for group in clusters:
        # A directly readable article is preferable to a discovery redirect.
        group.sort(key=lambda x: (bool(x.get('article_excerpt')), 'news.naver.com' in (x.get('url') or x.get('link') or ''), len(x.get('description') or x.get('listing_summary') or '')), reverse=True)
        item = dict(group[0])
        item['category'] = category(item)
        related = [source for x in group for source in x.get('related_sources', [])]
        related += [{'title': x.get('title'), 'url': x.get('url') or x.get('link'), 'source': x.get('source') or x.get('press')} for x in group[1:]]
        seen = {item.get('url') or item.get('link')}
        item['related_sources'] = []
        for source in related:
            if source.get('url') and source['url'] not in seen:
                item['related_sources'].append(source)
                seen.add(source['url'])
        item['related_sources'] = item['related_sources'][:4]
        item['cluster_size'] = max(len(seen), max(x.get('cluster_size', 1) for x in group))
        result.append(item)
    return result


def diverse_news(items, limit=8):
    selected, overflow, counts = [], [], {}
    for item in cluster_news(items):
        key = item['category']
        if counts.get(key,0) < 2:
            selected.append(item)
            counts[key] = counts.get(key,0)+1
        else:
            overflow.append(item)
    return (selected + overflow)[:limit]


def collect_news(now, crawler):
    def read(section):
        status = {}
        try:
            rows = crawler(now, max_articles=25, section=section, diagnostics=status)
        except Exception:
            # A failed section must not erase evidence from other sources.
            return [], {'section': SECTIONS[section], 'status': 'failed', 'accepted': 0}
        events = []
        for row in rows:
            stamp = row.get('dt')
            if not isinstance(stamp, datetime) or stamp > now:
                continue
            events.append({**row, 'url':row.get('link'), 'event_id':row.get('link'),
                           'source':row.get('press'), 'published_at':stamp.strftime('%Y-%m-%d %H:%M:%S'),
                           'listing_summary':row.get('description',''), 'discovery_section':SECTIONS[section]})
            events[-1].pop('dt',None)
        return events, {'section':SECTIONS[section], **status, 'accepted':len(events)}
    with ThreadPoolExecutor(max_workers=5) as pool:
        batches = list(pool.map(read, SECTIONS))
    rows = [row for batch,_ in batches for row in batch]
    return rows, [status for _,status in batches]


def collection_quality(raw, selected, sources):
    unique = cluster_news(raw)
    verified = sum(x.get('content_status') == 'article_verified' for x in selected)
    return {'candidate_count':len(raw), 'distinct_events':len(unique), 'selected':len(selected),
            'duplicate_ratio':round(1-len(unique)/len(raw),3) if raw else 0,
            'body_count':verified, 'body_ratio':round(verified/len(selected),3) if selected else 0,
            'sources':sources}
