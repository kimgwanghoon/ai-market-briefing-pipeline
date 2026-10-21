import unittest
from unittest.mock import Mock, patch
from pipeline.analysis.daily_desk import select_desk_news, build_issue_desk, validate_issue, tickers_for, is_us_issue, evidence_quotes
from ai_generation import grounded_claim_error, bind_observed_metrics


BODY = '엔비디아는 데이터센터용 반도체 공급 계획을 발표했다. 회사 측은 고객사의 투자 일정에 맞춰 공급량을 조절할 계획이라고 설명했다. 다만 개별 고객사의 구매 규모와 확정 계약 금액은 공개하지 않았다. 향후 실제 주문과 매출 반영 시점을 확인해야 한다.'


def response(key='issue-1'):
    return {'id': key, 'fact': '엔비디아가 데이터센터용 반도체 공급 계획을 발표했다.',
            'support_quote': '엔비디아는 데이터센터용 반도체 공급 계획을 발표했다.',
            'korea_impact': '조건부 추론: 실제 주문이 늘면 국내 반도체 공급망의 수요 변화도 확인할 필요가 있다.',
            'next_check': '회사 후속 발표에서 실제 주문과 매출 반영 여부를 확인한다.',
            'invalidation': '주문이 지연되거나 공급 계획이 축소되면 해석을 재검토한다.'}


class DailyDeskTests(unittest.TestCase):
    def test_us_technology_reserved_and_duplicate_urls_removed(self):
        domestic = [{'title':f'국내 기업 공시 {i}', 'link':f'https://example.com/kr{i}'} for i in range(8)]
        us = [{'title':title, 'link':f'https://example.com/us{i}'} for i,title in enumerate(
            ['미국 증시 혼조', '엔비디아 공급 계획', 'AAOI 광통신 투자', '미국 물가 발표'])]
        select = lambda items,max_count: items[:max_count]
        result = select_desk_news(domestic+us+us, True, select)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0]['title'], '엔비디아 공급 계획')
        self.assertEqual(result[1]['title'], 'AAOI 광통신 투자')
        self.assertEqual(len({i['link'] for i in result}),5)
        self.assertEqual(select_desk_news(domestic+us, False,select)[0]['title'], domestic[0]['title'])

    def test_exact_quote_and_numbers_required(self):
        self.assertEqual(validate_issue(response(), BODY), '')
        self.assertTrue(validate_issue({**response(), 'support_quote':'공급량이 두 배로 늘었다는 가상의 인용문입니다.'}, BODY))
        self.assertTrue(validate_issue({**response(), 'fact':'엔비디아 매출이 999% 증가했다.'}, BODY))

    @patch('pipeline.analysis.daily_desk.create_structured_completion')
    def test_repair_only_bad_story_and_keep_valid_story(self, generate):
        news = [{'title':'엔비디아 공급 계획', 'article_excerpt':BODY, 'content_status':'article_verified'},
                {'title':'AAOI 광통신 발표', 'article_excerpt':BODY, 'content_status':'article_verified'}]
        generate.side_effect=[{'items':[response(), {**response('issue-2'), 'fact':'매출 999% 증가'}]},
                              {'items':[response('issue-2')]}]
        quote = Mock(return_value={'price':'N/A'})
        result = build_issue_desk(news, 'morning', quote, Mock())
        self.assertEqual([i['analysis_mode'] for i in result], ['ai','ai'])
        self.assertEqual(generate.call_count,2)
        properties=generate.call_args.kwargs['schema']['properties']['items']['items']['properties']
        self.assertTrue(all(q in ' '.join(BODY.split()) for q in properties['support_quote']['enum']))
        self.assertIn('issue-2',generate.call_args.kwargs['user_prompt'])
        self.assertEqual(result[0]['observations'],[])

    def test_source_quote_choices_are_literal_even_for_long_sentences(self):
        source='긴 원문을 그대로 유지하는 검증 문장입니다. '*40
        quotes=evidence_quotes(source)
        self.assertTrue(quotes)
        self.assertTrue(all(15<=len(q)<=160 and q in ' '.join(source.split()) for q in quotes))

    @patch('pipeline.analysis.daily_desk.create_structured_completion')
    def test_headline_only_never_becomes_analysis(self, generate):
        result = build_issue_desk([{'title':'AAOI 투자 발표'}], 'morning', lambda _: {'price':'N/A'}, Mock())
        generate.assert_not_called()
        self.assertEqual(result[0]['content_status'],'headline_only')
        self.assertIn('보류',result[0]['fact'])

    def test_company_match_does_not_match_inside_words(self):
        self.assertEqual(tickers_for({'title':'metadata news'}),[])
        self.assertEqual(tickers_for({'title':'AAOI 광통신'}),['AAOI'])
        self.assertFalse(is_us_issue({'title':'코스피 반등 출발', 'description':'미국 엔비디아 뉴스'}))
        self.assertTrue(is_us_issue({'title':'美 기술주 전망'}))
        self.assertTrue(validate_issue({**response(),'next_check':'오늘 국내 개장 후 실적 발표를 확인한다.'}, BODY))

    def test_quoted_title_not_investment_advice(self):
        evidence={'news-1':{'title':'목표가 상향 보도'}}
        claim={'text':'목표가 상향 보도: 후속 실적 확인이 필요합니다.', 'evidence_ids':['news-1']}
        self.assertEqual(grounded_claim_error(claim,evidence,{}),'')
        claim['text']+=' 반드시 매수하세요.'
        self.assertTrue(grounded_claim_error(claim,evidence,{}))

    def test_index_name_digits_are_not_invented_values(self):
        claim={'text':'S&P 500 7123.45 흐름 확인', 'evidence_ids':['idx-sp500']}
        evidence={'idx-sp500':{'price':'7123.45'}}
        self.assertEqual(grounded_claim_error(claim,evidence,{'S&P 500':'idx-sp500'}),'')
        claim['text']='S&P 500 9999 흐름 확인'
        self.assertTrue(grounded_claim_error(claim,evidence,{'S&P 500':'idx-sp500'}))

    def test_resolve_explicit_names_without_accepting_swapped_or_new_values(self):
        evidence={'idx-kospi':{'price':'2500','change':'+1%'}, 'idx-kosdaq':{'price':'800','change':'-2%'}}
        aliases={'KOSPI':'idx-kospi','KOSDAQ':'idx-kosdaq'}
        claim={'text':'KOSPI 2500 (+1%), KOSDAQ 800 (-2%) 흐름입니다.', 'evidence_ids':['idx-kospi']}
        fixed=bind_observed_metrics(claim,evidence,aliases)
        self.assertEqual(fixed['evidence_ids'],['idx-kospi','idx-kosdaq'])
        self.assertEqual(claim['evidence_ids'],['idx-kospi'])
        for text in ('KOSPI 800, KOSDAQ 2500입니다.', 'KOSPI 2500, KOSDAQ 9999입니다.'):
            bad={**claim,'text':text}
            self.assertTrue(grounded_claim_error(bind_observed_metrics(bad,evidence,aliases),evidence,aliases))
        evidence['idx-kosdaq']['price']='N/A'
        self.assertEqual(bind_observed_metrics(claim,evidence,aliases),claim)

    def test_market_repair_preserves_other_valid_claims(self):
        import main
        market={'price':'100', 'change':'+1%', 'trend':'상승', 'color':'red'}
        good={'text':'KOSPI 100 흐름 확인', 'evidence_ids':['idx-kospi'], 'claim_type':'observation'}
        bad={**good,'text':'KOSPI 999 흐름 확인'}
        result={'headline':good,'korea_points':[good,bad,good], 'us_points':[good,good], 'watchpoint':good}
        meta={}
        with patch.object(main,'OPENAI_API_KEY','test'), patch.object(main,'OpenAI'), patch.object(main,'create_structured_completion',return_value=result) as call:
            _, points, _=main.generate_ai_briefing(*([market]*10),generation_meta=meta)
        self.assertEqual(call.call_count,2)
        self.assertEqual(meta['mode'],'partial')
        self.assertNotIn('999',' '.join(points))
        self.assertIn(good['text'],points)


if __name__ == '__main__':
    unittest.main()
