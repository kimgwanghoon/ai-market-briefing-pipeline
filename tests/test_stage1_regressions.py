import unittest
from ai_generation import grounded_claim_error
from intraday import build_rule_points, score_dart_events
from main import build_fallback_section_items
from pipeline.analysis.research import snapshot, event_insight


class StageOneRegressions(unittest.TestCase):
    def test_preopen_is_not_intraday_market_reaction(self):
        q = {'price': '100', 'change': '- 0.00 (+0.00%)', 'market_status': '전일 종가 · 개장 전'}
        evidence = {'idx-kospi': q}
        claim = {'text': 'KOSPI 100 보합세로 미국 상승의 영향을 받지 못했습니다.', 'evidence_ids': ['idx-kospi']}
        self.assertIn('개장 전', grounded_claim_error(claim, evidence, {'KOSPI':'idx-kospi'}))
        claim['text'] = '개장 전 KOSPI 100은 전일 종가 참고값이며 개장 후 방향을 확인하세요.'
        self.assertEqual(grounded_claim_error(claim, evidence, {'KOSPI':'idx-kospi'}), '')
        points, _ = build_fallback_section_items(*([q]*10))
        self.assertIn('아직 판단할 수 없습니다', points[0])

    def test_watchpoint_tracks_direction(self):
        for changes, phrase in [(('+1%', '+2%'), '상승세'), (('-1%', '-2%'), '낙폭'), (('+1%', '-2%'), '함께 개선')]:
            quotes = {k:{'change':'▲ 1 (+1%)'} for k in ('kospi','kosdaq','dow','nasdaq','vix')}
            for key, change in zip(('kospi','kosdaq'), changes):
                quotes[key]['change'] = f'1 ({change})'
            _, watch = build_rule_points(quotes, {'label':'중립'}, [], [])
            self.assertIn(phrase, watch)
            if phrase != '낙폭':
                self.assertNotIn('낙폭', watch)

    def test_dismissal_and_correction_do_not_keep_original_direction(self):
        for title in ('파산신청기각', '회생절차개시신청취소', '유상증자결정철회', '[기재정정]단일판매ㆍ공급계약체결'):
            item = {'title':title, 'corp_name':'테스트기업'}
            self.assertEqual(score_dart_events([item])[0]['impact_score'], 0)
            self.assertEqual(event_insight(item)['impact'], '판단 보류')
        self.assertLess(score_dart_events([{'title':'파산신청'}])[0]['impact_score'], 0)
        self.assertGreater(score_dart_events([{'title':'단일판매ㆍ공급계약체결'}])[0]['impact_score'], 0)

    def test_preopen_reference_does_not_block_regular_trade_but_live_regression_does(self):
        old = {'price':'100', 'source':'Naver Finance', 'price_basis':'regular_trade',
               'source_timestamp':'2026-10-26T08:00:30+09:00', 'market_status':'전일 종가 · 개장 전'}
        new = {**old, 'source_timestamp':'2026-10-23T16:15:00+09:00', 'market_status':'최근 종가'}
        snapshot({'kospi':new}, [{'indexes':{'kospi':old}}])
        with self.assertRaises(ValueError):
            snapshot({'kospi':new}, [{'indexes':{'kospi':{**old,'market_status':'최근 종가'}}}])

    def test_ai_cannot_describe_current_rising_market_as_falling(self):
        q = {'price':'100', 'change':'▲ 1 (+1%)'}
        evidence = {'idx-kospi':q, 'idx-kosdaq':q}
        claim = {'text':'KOSPI의 낙폭이 줄어드는지 확인하세요.', 'evidence_ids':['idx-kospi']}
        self.assertIn('상승', grounded_claim_error(claim,evidence,{'KOSPI':'idx-kospi'}))
