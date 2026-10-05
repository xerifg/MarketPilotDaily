from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from jobs import market
from jobs.presentation import email_paragraphs, report_paragraphs
from jobs.settings import SETTINGS


class SettingsTests(unittest.TestCase):
    def test_feed_settings_control_requests_filtering_and_limits(self):
        feeds = [dict(name='disabled', url='https://example.com/off', enabled=False, maxItems=5, filterByKeywords=False),
                 dict(name='filtered', url='https://example.com/filtered', enabled=True, maxItems=5, filterByKeywords=True),
                 dict(name='renamed central bank', url='https://example.com/all', enabled=True, maxItems=2, filterByKeywords=False)]
        articles = [dict(title='unrelated headline', url='https://example.com/one', publishedAt='2026-09-13'),
                    dict(title='market update', url='https://example.com/two', publishedAt='2026-09-13'),
                    dict(title='another unrelated headline', url='https://example.com/three', publishedAt='2026-09-13')]
        flows = dict(industry={'rows': []}, etf={'rows': []}, holdings=[], limitations=[])
        with patch.object(market, 'FEEDS', feeds), patch.object(market, 'BENCHMARKS', {}), \
                patch.object(market, 'fetch') as fetch, patch.object(market, 'parse_feed', return_value=articles), \
                patch.object(market, 'collect_flows', return_value=flows), patch.object(market, 'flow_sources', return_value=[]), \
                patch.dict(SETTINGS['news'], lookbackHours=48):
            evidence = market.collect({'positions': []}, datetime(2026, 9, 13, tzinfo=timezone.utc))
        self.assertEqual([call.args[0] for call in fetch.call_args_list], [feeds[1]['url'], feeds[2]['url']])
        self.assertEqual([(item['publisher'], item['title']) for item in evidence['news']],
                         [('filtered', 'market update'), ('renamed central bank', 'market update'),
                          ('renamed central bank', 'unrelated headline')])
        self.assertEqual(evidence['newsLookbackHours'], 48)
        self.assertTrue(all('过去48小时' in note for note in evidence['coverage'][:2]))

    def test_news_window_changes_collection_but_not_saved_report_window(self):
        cutoff = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
        raw = b'<rss><channel><item><title>market</title><link>https://example.com/news</link><pubDate>Fri, 11 Sep 2026 18:00:00 GMT</pubDate></item></channel></rss>'
        self.assertEqual(market.parse_feed(raw, cutoff), [])
        with patch.dict(SETTINGS['news'], lookbackHours=48):
            self.assertEqual(len(market.parse_feed(raw, cutoff)), 1)
        report = dict(cutoffAt=cutoff.isoformat(), evidence={'newsLookbackHours': 48})
        self.assertIn('此前 48 小时', email_paragraphs(report)[0])
        self.assertIn('过去 48 小时', '\n'.join(report_paragraphs(report)))
        report['evidence'] = {}
        self.assertIn('此前 24 小时', email_paragraphs(report)[0])

    def test_quote_uses_configured_endpoint_and_sample_count(self):
        with patch.dict(SETTINGS['sources']['tickflow'], klinesUrl='https://example.com/bars'), \
                patch.dict(SETTINGS['collection'], dailyBarCount=12), \
                patch.object(market, 'fetch', return_value=json.dumps({'data': {}}).encode()) as fetch, \
                patch.object(market, 'parse_bars', return_value={}):
            quote = market.read_quote('SPY.US', datetime(2026, 9, 13, tzinfo=timezone.utc))
        url = fetch.call_args.args[0]
        self.assertEqual(urlsplit(url).hostname, 'example.com')
        self.assertEqual(parse_qs(urlsplit(url).query)['count'], ['12'])
        self.assertEqual(quote['sourceUrl'], url)


if __name__ == '__main__':
    unittest.main()
