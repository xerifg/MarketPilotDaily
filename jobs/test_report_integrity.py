from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from jobs.market import news_relevance, parse_bars
from jobs.report import validate
from jobs.sector_presentation import sector_line, deterministic_sector_analysis
from jobs.fund_flows import read_sz_shares, read_industry_close
from decimal import Decimal
from jobs.test_presentation import example_report
from jobs.sectors import read_reports


class ReportIntegrityTests(unittest.TestCase):
    def test_commodity_keywords_do_not_select_agricultural_place_names(self):
        self.assertEqual(news_relevance('“铜鼓黄精”区域公用品牌亮相江西南昌', []), 0)
        self.assertEqual(news_relevance('茶枝柑结出“黄金果” 铺就富民新路', []), 0)
        self.assertGreater(news_relevance('铜价上涨，有色金属企业利润回升', []), 0)
        self.assertGreater(news_relevance('黄金ETF资金流入创纪录', []), 0)

    def test_available_quotes_cannot_be_described_as_missing_or_stale(self):
        content = deepcopy(example_report()['evidence']['analysisRaw'])
        content['holdings'] = [dict(symbol='159310.SZ', advice='今日：行情与资金均缺失。')]
        evidence = dict(sources=example_report()['sources'], quotes=[dict(symbol='159310.SZ', missing=False, stale=False)])
        with self.assertRaisesRegex(ValueError, 'contradictory_data_availability'):
            validate(content, [dict(symbol='159310.SZ')], evidence)
        content['holdings'][0]['advice'] = '今日：历史行情缺失，资金未取得。'
        validate(content, [dict(symbol='159310.SZ')], evidence)
        content['holdings'][0]['advice'] = '若行情缺失，则取消观察。'
        validate(content, [dict(symbol='159310.SZ')], evidence)
        content['holdings'][0]['advice'] = '今日：价格偏旧，不作判断。'
        with self.assertRaisesRegex(ValueError, 'contradictory_data_availability'):
            validate(content, [dict(symbol='159310.SZ')], evidence)

    @patch('jobs.fund_flows.fetch_json')
    def test_shenzhen_share_dates_units_identity_and_complete_pages(self, fetch):
        raw = dict(metadata=dict(catalogid='scsj_fund_jjgm', recordcount=2, cols={'current_size': '基金规模(万份)'}),
                   data=[dict(size_date=day, fund_code='159692', security_short_name='ETF', current_size='1,234.50')
                         for day in ['2026-10-08', '2026-10-09']])
        fetch.return_value = [raw]
        rows = read_sz_shares('159692', ['2026-10-08', '2026-10-09'])
        self.assertEqual(str(rows['2026-10-09']['shares']), '12345000.00')
        with self.assertRaises(ValueError):
            read_sz_shares('159583', ['2026-10-08', '2026-10-09'])
        raw['metadata']['recordcount'] = 3
        with self.assertRaises(ValueError):
            read_sz_shares('159692', ['2026-10-08', '2026-10-09'])

    def test_missing_history_and_flow_are_not_called_stale(self):
        row = dict(code='159692.SZ', name='ETF', kind='etf', date=None, changePct=None,
                   returns={}, relative={}, windows={}, stale=True, flowStale=True, flowDate=None,
                   net=None, net5=None, tracking='示例指数', sourceIds=['S1'])
        line = sector_line(row)
        self.assertIn('历史行情未取得', line)
        self.assertNotIn('数据偏旧', line)
        self.assertNotIn('资金偏旧', line)
        sectors = dict(detailCodes=['159692.SZ'], rows=[row])
        analysis = deterministic_sector_analysis(sectors)[0]
        self.assertIn('观察与复查条件', analysis['text'])
        self.assertIn('[S1]', analysis['text'])

    def test_us_volume_is_preserved_when_turnover_is_not_supplied(self):
        stamps = [int(datetime(2026, 10, day, tzinfo=timezone.utc).timestamp() * 1000) for day in (8, 9)]
        quote = parse_bars('SPY.US', dict(timestamp=stamps, close=[100, 101], volume=[10, 20], amount=[0, 0]),
                           datetime(2026, 10, 10, tzinfo=timezone.utc))
        self.assertEqual(quote['volume'], '20')
        self.assertIsNone(quote['amount'])

    @patch('jobs.sectors.fetch_json')
    def test_fund_reports_require_matching_identity_and_publication_before_cutoff(self, fetch):
        rows = [dict(FUNDCODE='159692', NEWCATEGORY='3', ID=f'AN2026100{day}', TITLE='定期报告',
                     PUBLISHDATE=f'2026-10-0{day}T00:00:00') for day in (8, 9)]
        fetch.return_value = dict(ErrCode=0, Data=rows)
        result = read_reports('159692.SZ', datetime(2026, 10, 8, 12, tzinfo=timezone.utc))
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['publishedAt'].startswith('2026-10-08'))
        rows[0]['FUNDCODE'] = '159583'
        with self.assertRaises(ValueError):
            read_reports('159692.SZ', datetime(2026, 10, 10, tzinfo=timezone.utc))

    @patch('jobs.sectors.read_history')
    @patch('jobs.fund_flows.fetch_json')
    def test_industry_previous_close_aligns_flow_and_prices_without_bridging_missing_days(self, fetch, history):
        days = ['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-08', '2026-10-09']
        history.return_value = {day: Decimal(100 + index) for index, day in enumerate(days)}
        raw = dict(code='BK1032', market=90, name='风电设备', klines=[f'{day},{index + 1},0,0,0,0' for index, day in enumerate(days)])
        fetch.return_value = dict(data=raw)
        cutoff = datetime(2026, 10, 12, tzinfo=timezone.utc)
        row = read_industry_close(dict(f12='BK1032', f14='风电设备'), cutoff)
        self.assertEqual(row['date'], '2026-10-09')
        self.assertEqual(row['net5'], '15')
        self.assertFalse(row['stale'])
        del raw['klines'][2]
        self.assertIsNone(read_industry_close(dict(f12='BK1032', f14='风电设备'), cutoff)['net5'])
        raw['code'] = 'BK9999'
        with self.assertRaises(ValueError):
            read_industry_close(dict(f12='BK1032', f14='风电设备'), cutoff)


if __name__ == '__main__':
    unittest.main()
