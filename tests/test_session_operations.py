import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
from pipeline.analysis.session import edition_context
from pipeline.analysis.research import enrich_events, retain_identical_close, snapshot, exclude_invalid_observations
from pipeline.jobs.health import due_slots, check, scheduled_check_time
from pipeline.jobs.run import market_participation
from pipeline.analysis.crosscheck import compare_quote

KST = ZoneInfo('Asia/Seoul')


class SessionOperationsTests(unittest.TestCase):
    def test_rejected_quotes_are_excluded_and_still_subject_to_coverage(self):
        from main import require_market_coverage
        old = {'price':'100', 'source':'Yahoo', 'price_basis':'regular_trade',
               'source_timestamp':'2026-10-21T05:45:00+09:00', 'collected_at':'2026-10-21T16:00:00+09:00'}
        good = {'price':'200'}
        for change in ({'source_timestamp':'2026-10-21T05:00:00+09:00'},
                       {'price':'101'}, {'source_timestamp':'2026-10-22T05:00:00+09:00'}):
            bad = {**old, **change}
            result = exclude_invalid_observations({'dow':bad, 'kospi':good}, [{'indexes':{'dow':old}}])
            self.assertEqual(result['dow']['price'], 'N/A')
            self.assertNotIn('source_timestamp', result['dow'])
            self.assertEqual(result['dow']['rejected_observation']['quote'], bad)
            self.assertIn('제외', result['dow']['source_warning'])
            self.assertEqual(result['kospi'], good)
            snapshot(result)
            with self.assertRaises(RuntimeError):
                require_market_coverage(result, minimum=2)
            with self.assertRaises(RuntimeError):
                require_market_coverage(result, minimum=1, required=('dow',))

    def test_identical_close_reuse_is_disclosed_and_preserves_collection_time(self):
        old={'price':'100','change':'+1%','source':'Yahoo','price_basis':'regular_trade','market_status':'최근 종가',
             'source_timestamp':'2026-10-21T05:45:00+09:00','collected_at':'2026-10-21T08:00:00+09:00'}
        history=[{'indexes':{'sp500':old}}]
        current={**old,'source_timestamp':'2026-10-21T05:00:00+09:00','collected_at':'2026-10-21T16:00:00+09:00'}
        fixed=retain_identical_close({'sp500':current},history)
        self.assertEqual(fixed['sp500']['collected_at'],old['collected_at'])
        self.assertIn('source_warning',fixed['sp500'])
        snapshot(fixed,history)
        for changed in ({'price':'99'},{'market_status':'장중'},{'source_timestamp':'2026-10-20T05:00:00+09:00'}):
            bad={'sp500':{**current,**changed}}
            self.assertEqual(retain_identical_close(bad,history),bad)
            with self.assertRaises(ValueError):
                snapshot(retain_identical_close(bad,history),history)

    def test_cross_source_comparison_requires_compatible_times(self):
        a={'source':'Naver','price':'2500','source_timestamp':'2026-10-20T15:30:00+09:00'}
        b={**a,'source':'Yahoo','price':'2490'}
        self.assertEqual(compare_quote(a,b)['status'],'different')
        b['price']='2501'
        self.assertEqual(compare_quote(a,b)['status'],'consistent')
        b['source_timestamp']='2026-10-19T15:30:00+09:00'
        self.assertEqual(compare_quote(a,b)['status'],'not_comparable')
    def test_morning_and_evening_have_distinct_roles(self):
        morning = edition_context({}, [], datetime(2026, 10, 20, 8, tzinfo=KST))
        evening = edition_context({}, [], datetime(2026, 10, 20, 18, tzinfo=KST))
        self.assertEqual(morning['session'], 'morning')
        self.assertIn('국내 개장', morning['upside'])
        self.assertIn('미국 개장', evening['upside'])
        self.assertIn('보류', evening['review'][0])

    def test_evening_reviews_only_same_day_morning(self):
        old = {'timestamp': '2026-10-20 08:03:00', 'id': 'morning-id',
               'indexes': {'kospi': {'price': '2500'}}, 'daily_overview': {'interpretation': '개장 반응 점검'}}
        context = edition_context({'kospi': {'price': '2510'}}, [old], datetime(2026, 10, 20, 18, tzinfo=KST))
        self.assertEqual(context['morning_report_id'], 'morning-id')
        self.assertIn('2500 → 마감 관측값 2510', context['review'][1])
        old['timestamp'] = '2026-10-19 08:00:00'
        self.assertNotIn('morning_report_id', edition_context({}, [old], datetime(2026, 10, 20, 18, tzinfo=KST)))

    def test_discord_session_headings_keep_region_assignment(self):
        from discord_notifications import _split_summary
        korea, us = _split_summary(['[미국장 결과]','미국 결과','[국내장 대응 조건]','국내 조건'])
        self.assertIn('국내 조건', korea)
        self.assertIn('미국 결과', us)

    def test_evidence_is_not_double_counted(self):
        item = {'title': 'test', 'url': 'https://example.com/1', 'content_status': 'article_verified', 'article_excerpt': '확인한 본문'}
        result = enrich_events({'news_items': [item], 'events': {'news': [item]}})
        self.assertEqual(result['evidence_quality']['article_verified'], 1)
        self.assertEqual(result['evidence_assessment']['total'], 1)
        self.assertEqual(enrich_events({})['evidence_assessment']['status'], '근거 부족')

    def test_risk_indicators_are_not_counted_as_equity_gains(self):
        result = market_participation({'kospi': {'change': '(-1.00%)'}, 'vix': {'change': '(+20.00%)'}})
        self.assertEqual(result['down'], 1)
        self.assertEqual(result['up'], 0)

    def test_watchdog_slots_and_weekend(self):
        delayed = scheduled_check_time(datetime(2026,10,20,9,15,tzinfo=KST),'45 23 * * 0-4')
        self.assertEqual((delayed.day,delayed.hour,delayed.minute),(20,8,45))
        self.assertEqual(due_slots(datetime(2026, 10, 24, 8, 45, tzinfo=KST)), [])
        [(kind, target)] = due_slots(datetime(2026,10,24,10,tzinfo=KST))
        self.assertEqual((kind,target.day,target.hour),('weekly',19,0))
        for hour, minute, kind, target_hour, target_minute in [(8,45,'daily',8,0),(9,45,'live',9,0),(16,15,'live',15,30),(18,45,'daily',18,0)]:
            [(actual_kind, target)] = due_slots(datetime(2026, 10, 20, hour, minute, tzinfo=KST))
            self.assertEqual((actual_kind, target.hour, target.minute), (kind,target_hour,target_minute))

    @patch('pipeline.storage.supabase.Store')
    def test_watchdog_detects_missing_without_running_pipeline(self, cls):
        cls.return_value.request.return_value = []
        with self.assertRaisesRegex(RuntimeError, '미발행'):
            check(datetime(2026,10,20,8,45,tzinfo=KST))
        cls.return_value.request.return_value = [{'status': 'complete', 'notification_status': 'sent'}]
        check(datetime(2026,10,20,8,45,tzinfo=KST))


if __name__ == '__main__':
    unittest.main()
