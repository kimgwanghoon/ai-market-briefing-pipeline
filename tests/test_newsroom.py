import unittest
from pipeline.analysis.newsroom import cluster_news, diverse_news, collection_quality


class NewsroomTests(unittest.TestCase):
    def test_repeated_headline_is_not_a_summary(self):
        from pipeline.analysis.newsroom import has_listing_summary
        self.assertFalse(has_listing_summary({'title':'연준 금리 동결', 'listing_summary':'연준 금리 동결 - 연합뉴스'}))

    def test_dart_navigation_is_not_document_evidence(self):
        from unittest.mock import patch, Mock
        from intraday import fetch_dart_document_excerpt
        landing=Mock(text='dcmNo="123456" ' + '공시 검색 메뉴 ' * 50)
        with patch('intraday.requests.get', return_value=landing) as request:
            self.assertEqual(fetch_dart_document_excerpt('20261029000001'), '')
            self.assertEqual(request.call_count, 1)

    def test_dart_reads_explicit_document_viewer(self):
        from unittest.mock import patch, Mock
        from intraday import fetch_dart_document_excerpt
        landing=Mock(text="viewDoc('20261029000001','123','1','0','200','dart3.xsd')")
        document=Mock(text='<html><body>' + '공급 계약 주요 내용 ' * 30 + '</body></html>')
        with patch('intraday.requests.get', side_effect=[landing, document]) as request:
            self.assertIn('공급 계약', fetch_dart_document_excerpt('20261029000001'))
            self.assertEqual(request.call_args.args[0], 'https://dart.fss.or.kr/report/viewer.do')

    def test_recaps_merge_but_opposite_directions_do_not(self):
        rows = [{'title':t,'url':f'https://example.com/{i}', 'published_at':'2026-10-29 10:00:00'} for i,t in enumerate([
            'AI 반도체주 강세에 뉴욕증시 상승', '앤트로픽 AI 랠리 나스닥 상승',
            'AI 반도체주 급락', '엔비디아 공급계약 체결'])]
        result = cluster_news(rows)
        self.assertEqual(len(result),3)
        self.assertEqual(result[0]['cluster_size'],2)
        self.assertEqual(len(result[0]['related_sources']),1)

    def test_separate_sessions_and_preserve_sources(self):
        rows = [{'title':'AI 반도체주 강세에 뉴욕증시 상승','url':'https://example.com/a','published_at':'2026-10-28'},
                {'title':'앤트로픽 AI 랠리 나스닥 상승','url':'https://example.com/b','published_at':'2026-10-29'}]
        self.assertEqual(len(cluster_news(rows)), 2)
        rows[0]['published_at']='2026-10-29'
        once=cluster_news(rows)
        self.assertEqual(cluster_news(once), once)

    def test_section_failure_does_not_abort_other_sources(self):
        from datetime import datetime
        from pipeline.analysis.newsroom import collect_news
        now=datetime(2026,10,29,12)
        def crawler(stamp, max_articles, section, diagnostics):
            if section=='258':
                raise TimeoutError()
            diagnostics['status']='ok'
            return [{'title':'기사', 'dt':now, 'link':section}]
        rows,status=collect_news(now,crawler)
        self.assertEqual(len(rows),4)
        self.assertEqual(status[0]['status'],'failed')

    def test_quality_is_measured_not_inferred(self):
        rows=[{'title':'연준 금리 동결','url':'https://example.com/a'}]*2
        metric=collection_quality(rows,[{**rows[0],'content_status':'headline_only'}],[])
        self.assertEqual(metric['duplicate_ratio'],.5)
        self.assertEqual(metric['body_ratio'],0)

    def test_selection_retains_distinct_facts(self):
        rows=[{'title':t,'url':f'https://example.com/{i}'} for i,t in enumerate([
            '삼성전자 영업이익 발표', '현대차 실적 발표', '기아 매출 발표',
            '연준 금리 결정', '기업 공급계약 체결'])]
        result=diverse_news(rows,4)
        self.assertIn('연준 금리 결정',[x['title'] for x in result])
        self.assertIn('기업 공급계약 체결',[x['title'] for x in result])
