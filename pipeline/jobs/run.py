"""DB jobs reuse existing collectors/analysis, never read or write snapshot JSON."""
import argparse
import logging
import os
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pipeline.storage.supabase import Store, iso
from pipeline.analysis.watchlist import build_watchlist

KST = ZoneInfo('Asia/Seoul')


def target_time(kind, now):
    now = now.astimezone(KST)
    explicit = os.getenv('SCHEDULE_TARGET_KST', '').strip()
    if explicit:
        dt = datetime.fromisoformat(iso(explicit))
        if dt > now:
            raise ValueError('Cannot collect a future target')
        return dt
    if kind == 'daily':
        # Only the regular edition hours share a scheduled target. An off-hour
        # manual run must not reserve a future edition or collide with a past one.
        if now.hour in (8, 18):
            return now.replace(minute=0, second=0, microsecond=0)
        return now.replace(second=0, microsecond=0)
    if kind == 'weekly':
        return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(minute=30 if now.hour == 15 and now.minute >= 30 else 0, second=0, microsecond=0)


def daily(store):
    import main as m
    now = datetime.now(KST)
    m.CURRENT_TIME_STR = now.strftime('%Y-%m-%d %H:%M:%S')
    m.IS_MORNING = now.hour < 12
    m.EDITION_TITLE = '아침 시장 브리핑' if m.IS_MORNING else '마감 시장 브리핑'
    indexes = {'kospi': m.get_korean_index_data('KOSPI'), 'kosdaq': m.get_korean_index_data('KOSDAQ')}
    indexes.update({k: m.get_index_data(t) for k, t in
                    {'sp500': '^GSPC', 'dow': '^DJI', 'nasdaq': '^IXIC', 'ewy': 'EWY',
                     'vix': '^VIX', 'usdkrw': 'KRW=X', 'us10y': '^TNX', 'wti': 'CL=F'}.items()})
    m.require_market_coverage(indexes, minimum=6, required=('kospi', 'kosdaq'))
    raw_news = m.crawl_naver_news(now)
    cutoff = datetime.now(KST)
    for item in raw_news:
        item.pop('dt', None)
    headline, points, cover = m.generate_ai_briefing(*(indexes[k] for k in
                               ('kospi','kosdaq','sp500','dow','nasdaq','ewy','vix','usdkrw','us10y','wti')))
    history = store.history('daily', limit=7)
    news = m.select_top_news(raw_news, max_count=5)
    research = build_watchlist(raw_news, history[0].get('research') if history else None)
    return {'timestamp': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'), 'headline': headline,
            'edition_title': m.EDITION_TITLE, 'summary_items': points, 'indexes': indexes,
            'news_items': news, 'events': {'news': raw_news, 'dart': []},
            'cover_image': cover, 'research': research, 'collection_cutoff': cutoff.isoformat(),
            'risk_trends': m.build_risk_trends(history, indexes), 'run_source': 'supabase'}


def live(store):
    import intraday as i
    history = store.history('live', limit=400)
    stats = i.build_component_stats(history)
    result = i.calibrate_weights(history, stats)
    calibration = {'stats': stats, 'weights': result.get('weights', {'market': .35, 'news': .2, 'dart': .25, 'sector': .2}),
                   'metric': result.get('metric', 0), 'samples': result.get('samples', 0),
                   'model_version': 'v2.0-calibrated', 'mode': result.get('mode', 'default')}
    indexes = i.fetch_market_signals()
    i.require_market_coverage(indexes, minimum=5, required=('kospi', 'kosdaq'))
    news, darts = i.fetch_naver_news(limit=20), i.fetch_dart_events(limit=20)
    cutoff = datetime.now(i.KST)
    start, end = i.observation_window(history, cutoff)
    news = i.score_news_events(i.filter_unseen_events(news, history, 'news', cutoff))
    darts = i.score_dart_events(i.filter_unseen_events(darts, history, 'dart', cutoff))
    sectors = i.detect_sector_rotation(news, darts)
    sentiment = i.build_sentiment(indexes, news, darts, sectors, calibration)
    points, watchpoint = i.build_llm_points(indexes, sentiment, news, darts)
    payload = {'timestamp': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'),
               'window_start': start.strftime('%Y-%m-%d %H:%M:%S'), 'window_end': end.strftime('%Y-%m-%d %H:%M:%S'),
               'collection_cutoff': end.strftime('%Y-%m-%d %H:%M:%S'), 'market_signals': indexes,
               'events': {'news': news, 'dart': darts, 'news_count': len(news), 'dart_count': len(darts)},
               'sentiment': sentiment, 'key_points': points, 'watchpoint': watchpoint,
               'sector_rotation': sectors, 'calibration': calibration,
               'execution': {'scheduled_target_kst': os.getenv('SCHEDULE_TARGET_KST', '')}}
    payload['day_over_day'] = i.build_day_over_day_comments(payload, history)
    payload['comparison'] = i.build_score_comparison(payload, history)
    payload['reliability'] = i.compute_reliability(history)
    return payload


def weekly(store):
    import weekly_report as w
    snapshots = sorted(store.history('live', limit=1000, since=datetime.now(KST) - timedelta(days=7)), key=lambda p: p['timestamp'])
    summary = w.build_week_summary(snapshots)
    days = summary.get('trading_days', 0)
    if days < 4:
        summary['next_week_outlook'] = {'bias': '데이터 축적 중', 'expected_range': '산출 보류',
            'confidence': '자료 부족', 'rationale': [f'관측 {days}일, {len(snapshots)}건'],
            'upside_conditions': [], 'downside_conditions': []}
    return {'generated_at': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'),
            'title': f"주간 전략 · {summary.get('period_start', '-')} ~ {summary.get('period_end', '-')}",
            'summary': summary, 'source_samples': snapshots,
            **{k: summary.get(k, []) for k in ('daily_points','market_performance','risk_events','opportunity_events')},
            'next_week_outlook': summary['next_week_outlook']}


def notify(kind, payload, site_url):
    from discord_notifications import build_daily_embed, build_intraday_embed, build_weekly_embed, post_discord_webhook
    webhook = os.getenv('DISCORD_WEBHOOK_URL', '')
    if not webhook:
        return 'skipped'
    if kind == 'daily':
        embed = build_daily_embed(payload['edition_title'], payload['timestamp'], payload['headline'], payload['summary_items'], payload['indexes'],
                                  payload['news_items'], site_url)
    elif kind == 'live':
        embed = build_intraday_embed(payload, site_url)
    else:
        embed = build_weekly_embed(payload, site_url)
    embed['url'] = f"{site_url.rstrip('/')}/{kind}?id={payload['id']}"
    return 'sent' if post_discord_webhook(webhook, embed) else 'failed'


def run(kind):
    site_url = os.environ.get('SITE_URL', '').strip()
    if not site_url.startswith('https://'):
        raise ValueError('Set SITE_URL to the deployed HTTPS frontend URL before cutover')
    store, token = Store(), str(uuid.uuid4())
    target = target_time(kind, datetime.now(KST))
    claim = store.claim(kind, target.isoformat(), token)
    if not claim:
        print('Target already claimed or completed; skipped')
        return
    run_id, cover_url = claim['id'], None
    try:
        payload = {'daily': daily, 'live': live, 'weekly': weekly}[kind](store)
        payload['id'] = run_id
        if kind == 'daily':
            from main import OUTPUT_DIR
            try:
                cover_url = store.upload_cover(run_id, OUTPUT_DIR / payload.get('cover_image', ''))
            except Exception:
                logging.warning('Cover unavailable; publishing text report')
            payload['cover_image'] = cover_url or ''
        store.publish(run_id, token, kind, payload)
    except Exception as exc:
        # A response timeout may occur after DB commit: confirm before treating it as failure.
        rows = store.request('GET', '/rest/v1/pipeline_runs', params={'id': f'eq.{run_id}', 'select': 'status'})
        if rows and rows[0]['status'] == 'complete':
            print('Publication confirmed after interrupted response')
        else:
            if cover_url:
                try:
                    store.delete_cover(cover_url)
                except Exception:
                    logging.warning('Orphan cover cleanup required for run %s', run_id)
            store.update_run(run_id, token, status='failed', error_code=type(exc).__name__, finished_at=datetime.now(KST).isoformat())
            raise
    store.update_run(run_id, token, notification_status='sending')
    try:
        status = notify(kind, payload, site_url)
    except Exception:
        status = 'unknown'
    store.update_run(run_id, token, notification_status=status)
    print(f'Published {kind}: {run_id}; notification: {status}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=['live','daily','weekly'])
    run(parser.parse_args().kind)
