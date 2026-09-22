import copy
import unittest
from main import yahoo_quote_values
from pipeline.analysis.research import snapshot, source_time, event_insight
from pipeline.analysis.watchlist import build_watchlist


class ResearchQualityTests(unittest.TestCase):
    def test_stale_bars_cannot_borrow_new_metadata_time(self):
        data = {'meta': {'regularMarketPrice':189.16, 'previousClose':181.31,
                        'regularMarketTime':1790020800},
                'indicators': {'quote':[{'close':[182.39,181.31]}]}}
        self.assertEqual(yahoo_quote_values(data), (189.16,181.31,1790020800))
        del data['meta']['regularMarketPrice']
        with self.assertRaises(ValueError):
            yahoo_quote_values(data)

    def test_daily_reference_is_previous_session_not_range_start(self):
        from datetime import datetime
        def epoch(s): return int(datetime.fromisoformat(s).timestamp())
        data={'meta':{'regularMarketPrice':190, 'regularMarketTime':epoch('2026-09-22T05:00:00+09:00'),
                      'chartPreviousClose':150, 'exchangeTimezoneName':'America/New_York'},
              'timestamp':[epoch('2026-09-18T22:30:00+09:00'),epoch('2026-09-21T22:30:00+09:00')],
              'indicators':{'quote':[{'close':[180,189]}]}}
        self.assertEqual(yahoo_quote_values(data)[1],180)

    def test_snapshot_conflicts_regressions_and_immutability(self):
        q={'ewy':{'price':'189.16','change':'+4.33%', 'source':'Yahoo Finance',
                  'price_basis':'regular_trade','source_timestamp':'2026-09-22T05:00:00+09:00',
                  'collected_at':'2026-09-22T08:00:00+09:00'}}
        saved=copy.deepcopy(q)
        identity=snapshot(q)['id']
        self.assertEqual(q,saved)
        q['ewy']['collected_at']='2026-09-22T09:00:00+09:00'
        self.assertEqual(identity,snapshot(q)['id'])
        q['ewy']['price']='181.31'
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            snapshot(q,[{'indexes':saved}])
        q['ewy']['source_timestamp']='2026-09-19T05:00:00+09:00'
        with self.assertRaisesRegex(ValueError,'Regressing'):
            snapshot(q,[{'market_signals':saved}])

    def test_future_and_unknown_times(self):
        self.assertIsNone(source_time('기준시각 확인 필요'))
        with self.assertRaisesRegex(ValueError,'Future'):
            snapshot({'x':{'source_timestamp':'2026-09-22T10:00:00+09:00','collected_at':'2026-09-22T09:00:00+09:00'}})

    def test_empty_watchlist_reports_actual_screening(self):
        def forbidden(_): raise AssertionError('No evidence means no quote request')
        result=build_watchlist([],quote_loader=forbidden)
        self.assertEqual(result['screening']['quote_requested'],0)
        self.assertEqual(result['screening']['rejected']['evidence'],result['screening']['universe'])

    def test_unverified_events_never_get_directional_score(self):
        result=event_insight({'title':'신규 서비스 발표'})
        self.assertEqual(result['impact'],'판단 보류')
        self.assertNotIn('score',result)


if __name__ == '__main__':
    unittest.main()
