from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import Mock, patch

from jobs.deepseek import JsonCompletion
from jobs.fund_flows import parse_industries
from jobs.mail import MailConfig, build_message
from jobs.market import collect, news_relevance
from jobs.presentation import report_paragraphs
from jobs.report import build_report, validate
from jobs.sectors import collect_sectors, parse_history, performance, read_tracking, sector_sources
from jobs.sector_presentation import sector_email_lines, validate_sector_analysis
from jobs.test_fund_flows import CUTOFF, flows_fixture, industry
from jobs.test_presentation import example_report, SHARE_URL


def history():
    end = datetime(2026, 9, 16)
    days = sorted((end - timedelta(days=i)).date().isoformat() for i in range(35)
                  if (end - timedelta(days=i)).weekday() < 5)
    return {day: Decimal(100 + i) for i, day in enumerate(days)}


def fixture():
    flows = flows_fixture()
    flows['industry']['rows'] = parse_industries([
        dict(industry('BK1'), f3='2', f6='10000', f104=10, f105=2),
        dict(industry('BK2', '-100'), f3='-1', f6='20000', f104=2, f105=10)], CUTOFF)
    flows['holdings'] = [dict(symbol='600001.SH', name='示例股票', kind='stock', flow=None, industryCode='BK2', note='')]
    def aligned_history(secid, cutoff):
        bars = history()
        days = sorted(bars)
        if secid.startswith('90.'):
            bars[days[-1]] = bars[days[-2]] * (Decimal('.99') if secid.endswith('BK2') else Decimal('1.02'))
        return bars
    with patch('jobs.sectors.read_history', side_effect=aligned_history):
        return collect_sectors([], flows, CUTOFF)


def analysis(sectors):
    return [dict(code=code, text='走势与资金：核对数据。[S1][F1]\n驱动依据与反证：驱动待核实。\n持续性与风险：证据不足。\n持仓影响：关注关联方向。\n观察与复查条件：下一次收盘后复查。[S2]',
                 email='先观察走势与资金配合；关联持仓需核对，下一次收盘后复查。[S1]') for code in sectors['detailCodes']]


class SectorTests(unittest.TestCase):
    def test_industry_snapshot_preserves_zero_and_rejects_invalid_breadth(self):
        row = parse_industries([dict(industry(), f3='0', f6='0', f104=-1, f105='1.5')], CUTOFF)[0]
        self.assertEqual((row['changePct'], row['amount']), ('0', '0'))
        self.assertIsNone(row['advancers'])
        self.assertIsNone(row['decliners'])

    def test_sector_news_can_be_selected_without_a_stock_keyword(self):
        self.assertEqual(news_relevance('半导体企业发布业绩', []), 0)
        self.assertGreater(news_relevance('半导体企业发布业绩', [], ['半导体']), 0)

    def test_returns_use_five_and_twenty_intervals_not_bar_counts(self):
        bars = history()
        days = sorted(bars)
        benchmark = {day: Decimal(100) for day in days}
        result = performance(bars, benchmark)
        for period in (1, 5, 20):
            expected = str(((bars[days[-1]] / bars[days[-period - 1]] - 1) * 100).quantize(Decimal('.01')))
            self.assertEqual(result['returns'][str(period)], expected)
            self.assertEqual(result['relative'][str(period)], expected)
            self.assertEqual(result['windows'][str(period)], {'start': days[-period - 1], 'end': days[-1]})

    def test_missing_sessions_and_short_history_do_not_invent_strength(self):
        bars = history()
        benchmark = dict(bars)
        del bars[sorted(bars)[-3]]
        result = performance(bars, benchmark)
        self.assertNotIn('5', result['relative'])
        self.assertNotIn('5', result['returns'])
        result = performance(dict(list(bars.items())[-2:]), {})
        self.assertEqual(set(result['returns']), {'1'})
        self.assertEqual(result['relative'], {})

    def test_history_identity_dates_invalid_and_duplicate_bars(self):
        raw = dict(code='BK1', klines=['2026-09-15,100,100', '2026-09-16,110,110', '2026-09-17,120,120'])
        self.assertEqual(list(parse_history(raw, 'BK1', CUTOFF)), ['2026-09-15', '2026-09-16'])
        for bad in [dict(raw, code='BK2'), dict(raw, klines=[raw['klines'][0]] * 2),
                    dict(raw, klines=['2026-09-16,1,NaN']), dict(raw, klines=['2026-09-16'])]:
            with self.assertRaises(ValueError):
                parse_history(bad, 'BK1', CUTOFF)
        intraday = datetime(2026, 9, 16, 6, tzinfo=timezone.utc)
        self.assertEqual(list(parse_history(raw, 'BK1', intraday)), ['2026-09-15'])

    def test_selection_deduplicates_and_prioritizes_holdings_and_flow_signs(self):
        sectors = fixture()
        self.assertEqual(sectors['detailCodes'], ['BK2', 'BK1'])
        self.assertEqual(len(sectors['selectedCodes']), len(set(sectors['selectedCodes'])))
        self.assertEqual(sectors['holdings'][0]['code'], 'BK2')
        self.assertIn('当日下跌且主力流出', sectors['rows'][1]['reasons'])

    @patch('jobs.sectors.read_tracking', return_value=dict(target='示例指数', sourceUrl='https://example.com/fund'))
    @patch('jobs.sectors.read_history', return_value=history())
    def test_etf_uses_verified_tracking_and_keeps_different_flow_date(self, *_mocks):
        flows = flows_fixture()
        flows['holdings'][0]['flow']['date'] = '2026-09-15'
        sectors = collect_sectors([dict(symbol='510300.SH', name='示例ETF', assetType='etf')], flows, CUTOFF)
        row = next(row for row in sectors['rows'] if row['kind'] == 'etf')
        self.assertEqual(row['tracking'], '示例指数')
        self.assertEqual(row['code'], '510300.SH')
        self.assertEqual(row['flowDate'], '2026-09-15')
        self.assertEqual(row['date'], '2026-09-16')
        self.assertIn('不视为单一行业', sectors['holdings'][0]['note'])

    @patch('jobs.sectors.read_tracking', side_effect=ValueError('unverified'))
    @patch('jobs.sectors.read_history', side_effect=OSError('offline'))
    def test_failed_sources_and_unmapped_etfs_do_not_become_zero_or_guessed_sectors(self, *_mocks):
        flows = flows_fixture()
        sectors = collect_sectors([dict(symbol='510300.SH', name='示例ETF', assetType='etf')], flows, CUTOFF)
        self.assertIsNone(sectors['holdings'][0]['code'])
        self.assertFalse(any(row['kind'] == 'etf' for row in sectors['rows']))
        self.assertIsNone(sectors['benchmarkDate'])
        self.assertTrue(any('未核验' in line for line in sectors['limitations']))

    @patch('jobs.sectors.urlopen')
    def test_tracking_profile_checks_identity_and_requires_a_real_target(self, open_url):
        response = open_url.return_value.__enter__.return_value
        response.read.return_value = '<h1>示例ETF(510300)</h1><p>跟踪标的：沪深300指数 | 年化跟踪误差：0.1%</p>'.encode()
        self.assertEqual(read_tracking('510300.SH')['target'], '沪深300指数')
        with self.assertRaises(ValueError):
            read_tracking('512480.SH')
        response.read.return_value = '<h1>示例ETF(510300)</h1><p>跟踪标的：无 | 年化跟踪误差：0</p>'.encode()
        with self.assertRaises(ValueError):
            read_tracking('510300.SH')

    def test_sector_output_validation_unknown_citations_and_duplicates(self):
        sectors = fixture()
        sources = sector_sources(sectors) + [dict(id='F1')]
        good = analysis(sectors)
        self.assertEqual(validate_sector_analysis(good[::-1], sectors, sources), good)
        for bad in [good[:1], [good[0], good[0]], [dict(good[0], code='BK99'), good[1]],
                    [dict(good[0], email='未经核实[S99]'), good[1]], [dict(good[0], email='没有证据'), good[1]],
                    [dict(good[0], text='字' * 351), good[1]], [dict(good[0], code=[]), good[1]]]:
            with self.assertRaises(ValueError):
                validate_sector_analysis(bad, sectors, sources)

    def test_generation_and_mail_keep_full_analysis_and_sector_digest_in_one_call(self):
        report = example_report()
        sectors = fixture()
        evidence = deepcopy(report['evidence']) | {'sources': report['sources'], 'sectors': sectors}
        evidence['sources'].extend([*sector_sources(sectors), dict(id='F1', title='行业资金', url='https://example.com/funds', publishedAt='2026-09-16')])
        content = deepcopy(evidence['analysisRaw']) | {'sectorAnalysis': analysis(sectors)}
        client = Mock()
        client.complete_json.return_value = JsonCompletion(content, 10, 20, Decimal('.001'), 'test-model')
        snapshot = dict(revision=1, updatedAt=CUTOFF.isoformat(), positions=[], cash={'CNY': None, 'USD': None}, profile={})
        saved = build_report(snapshot, evidence, CUTOFF, client)
        self.assertEqual(client.complete_json.call_count, 1)
        prompt = json.loads(client.complete_json.call_args.args[1][1]['content'])
        self.assertEqual(prompt['sectorEvidence']['detailCodes'], sectors['detailCodes'])
        self.assertEqual(saved['evidence']['sectorAnalysis'], analysis(sectors))
        self.assertIn('走势与资金', '\n'.join(saved['paragraphs']))
        config = MailConfig('example@163.com', 'fake', 'example@163.com', 'example@163.com')
        message = build_message(config, 'sector-test', '日报', ['发送时间'], [], saved, SHARE_URL)
        for part in ('plain', 'html'):
            body = message.get_body(preferencelist=(part,)).get_content()
            self.assertIn('板块观察', body)
            self.assertIn('下一次收盘后复查', body)
            self.assertIn('https://quote.eastmoney.com/center/boardlist.html', body)
            self.assertNotIn('驱动依据与反证', body)
        content['sectorAnalysis'][0]['email'] = '坏的引用[S99]'
        saved = build_report(snapshot, evidence, CUTOFF, client)
        self.assertEqual(saved['evidence']['analysisStatus'], 'ok')
        self.assertNotIn('sectorAnalysis', saved['evidence'])
        self.assertIn('未生成可用', '\n'.join(sector_email_lines(saved['evidence'])))

    def test_stale_snapshots_and_history_date_mismatch_do_not_become_current(self):
        flows = flows_fixture()
        flows['industry']['rows'][0].update(changePct='1', stale=True)
        with patch('jobs.sectors.read_history', return_value=history()):
            sectors = collect_sectors([], flows, CUTOFF)
        self.assertEqual(sectors['detailCodes'], [])
        flows['industry']['rows'][0].update(stale=False)
        old = dict(list(history().items())[:-1])
        with patch('jobs.sectors.read_history', return_value=old):
            sectors = collect_sectors([], flows, CUTOFF)
        self.assertEqual(sectors['rows'][0]['returns'], {})

    def test_missing_module_keeps_old_reports_and_citations_are_validated(self):
        report = example_report()
        self.assertEqual(sector_email_lines(report['evidence']), [])
        self.assertNotIn('板块分析', '\n'.join(report_paragraphs(report)))
        content = deepcopy(report['evidence']['analysisRaw'])
        content['overview'] = '板块证据[S1]'
        with self.assertRaisesRegex(ValueError, 'unlisted_citation'):
            validate(content, [], {'sources': [dict(id='Q1'), dict(id='S1')]})

    def test_full_universe_and_twenty_positions_fit_request_and_storage_limits(self):
        flows = flows_fixture()
        flows['industry']['rows'] = parse_industries([dict(industry(f'BK{i}', str(i - 64)), f14=f'合成行业{i}',
            f3=str(i - 64), f6='123456789', f104=20, f105=10) for i in range(128)], CUTOFF)
        flows['industry']['expectedCount'] = 128
        flows['holdings'] = [dict(symbol=f'{i:06}.SH', name=f'合成持仓{i}', kind='stock', flow=None,
                                 industryCode=f'BK{i}', note='') for i in range(20)]
        positions = [dict(symbol=f'{i:06}.SH', name=f'合成持仓{i}', assetType='stock', currency='CNY',
                          quantity='1', averageCost=None, horizon=None) for i in range(20)]
        snapshot = dict(positions=positions, cash={'CNY': None, 'USD': None}, profile={}, revision=1, updatedAt=CUTOFF.isoformat())
        articles = [dict(title='market', summary='合成新闻', url=f'https://example.com/news/{i}', publishedAt=CUTOFF.isoformat()) for i in range(5)]
        with patch('jobs.market.collect_flows', return_value=flows), patch('jobs.sectors.read_history', return_value=history()), \
                patch('jobs.market.read_quote', side_effect=lambda symbol, cutoff: dict(symbol=symbol, missing=True, sourceUrl='https://example.com/quote')), \
                patch('jobs.market.fetch', return_value=b'<rss/>'), patch('jobs.market.parse_feed', return_value=articles):
            evidence = collect(snapshot, CUTOFF)
        content = deepcopy(example_report()['evidence']['analysisRaw'])
        content['holdings'] = [dict(symbol=position['symbol'], advice='合成验证说明。' * 35 + '[P1]') for position in positions]
        content['sectorAnalysis'] = [dict(code=code, text='字' * 346 + '[S1]', email='字' * 96 + '[S1]')
                                     for code in evidence['sectors']['detailCodes']]
        client = Mock()
        client.complete_json.return_value = JsonCompletion(content, 100, 100, Decimal('.001'), 'test-model')
        saved = build_report(snapshot, evidence, CUTOFF, client)
        messages = client.complete_json.call_args.args[1]
        self.assertLess(len(json.dumps({'messages': messages}, ensure_ascii=False).encode()), 59000)
        self.assertLess(len(json.dumps(saved, ensure_ascii=False).encode()), 245000)
        self.assertEqual(len(saved['sources']), 40)
        self.assertEqual(len({source['id'] for source in saved['sources']}), 40)
        self.assertLessEqual(len(saved['paragraphs']), 100)
        self.assertLessEqual(max(map(len, saved['paragraphs'])), 6000)
        self.assertLessEqual(len(sector_email_lines(saved['evidence'])), 3)
        self.assertLessEqual(sum(map(len, sector_email_lines(saved['evidence']))), 300)


if __name__ == '__main__':
    unittest.main()
