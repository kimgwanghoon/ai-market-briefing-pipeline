"""Independent publication watchdog. Never collects data or reruns AI jobs."""
import argparse
import json
import os
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def scheduled_check_time(now, cron):
    """Preserve the intended check slot if GitHub starts the cron late."""
    schedules = {
        '45 23 * * 0-4': (45, [23], {6,0,1,2,3}),
        '45 0-6,9 * * 1-5': (45, [0,1,2,3,4,5,6,9], {0,1,2,3,4}),
        '15 7 * * 1-5': (15, [7], {0,1,2,3,4}),
        '0 1 * * 6': (0, [1], {5}),
    }
    if cron not in schedules:
        return now
    minute, hours, weekdays = schedules[cron]
    utc = now.astimezone(ZoneInfo('UTC'))
    candidates = []
    for days in range(8):
        day = utc - timedelta(days=days)
        for hour in hours:
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate.weekday() in weekdays and candidate <= utc:
                candidates.append(candidate)
    return max(candidates).astimezone(KST) if candidates else now


def due_slots(now):
    now = now.astimezone(KST)
    if now.weekday() == 5 and now.hour == 10:
        return [('weekly', (now-timedelta(days=5)).replace(hour=0, minute=0, second=0, microsecond=0))]
    if now.weekday() > 4:
        return []
    slots = []
    # Each scheduled invocation checks one edition once, after a grace period.
    if now.hour in (8, 18) and now.minute >= 40:
        slots.append(('daily', now.replace(minute=0, second=0, microsecond=0)))
    if 9 <= now.hour <= 15 and now.minute >= 40:
        slots.append(('live', now.replace(minute=0, second=0, microsecond=0)))
    if now.hour == 16 and now.minute >= 15:
        slots.append(('live', now.replace(hour=15, minute=30, second=0, microsecond=0)))
    return slots


def alert(message):
    webhook = os.getenv('DISCORD_WEBHOOK_URL', '')
    if not webhook:
        print('Operations alert unavailable: webhook not configured')
        return False
    body = json.dumps({'content': message[:1900], 'allowed_mentions': {'parse': []}}).encode()
    request = urllib.request.Request(webhook, data=body, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception:
        print('Operations alert delivery failed (details redacted)')
        return False


def check(now=None):
    from pipeline.storage.supabase import Store
    now = now or datetime.now(KST)
    store, issues = Store(), []
    scheduled = scheduled_check_time(now, os.getenv('EXPECTED_CRON', ''))
    for kind, target in due_slots(scheduled):
        rows = store.request('GET', '/rest/v1/pipeline_runs', params={
            'kind': f'eq.{kind}', 'target_at': f'eq.{target.isoformat()}',
            'select': 'status,notification_status', 'limit': '1'})
        if not rows or rows[0]['status'] != 'complete':
            issues.append(f'{kind} {target:%m-%d %H:%M} KST 예약분 미발행: QStash 전달·Actions 로그 확인')
        elif rows[0]['notification_status'] in ('failed', 'unknown', 'sending', 'pending'):
            issues.append(f'{kind} {target:%m-%d %H:%M} KST 발행 완료, Discord 전달 확인 필요')
    if issues:
        raise RuntimeError('Publication health check failed: ' + '; '.join(issues))
    print('Publication health check passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--failure', action='store_true')
    args = parser.parse_args()
    if args.failure:
        repository = os.getenv('GITHUB_REPOSITORY', '')
        run_id = os.getenv('GITHUB_RUN_ID', '')
        if not alert(f'시장 브리핑 작업 실패 · {os.getenv("GITHUB_WORKFLOW", "pipeline")}\nhttps://github.com/{repository}/actions/runs/{run_id}\n기존 발행 자료는 유지됩니다. 원인 확인 후 수동 재실행하세요.'):
            raise SystemExit(1)
    else:
        check()
