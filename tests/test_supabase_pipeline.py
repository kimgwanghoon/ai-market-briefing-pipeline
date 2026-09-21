import os
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from pipeline.storage.supabase import Store, document, iso
from pipeline.analysis.watchlist import build_watchlist
from pipeline.jobs.run import target_time, weekly, notify, run


class SupabaseContractTests(unittest.TestCase):
    def test_kst_and_source_evidence(self):
        self.assertEqual(iso('2026-09-18 09:00:00'), '2026-09-18T09:00:00+09:00')
        p = {'timestamp': '2026-09-18 09:01:00', 'window_start': '2026-09-18 08:50:00',
             'window_end': '2026-09-18 09:00:00', 'market_signals': {'kospi': {'price':'100'}},
             'events': {'news': [{'title':'테스트','url':'https://example.com/1'}]}, 'key_points':['분석']}
        d = document('live', p)
        self.assertEqual(d['observations']['markets']['kospi']['price'], '100')
        self.assertNotIn('market_signals', d['analysis'])
        self.assertEqual(d['analysis']['key_points'], ['분석'])
        self.assertEqual(document('live', p)['events'][0]['id'], d['events'][0]['id'])

    @patch.dict(os.environ, {'SUPABASE_URL':'https://example.supabase.co','SUPABASE_SERVICE_ROLE_KEY':'sb_secret_test'})
    def test_secret_key_is_not_used_as_jwt(self):
        store = Store(session=Mock())
        self.assertNotIn('Authorization', store.headers)
        self.assertEqual(store.headers['apikey'], 'sb_secret_test')

    @patch.dict(os.environ, {'SUPABASE_URL':'https://example.supabase.co','SUPABASE_SERVICE_ROLE_KEY':'key'})
    def test_history_is_filtered_and_error_does_not_expose_body(self):
        session = Mock()
        session.request.return_value = Mock(ok=True,content=b'[]',json=lambda:[])
        store = Store(session=session)
        self.assertEqual(store.history('live',limit=30), [])
        self.assertEqual(session.request.call_args.kwargs['params']['kind'], 'eq.live')
        session.request.return_value = Mock(ok=False,status_code=401,text='SECRET_RESPONSE')
        with self.assertRaisesRegex(RuntimeError, r'failed \(401\)') as ctx:
            store.history('live')
        self.assertNotIn('SECRET', str(ctx.exception))

    @patch.dict(os.environ, {'SCHEDULE_TARGET_KST':'2026-09-18 10:00:00'})
    def test_future_target_rejected(self):
        with self.assertRaises(ValueError):
            target_time('live', datetime(2026,9,18,9,0,tzinfo=ZoneInfo('Asia/Seoul')))

    def test_weekly_does_not_predict_with_no_observations(self):
        store = Mock(); store.history.return_value = []
        p = weekly(store)
        self.assertEqual(p['next_week_outlook']['bias'], '데이터 축적 중')
        self.assertEqual(p['summary']['count'], 0)

    @patch.dict(os.environ, {'DISCORD_WEBHOOK_URL':'https://example.com/test'})
    @patch('discord_notifications.post_discord_webhook', return_value=True)
    def test_daily_notification_uses_fixed_report_link(self, post):
        p = {'id':'report-id','edition_title':'아침','timestamp':'2026-09-18 08:00:00',
             'headline':'테스트','summary_items':['[한국 시장]','분석'],'indexes':{},'news_items':[]}
        self.assertEqual(notify('daily',p,'https://example.com'), 'sent')
        self.assertEqual(post.call_args.args[1]['url'], 'https://example.com/daily?id=report-id')


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,9,18,9,0,tzinfo=ZoneInfo('Asia/Seoul'))
        self.quote = {'price':100000,'volume':100000,'currency':'KRW','as_of':'2026-09-18T09:00:00+09:00'}

    def test_no_evidence_no_quote_requests(self):
        loader = Mock()
        self.assertEqual(build_watchlist([],quote_loader=loader,now=self.now)['stocks'], [])
        loader.assert_not_called()

    def test_evidence_and_quote_gates(self):
        news=[{'title':'삼성전자 수주 확대','link':'https://example.com/news'}]
        p=build_watchlist(news,quote_loader=lambda _:self.quote,now=self.now)
        self.assertEqual(p['stocks'][0]['name'],'삼성전자')
        self.assertEqual(p['stocks'][0]['status'],'신규 관찰')
        self.assertEqual(build_watchlist(news,p,lambda _:self.quote,self.now)['stocks'][0]['status'],'관점 유지')
        for quote in [None,{**self.quote,'currency':'USD'},{**self.quote,'volume':1},
                      {**self.quote,'as_of':'2026-09-01T09:00:00+09:00'}, {**self.quote,'price':float('nan')}]:
            self.assertEqual(build_watchlist(news,quote_loader=lambda _:quote,now=self.now)['stocks'],[])

    def test_negative_evidence_and_name_substring_excluded(self):
        for title in ['삼성전자 수주 취소 우려','삼성전자서비스 수주 확대']:
            result=build_watchlist([{'title':title,'link':'https://example.com'}],quote_loader=lambda _:self.quote,now=self.now)
            self.assertEqual(result['stocks'],[])


@patch.dict(os.environ, {'SITE_URL':'https://desk.example.com','SCHEDULE_TARGET_KST':''})
class RunLifecycleTests(unittest.TestCase):
    @patch('pipeline.jobs.run.notify')
    @patch('pipeline.jobs.run.live')
    @patch('pipeline.jobs.run.Store')
    def test_duplicate_does_not_collect_or_notify(self, store_cls, collect, alert):
        store_cls.return_value.claim.return_value = None
        run('live')
        collect.assert_not_called()
        alert.assert_not_called()

    @patch('pipeline.jobs.run.notify', return_value='sent')
    @patch('pipeline.jobs.run.live', return_value={'timestamp':'2026-09-18 09:00:00'})
    @patch('pipeline.jobs.run.Store')
    def test_save_before_notification(self, store_cls, collect, alert):
        store = store_cls.return_value
        store.claim.return_value = {'id':'run-id'}
        def check_saved(*args):
            store.publish.assert_called_once()
            return 'sent'
        alert.side_effect = check_saved
        run('live')
        self.assertEqual(store.publish.call_args.args[3]['id'], 'run-id')
        self.assertEqual(store.update_run.call_args.kwargs['notification_status'], 'sent')

    @patch('pipeline.jobs.run.notify')
    @patch('pipeline.jobs.run.live', side_effect=ValueError('bad observation'))
    @patch('pipeline.jobs.run.Store')
    def test_generation_failure_is_not_published(self, store_cls, collect, alert):
        store = store_cls.return_value
        store.claim.return_value = {'id':'run-id'}
        store.request.return_value = [{'status':'running'}]
        with self.assertRaises(ValueError):
            run('live')
        store.publish.assert_not_called()
        alert.assert_not_called()
        self.assertEqual(store.update_run.call_args.kwargs['status'],'failed')

    @patch('pipeline.jobs.run.notify', return_value='sent')
    @patch('pipeline.jobs.run.live', return_value={'timestamp':'2026-09-18 09:00:00'})
    @patch('pipeline.jobs.run.Store')
    def test_commit_response_loss_is_confirmed(self, store_cls, collect, alert):
        store=store_cls.return_value
        store.claim.return_value={'id':'run-id'}
        store.publish.side_effect=TimeoutError()
        store.request.return_value=[{'status':'complete'}]
        run('live')
        self.assertFalse(any(c.kwargs.get('status')=='failed' for c in store.update_run.call_args_list))
        alert.assert_called_once()
