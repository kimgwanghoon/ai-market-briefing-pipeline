"""Small REST adapter: explicit timeouts, no secret/response-body logging."""
import hashlib
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from pipeline.prompts.versions import VERSIONS

KST = ZoneInfo('Asia/Seoul')


def database_enabled():
    backend = os.getenv('STORAGE_BACKEND', 'json')
    if backend not in ('json', 'supabase'):
        raise ValueError('STORAGE_BACKEND must be json or supabase')
    return backend == 'supabase'


def iso(value):
    if not value:
        return None
    dt = datetime.fromisoformat(str(value))
    return (dt.replace(tzinfo=KST) if dt.tzinfo is None else dt).isoformat()


def document(kind, payload):
    markets = payload.get('market_signals', payload.get('indexes', {}))
    source_events = payload.get('events', {'news': payload.get('news_items', [])})
    events = []
    for event_type in ('news', 'dart'):
        for item in source_events.get(event_type, []):
            url = item.get('url') or item.get('link') or ''
            identity = item.get('event_id') or url or f"{item.get('title')}|{item.get('published_at', '')}"
            events.append({'id': hashlib.sha256(f'{event_type}:{identity}'.encode()).hexdigest(),
                           'event_type': event_type, 'title': item.get('title', ''),
                           'url': url, 'position': len(events), 'evidence': item})
    observed = {'markets': markets, 'events': source_events,
                'source_samples': payload.get('source_samples', []),
                'candidate_quotes': {s['ticker']: s['quote'] for s in payload.get('research', {}).get('stocks', [])}}
    analysis = {k: v for k, v in payload.items() if k not in
                ('market_signals', 'indexes', 'events', 'news_items', 'source_samples')}
    return {'title': payload.get('headline') or payload.get('title') or '장중 시장 브리핑',
            'generated_at': iso(payload.get('timestamp') or payload['generated_at']),
            'observation_start': iso(payload.get('window_start')),
            'observation_end': iso(payload.get('collection_cutoff') or payload.get('window_end')),
            'observations': observed, 'analysis': analysis, 'markets': markets, 'events': events,
            'versions': {**VERSIONS, 'text_model': os.getenv('OPENAI_TEXT_MODEL', 'gpt-4o-mini-2024-07-18')},
            'payload': payload}


class Store:
    def __init__(self, session=None):
        self.url = os.environ['SUPABASE_URL'].rstrip('/')
        key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
        if not self.url.startswith('https://') or not key:
            raise ValueError('Valid SUPABASE_URL and batch key required')
        self.session = session or requests.Session()
        self.headers = {'apikey': key, 'Content-Type': 'application/json'}
        # New sb_secret keys are API keys, not JWTs.
        if not key.startswith('sb_secret_'):
            self.headers['Authorization'] = f'Bearer {key}'

    def request(self, method, path, **kwargs):
        response = self.session.request(method, self.url + path, headers=self.headers,
                                        timeout=(10, 40), **kwargs)
        if not response.ok:
            raise RuntimeError(f'Supabase {method} failed ({response.status_code})')
        return response.json() if response.content else None

    def history(self, kind, limit=400, since=None):
        params = {'select': 'payload', 'kind': f'eq.{kind}', 'published': 'eq.true',
                  'order': 'generated_at.desc', 'limit': str(limit)}
        if since:
            params['generated_at'] = f'gte.{iso(since)}'
        return [row['payload'] for row in self.request('GET', '/rest/v1/briefings', params=params)]

    def claim(self, kind, target, token):
        return self.request('POST', '/rest/v1/rpc/claim_run',
                            json={'p_kind': kind, 'p_target': iso(target), 'p_token': token})

    def publish(self, run, token, kind, payload):
        return self.request('POST', '/rest/v1/rpc/publish_briefing',
                            json={'p_run': run, 'p_token': token, 'p_document': document(kind, payload)})

    def update_run(self, run, token, **values):
        return self.request('PATCH', '/rest/v1/pipeline_runs',
                            params={'id': f'eq.{run}', 'token': f'eq.{token}'}, json=values)

    def upload_cover(self, run, path):
        path = Path(path)
        content_types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                         '.webp': 'image/webp', '.svg': 'image/svg+xml'}
        if path.suffix.lower() not in content_types or not path.is_file():
            return None
        name = f'{run}/{path.name}'
        headers = {**self.headers, 'Content-Type': content_types[path.suffix.lower()]}
        response = self.session.post(f'{self.url}/storage/v1/object/research-covers/{quote(name)}',
                                     headers=headers, data=path.read_bytes(), timeout=(10, 60))
        if not response.ok:
            raise RuntimeError(f'Cover upload failed ({response.status_code})')
        return f'{self.url}/storage/v1/object/public/research-covers/{quote(name)}'

    def delete_cover(self, url):
        prefix = f'{self.url}/storage/v1/object/public/research-covers/'
        if url and url.startswith(prefix):
            from urllib.parse import unquote
            self.request('DELETE', '/storage/v1/object/research-covers',
                         json={'prefixes': [unquote(url[len(prefix):])]})
