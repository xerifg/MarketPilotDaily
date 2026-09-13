from datetime import datetime, timezone
import unittest
from jobs.market import parse_bars, parse_feed, portfolio_metrics
from jobs.report import validate


class MarketTests(unittest.TestCase):
    def test_bars_use_exchange_date_and_exclude_incomplete_current_session(self):
        cutoff = datetime(2026, 9, 11, 0, 30, tzinfo=timezone.utc)
        stamps = [int(datetime(2026, 9, d, 20, tzinfo=timezone.utc).timestamp() * 1000) for d in (8, 9, 10, 11)]
        q = parse_bars('SPY.US', {'timestamp': stamps, 'close': [100, 110, 121, 150], 'volume': [1]*4, 'amount': [0]*4}, cutoff)
        self.assertEqual(q['sessionDate'], '2026-09-10')
        self.assertEqual(q['changePct'], '10.00')
        self.assertIsNone(q['amount'])
        self.assertFalse(q['stale'])

    def test_stale_and_invalid_prices_do_not_become_live_quotes(self):
        stamp = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
        bars = {'timestamp': [stamp, stamp + 86400000], 'close': [100, 90], 'volume': [1, 1], 'amount': [0, 0]}
        self.assertTrue(parse_bars('510300.SH', bars, datetime(2026, 9, 13, tzinfo=timezone.utc))['stale'])
        bars['close'][0] = float('nan')
        with self.assertRaises(ValueError):
            parse_bars('510300.SH', bars, datetime(2026, 9, 13, tzinfo=timezone.utc))

    def test_news_excludes_stale_future_undated_and_unsafe_links(self):
        items = ''.join(f'<item><title>Headline</title><link>{url}</link><pubDate>{date}</pubDate></item>' for date, url in [
            ('Sun, 13 Sep 2026 01:00:00 GMT', 'https://example.com/ok'),
            ('Fri, 11 Sep 2026 01:00:00 GMT', 'https://example.com/old'),
            ('Mon, 14 Sep 2026 01:00:00 GMT', 'https://example.com/future'),
            ('unknown', 'https://example.com/undated'),
            ('Sun, 13 Sep 2026 01:00:00 GMT', 'javascript:alert(1)')])
        rows = parse_feed(('<rss><channel>' + items + '</channel></rss>').encode(), datetime(2026, 9, 13, 12, tzinfo=timezone.utc))
        self.assertEqual([r['url'] for r in rows], ['https://example.com/ok'])

    def test_metrics_preserve_unknown_and_zero_and_never_mix_currencies(self):
        snapshot = {'positions': [dict(symbol=s, name=s, currency=c, quantity='0.125', averageCost=cost, horizon=None)
                                  for s, c, cost in [('VOO.US', 'USD', '80'), ('510300.SH', 'CNY', '0')]],
                    'cash': {'USD': '12.5', 'CNY': None}}
        quotes = [dict(symbol=s, close='100', id='Q1') for s in ('VOO.US', '510300.SH')]
        rows = portfolio_metrics(snapshot, quotes)
        self.assertEqual(rows[0]['weightPct'], '50.00')
        self.assertEqual(rows[0]['pnlPct'], '25.00')
        self.assertIsNone(rows[1]['weightPct'])
        self.assertIsNone(rows[1]['pnlPct'])
        self.assertNotIn('value', rows[0])
        quotes[0]['stale'] = True
        self.assertIsNone(portfolio_metrics(snapshot, quotes)[0]['weightPct'])

    def test_ai_cannot_introduce_unheld_security_or_unknown_evidence(self):
        content = dict(overview='x', today='x', short='x', long='x', watch='x', holdings=[], evidenceIds=[])
        validate(content, [], {'sources': []})
        content['evidenceIds'] = ['N99']
        with self.assertRaises(ValueError):
            validate(content, [], {'sources': []})
        content['evidenceIds'] = []
        content['holdings'] = [{'symbol': 'AAPL.US', 'advice': 'x'}]
        with self.assertRaises(ValueError):
            validate(content, [], {'sources': []})


if __name__ == '__main__':
    unittest.main()
