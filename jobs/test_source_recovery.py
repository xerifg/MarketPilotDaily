from datetime import datetime, timezone
from decimal import Decimal
from http.client import RemoteDisconnected
from io import StringIO
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from jobs.fund_flows import attempt, fetch_json, read_etfs, read_cached_industries, parse_industries, read_industry_close, refresh_cached_industries
from jobs.sectors import read_history
from jobs import check_sources
import json
from jobs.test_fund_flows import industry


CUTOFF = datetime(2026, 10, 10, tzinfo=timezone.utc)


class SourceRecoveryTests(unittest.TestCase):
    @patch('jobs.fund_flows.read_industry_history')
    def test_cached_universe_can_recover_daily_values_without_claiming_complete_current_classification(self, history):
        old = dict(date='2026-09-16', expectedCount=128, rows=[dict(code='BK0001', name='行业', stale=True)])
        history.return_value = [dict(code='BK0001', date='2026-10-09', stale=False)]
        result = refresh_cached_industries(old, CUTOFF)
        self.assertEqual(result['date'], '2026-10-09')
        self.assertEqual(result['universeDate'], '2026-09-16')
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['cachedUniverse'])
        history.return_value = []
        self.assertIs(refresh_cached_industries(old, CUTOFF), old)

    @patch('jobs.fund_flows.fetch_json')
    def test_industry_history_excludes_intraday_and_does_not_bridge_missing_sessions(self, fetch):
        days = ['2026-10-12', '2026-10-09', '2026-10-08', '2026-09-30', '2026-09-29', '2026-09-28']
        data = dict(code='BK0001', name='行业', market=90,
                    klines=[day + ',100,0,0,0,0' for day in days])
        fetch.return_value = dict(data=data)
        cutoff = datetime(2026, 10, 12, 0, tzinfo=timezone.utc)
        row = read_industry_close(dict(f12='BK0001', f14='行业'), cutoff)
        self.assertEqual(row['date'], '2026-10-09')
        self.assertEqual(row['net5'], '500')
        self.assertFalse(row['stale'])
        self.assertIsNone(row['changePct'])
        data['klines'].pop(2)
        self.assertIsNone(read_industry_close(dict(f12='BK0001', f14='行业'), cutoff)['net5'])
        data['name'] = '其他行业'
        with self.assertRaises(ValueError):
            read_industry_close(dict(f12='BK0001', f14='行业'), cutoff)

    @patch('jobs.fund_flows.time.sleep')
    @patch('jobs.fund_flows.urlopen')
    def test_transient_disconnect_is_retried(self, open_url, sleep):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"data": 1.25}'
        open_url.side_effect = [RemoteDisconnected('private request'), response]
        self.assertEqual(fetch_json('https://example.com/quotes'), {'data': Decimal('1.25')})
        self.assertEqual(open_url.call_count, 2)
        sleep.assert_called_once()

    @patch('jobs.fund_flows.time.sleep')
    @patch('jobs.fund_flows.urlopen')
    def test_retry_is_bounded_and_does_not_retry_forbidden_or_invalid_data(self, open_url, sleep):
        open_url.side_effect = TimeoutError('private request')
        with self.assertRaises(TimeoutError):
            fetch_json('https://example.com/quotes')
        self.assertEqual(open_url.call_count, 3)
        open_url.reset_mock()
        open_url.side_effect = HTTPError('https://example.com/private', 403, 'denied', {}, None)
        with self.assertRaises(HTTPError):
            fetch_json('https://example.com/quotes')
        self.assertEqual(open_url.call_count, 1)

    @patch('jobs.fund_flows.urlopen')
    def test_jsonp_is_parsed_as_data_and_never_executes_script(self, open_url):
        response = open_url.return_value.__enter__.return_value
        response.read.return_value = b'marketpilot({"data":{"net":1.25}});'
        self.assertEqual(fetch_json('https://example.com/industry', {'cb': 'marketpilot'}), {'data': {'net': Decimal('1.25')}})
        for raw in (b'evil({"data":1});', b'marketpilot({"data":1});alert("private")', b'marketpilot(not_json);'):
            response.read.return_value = raw
            with self.assertRaises(ValueError):
                fetch_json('https://example.com/industry', {'cb': 'marketpilot'})
        open_url.reset_mock()
        open_url.side_effect = None
        open_url.return_value.__enter__.return_value.read.return_value = b'not json'
        with self.assertRaises(ValueError):
            fetch_json('https://example.com/quotes')
        self.assertEqual(open_url.call_count, 1)

    def test_failure_diagnostics_never_log_arguments_or_error_messages(self):
        def read_private(symbol):
            raise OSError('private account and secret token')
        with patch('sys.stderr', new_callable=StringIO) as log:
            self.assertIsNone(attempt(read_private, 'private holding'))
        self.assertIn('read_private', log.getvalue())
        self.assertIn('OSError', log.getvalue())
        self.assertNotIn('private holding', log.getvalue())
        self.assertNotIn('secret token', log.getvalue())

    @patch('jobs.sectors.fetch_json')
    def test_index_and_etf_history_use_working_quote_provider(self, fetch):
        stamps = [int(datetime(2026, 10, d, tzinfo=timezone.utc).timestamp() * 1000) for d in (8, 9, 10)]
        payload = dict(data=dict(timestamp=stamps, close=[100, 110, 120]))
        for secid, symbol in [('1.000300', '000300.SH'), ('0.159692', '159692.SZ')]:
            fetch.return_value = payload
            bars = read_history(secid, CUTOFF)
            self.assertEqual(bars, {'2026-10-08': Decimal(100), '2026-10-09': Decimal(110)})
            self.assertEqual(fetch.call_args.args[1]['symbol'], symbol)
            self.assertEqual(fetch.call_args.args[1]['adjust'], 'none')
            self.assertGreaterEqual(fetch.call_args.args[1]['count'], 21)

    @patch('jobs.sectors.fetch_json')
    def test_provider_does_not_invent_industry_symbols_or_accept_duplicate_dates(self, fetch):
        fetch.side_effect = OSError('offline')
        with self.assertRaises(OSError):
            read_history('90.BK0001', CUTOFF)
        fetch.assert_called_once()
        stamp = int(datetime(2026, 10, 9, tzinfo=timezone.utc).timestamp() * 1000)
        for data in [dict(timestamp=[stamp, stamp], close=[100, 101]),
                     dict(timestamp=[stamp], close=[100, 101]),
                     dict(timestamp=[stamp], close=['NaN'])]:
            fetch.side_effect = None
            fetch.return_value = dict(data=data)
            with self.assertRaises(ValueError):
                read_history('1.000300', CUTOFF)

    @patch('jobs.fund_flows.read_shares')
    @patch('jobs.fund_flows.read_nav')
    def test_unpublished_latest_shares_use_latest_adjacent_complete_sessions(self, nav, shares):
        days = ['2026-10-09', '2026-10-08', '2026-09-30', '2026-09-29', '2026-09-28', '2026-09-25', '2026-09-24']
        nav.return_value = {day: dict(nav=Decimal(2), corporateAction=False) for day in days}
        shares.side_effect = lambda day: {} if day == days[0] else {'510300': dict(name='ETF', shares=Decimal(100 + days.index(day)))}
        result = read_etfs([], CUTOFF)
        self.assertEqual(result['date'], '2026-10-08')
        self.assertEqual(result['rows'][0]['previousDate'], '2026-09-30')
        self.assertEqual(result['rows'][0]['net'], '-2.00')
        self.assertEqual(result['rows'][0]['net5'], '-10.00')
        self.assertIn('份额', result['note'])

    @patch('jobs.fund_flows.read_shares')
    @patch('jobs.fund_flows.read_nav')
    def test_missing_middle_share_date_is_not_bridged(self, nav, shares):
        days = ['2026-10-09', '2026-10-08', '2026-09-30']
        nav.return_value = {day: dict(nav=Decimal(2), corporateAction=False) for day in days}
        shares.side_effect = lambda day: {} if day == days[1] else {'510300': dict(name='ETF', shares=Decimal(100))}
        self.assertEqual(read_etfs([], CUTOFF)['rows'], [])

    @patch('jobs.fund_flows.read_shares')
    @patch('jobs.fund_flows.read_nav')
    def test_corporate_action_does_not_fall_back_to_older_inflow(self, nav, shares):
        days = ['2026-10-09', '2026-10-08', '2026-09-30']
        nav.return_value = {day: dict(nav=Decimal(2), corporateAction=day == days[0]) for day in days}
        shares.side_effect = lambda day: {'510300': dict(name='ETF', shares=Decimal(100))}
        result = read_etfs([], CUTOFF)
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['date'], days[0])

    @patch('jobs.check_sources.collect_context', return_value=dict(margin=[dict(stale=False)] * 2,
        valuation=dict(date='2026-10-09', stale=False)))
    @patch('jobs.check_sources.collect_sectors')
    @patch('jobs.check_sources.collect_flows')
    def test_diagnostic_fails_for_empty_or_stale_sources(self, flows, sectors, _context):
        group = dict(status='ok', date='2026-10-09', expectedCount=1, rows=[dict(stale=False)])
        flows.return_value = dict(industry=group, etf=group)
        sectors.return_value = dict(benchmarkDate='2026-10-09', selectedCodes=['ETF'],
                                   rows=[dict(code='ETF', returns={'1': '0', '5': '0', '20': '0'},
                                              relative={'1': '0', '5': '0', '20': '0'}, stale=False)])
        with patch('sys.stdout', new_callable=StringIO):
            self.assertEqual(check_sources.main(), 0)
            group['rows'][0]['stale'] = True
            self.assertEqual(check_sources.main(), 1)
            group['rows'] = []
            self.assertEqual(check_sources.main(), 1)

    def test_cached_industry_dates_are_preserved_and_old_snapshots_cannot_lead(self):
        from jobs.settings import SETTINGS
        stamp = int(datetime(2026, 10, 9, 8, tzinfo=timezone.utc).timestamp())
        path = Mock()
        path.exists.return_value = True
        path.stat.return_value.st_size = 500
        path.read_text.return_value = json.dumps(dict(apiUrl=SETTINGS['sources']['eastmoney']['industryApiUrl'], total=1,
            rows=parse_industries([industry(stamp=stamp)], CUTOFF), fetchedAt='2026-10-10T00:00:00+00:00'))
        with patch('jobs.fund_flows.industry_cache_path', return_value=path):
            current = read_cached_industries(CUTOFF)
            self.assertEqual(current['date'], '2026-10-09')
            self.assertTrue(current['cached'])
            self.assertFalse(current['rows'][0]['stale'])
            old = read_cached_industries(datetime(2026, 10, 13, tzinfo=timezone.utc))
            self.assertTrue(old['rows'][0]['stale'])
            self.assertIsNone(read_cached_industries(datetime(2026, 10, 8, tzinfo=timezone.utc)))


if __name__ == '__main__':
    unittest.main()
