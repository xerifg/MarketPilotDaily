from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import Mock, patch

from jobs.fund_flows import (collect_flows, estimate_etf, flow_sources, parse_industries,
                             read_industries, read_nav, read_shares, read_stock)
from jobs.flow_presentation import flow_highlights, leaders
from jobs.mail import MailConfig, build_message
from jobs.market import collect
from jobs.presentation import report_paragraphs
from jobs.report import build_report, validate
from jobs.test_presentation import example_report

CUTOFF = datetime(2026, 9, 17, 0, tzinfo=timezone.utc)
STAMP = int(datetime(2026, 9, 16, 8, tzinfo=timezone.utc).timestamp())


def industry(code='BK0001', net='100000000', net5='200000000', stamp=STAMP):
    return dict(f12=code, f14='示例行业' + code, f62=net, f164=net5, f124=stamp)


def flows_fixture():
    rows = parse_industries([industry('BK0001'), industry('BK0002', '-50000000', '10000000'),
                            industry('BK0003', '0'), industry('BK0004', '20000000')], CUTOFF)
    etf = dict(code='510300', name='示例ETF', date='2026-09-16', net='20000000', net5='-10000000',
               previousDate='2026-09-15', navDate='2026-09-15', shareChange='10000000', stale=False,
               sourceIds=['F2', 'F3'], sourceUrl='https://fund.eastmoney.com/510300.html')
    return dict(industry=dict(status='ok', date='2026-09-16', expectedCount=4, rows=rows, note='主力为大单统计'),
                etf=dict(status='ok', date='2026-09-16', expectedCount=1, rows=[etf], note='观察池净申赎估算'),
                holdings=[dict(symbol='510300.SH', name='示例ETF', kind='etf', flow=etf, industryCode=None, note='')],
                limitations=['示例覆盖范围'], fetchedAt=CUTOFF.isoformat())


class FundFlowTests(unittest.TestCase):
    def test_industry_ranking_preserves_sign_zero_and_missing_five_day(self):
        rows = parse_industries([industry('BK1', '-100'), industry('BK2', '0'), industry('BK3', '50', '-')], CUTOFF)
        self.assertEqual([r['net'] for r in rows], ['50', '0', '-100'])
        self.assertIsNone(rows[0]['net5'])
        self.assertEqual([r['code'] for r in leaders(rows, True)], ['BK3'])
        self.assertEqual([r['code'] for r in leaders(rows, False)], ['BK1'])
        for bad in ('NaN', 'Infinity', '-', None):
            self.assertEqual(parse_industries([industry(net=bad)], CUTOFF), [])

    def test_dates_duplicates_and_staleness_cannot_become_current_rankings(self):
        with self.assertRaises(ValueError):
            parse_industries([industry(), industry()], CUTOFF)
        self.assertEqual(parse_industries([industry(stamp=STAMP + 86400)], CUTOFF), [])
        intraday = datetime(2026, 9, 16, 6, tzinfo=timezone.utc)
        self.assertEqual(parse_industries([industry(stamp=int(intraday.timestamp()))], intraday), [])
        old = parse_industries([industry(stamp=STAMP - 86400 * 10)], CUTOFF)
        self.assertTrue(old[0]['stale'])
        self.assertEqual(leaders(old, True), [])
        mixed = parse_industries([industry(), industry('BK2', stamp=STAMP - 86400)], CUTOFF)
        self.assertEqual(len(mixed), 1)

    @patch('jobs.fund_flows.fetch_json')
    def test_pagination_is_complete_and_duplicate_pages_are_rejected(self, fetch):
        fetch.side_effect = [dict(data=dict(total=2, diff=[industry('BK1')])), dict(data=dict(total=2, diff=[industry('BK2')]))]
        self.assertEqual(len(read_industries(CUTOFF)['rows']), 2)
        self.assertEqual(fetch.call_args_list[1].args[1]['pn'], 2)
        fetch.side_effect = [dict(data=dict(total=2, diff=[industry()])), dict(data=dict(total=2, diff=[industry()]))]
        with self.assertRaises(ValueError):
            read_industries(CUTOFF)

    @patch('jobs.fund_flows.fetch_json')
    def test_official_share_units_and_dates_are_checked(self, fetch):
        payload = dict(result=[dict(SEC_CODE='510300', SEC_NAME='ETF', STAT_DATE='2026-09-16', TOT_VOL='12.5')], pageHelp=dict(total=1))
        fetch.return_value = payload
        self.assertEqual(read_shares('2026-09-16')['510300']['shares'], Decimal('125000'))
        with self.assertRaises(ValueError):
            read_shares('2026-09-15')
        payload['pageHelp']['total'] = 2
        with self.assertRaises(ValueError):
            read_shares('2026-09-16')

    def etf_inputs(self):
        days = [f'2026-09-{d}' for d in (16, 15, 14, 11, 10, 9)]
        shares = {day: {'510300': dict(name='ETF', shares=Decimal(150 - i * 10))} for i, day in enumerate(days)}
        navs = {day: dict(nav=Decimal(1 + i), corporateAction=False) for i, day in enumerate(days)}
        return days, shares, navs

    def test_etf_estimate_uses_share_change_and_prior_nav_not_price_or_aum_change(self):
        days, shares, navs = self.etf_inputs()
        row = estimate_etf('510300', days, shares, navs, CUTOFF)
        self.assertEqual(row['net'], '20.00')
        self.assertEqual(row['net5'], '200.00')
        self.assertEqual(row['shareChange'], '10')
        self.assertEqual(row['navDate'], '2026-09-15')
        shares[days[0]]['510300']['shares'] = Decimal(140)
        self.assertEqual(estimate_etf('510300', days, shares, navs, CUTOFF)['net'], '0.00')

    def test_missing_sessions_and_corporate_actions_never_become_five_day_totals(self):
        days, shares, navs = self.etf_inputs()
        del shares[days[3]]
        row = estimate_etf('510300', days, shares, navs, CUTOFF)
        self.assertIsNone(row['net5'])
        del shares[days[1]]
        with self.assertRaises(ValueError):
            estimate_etf('510300', days, shares, navs, CUTOFF)
        days, shares, navs = self.etf_inputs()
        navs[days[0]]['corporateAction'] = True
        with self.assertRaises(ValueError):
            estimate_etf('510300', days, shares, navs, CUTOFF)
        navs[days[0]]['corporateAction'] = False
        shares[days[0]]['510300']['shares'] = shares[days[1]]['510300']['shares'] * 2
        with self.assertRaises(ValueError):
            estimate_etf('510300', days, shares, navs, CUTOFF)

    @patch('jobs.fund_flows.fetch_json')
    def test_nav_excludes_future_and_detects_split_flags(self, fetch):
        fetch.return_value = dict(ErrCode=0, Data=dict(LSJZList=[dict(FSRQ='2026-09-17', DWJZ='2'),
                                      dict(FSRQ='2026-09-16', DWJZ='1', FHFCZ='split')]))
        rows = read_nav('510300', CUTOFF)
        self.assertEqual(list(rows), ['2026-09-16'])
        self.assertTrue(rows['2026-09-16']['corporateAction'])

    @patch('jobs.fund_flows.fetch_json')
    def test_stock_identity_and_industry_mapping_are_evidence_based(self, fetch):
        payload = industry('600519', '-100', '-500')
        fetch.side_effect = [dict(data=dict(diff=[payload])), dict(data=dict(f57='600519', f127='白酒Ⅱ'))]
        row, name = read_stock(dict(symbol='600519.SH', name='示例'), CUTOFF)
        self.assertEqual((row['net'], row['net5'], name), ('-100', '-500', '白酒Ⅱ'))
        payload['f12'] = '000001'
        fetch.side_effect = [dict(data=dict(diff=[payload]))]
        with self.assertRaises(ValueError):
            read_stock(dict(symbol='600519.SH', name='示例'), CUTOFF)

    @patch('jobs.fund_flows.fetch_json', side_effect=OSError('offline'))
    def test_source_failure_and_unsupported_markets_are_explicit_not_zero(self, _fetch):
        rows = [dict(symbol='SPY.US', name='US ETF', assetType='etf'),
                dict(symbol='159915.SZ', name='SZ ETF', assetType='etf')]
        flows = collect_flows(rows, CUTOFF)
        self.assertEqual(flows['industry']['status'], 'missing')
        self.assertEqual(flows['etf']['status'], 'missing')
        self.assertTrue(all(row['flow'] is None for row in flows['holdings']))
        self.assertIn('美股', flows['holdings'][0]['note'])
        self.assertIn('深市', flows['holdings'][1]['note'])

    def test_both_mail_parts_get_only_flow_highlights_even_when_ai_fails(self):
        report = example_report()
        flows = flows_fixture()
        report['evidence']['fundFlows'] = flows
        report['evidence']['analysisStatus'] = 'failed'
        report['sources'].extend(flow_sources(flows))
        config = MailConfig('example@163.com', 'fake', 'example@163.com', 'example@163.com')
        message = build_message(config, 'flows-test', '日报', ['发送时间'], [], report)
        for part in ('plain', 'html'):
            body = message.get_body(preferencelist=(part,)).get_content()
            self.assertIn('资金流向重点', body)
            self.assertIn('2026-09-16', body)
            self.assertIn('净申购前三', body)
            self.assertIn('方向相反', body)
            self.assertIn('[F2]', body)
            self.assertIn('https://fund.eastmoney.com/jzzzl.html', body)
            self.assertNotIn('BK0003', body)  # zero flow is not a leader
        flows['industry']['rows'][0]['stale'] = True
        self.assertNotIn('示例行业BK0001', '\n'.join(flow_highlights(flows)))

    def test_full_report_keeps_complete_flow_rows_without_exceeding_storage_paragraph_limit(self):
        report = example_report()
        flows = flows_fixture()
        flows['industry']['rows'] = parse_industries([industry(f'BK{i}') for i in range(128)], CUTOFF)
        report['evidence']['fundFlows'] = flows
        paragraphs = report_paragraphs(report)
        self.assertIn('BK127', '\n'.join(paragraphs))
        self.assertLess(max(map(len, paragraphs)), 6000)
        self.assertLess(len(paragraphs), 100)

    @patch('jobs.market.collect_flows', return_value=flows_fixture())
    @patch('jobs.market.read_quote', side_effect=lambda symbol, cutoff: dict(symbol=symbol, missing=True, sourceUrl='https://example.com/quote'))
    @patch('jobs.market.fetch', return_value=b'<rss/>')
    @patch('jobs.market.parse_feed', return_value=[dict(title='market', summary='x', url='https://example.com/news', publishedAt=CUTOFF.isoformat()) for _ in range(5)])
    def test_evidence_source_budget_and_model_input_include_funds(self, *_mocks):
        snapshot = dict(positions=[dict(symbol=f'{i:06}.SH', name='示例', assetType='stock', currency='CNY',
                            quantity='1', averageCost=None, horizon=None) for i in range(20)], cash={'CNY': None, 'USD': None},
                            profile={}, revision=1, updatedAt=CUTOFF.isoformat())
        evidence = collect(snapshot, CUTOFF)
        self.assertEqual(len(evidence['sources']), 39)
        client = Mock()
        client.complete_json.side_effect = ValueError('synthetic_failure')
        report = build_report(snapshot, evidence, CUTOFF, client)
        prompt = json.loads(client.complete_json.call_args.args[1][1]['content'])
        self.assertIn('行业主力', '\n'.join(prompt['fundFlowHighlights']))
        self.assertEqual(len(report['sources']), 40)
        self.assertEqual(len({s['id'] for s in report['sources']}), 40)
        self.assertNotIn('未接入可靠资金净流入', '\n'.join(evidence['missing']))

    def test_flow_citations_are_subject_to_existing_validation(self):
        advice = deepcopy(example_report()['evidence']['analysisRaw'])
        advice['overview'] = '资金方向[F1]'
        with self.assertRaisesRegex(ValueError, 'unlisted_citation'):
            validate(advice, [], {'sources': [dict(id='Q1'), dict(id='F1')]})


if __name__ == '__main__':
    unittest.main()
