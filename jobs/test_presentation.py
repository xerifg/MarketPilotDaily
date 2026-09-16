from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import Mock
from jobs.deepseek import JsonCompletion
from jobs.mail import MailConfig, build_message
from jobs.presentation import beijing, number, reading_lines, report_paragraphs
from jobs.report import build_report


def example_report(email_summary=None):
    # Synthetic data only. Never store a real portfolio in a test fixture.
    advice = dict(overview='市场涨跌分化，先核对证据。[Q1]', today='建议：先观察。\n依据与反证：信息不足。\n触发条件：补齐行情。\n失效条件：原有判断被新证据推翻。\n复查时间：下一次收盘后。',
                  short='建议：核对持有理由。\n复查时间：每周。', long='建议：复查资产配置。\n复查时间：每季度。', watch='<img src=x onerror=alert(1)>', holdings=[], evidenceIds=['Q1'])
    if email_summary is not None:
        advice['emailSummary'] = email_summary
    evidence = dict(quotes=[dict(id='Q1', symbol='000001.SH', close='3000.25', changePct='-1.20', currency='CNY',
                                amount='123456789', sessionDate='2026-09-11', previousSessionDate='2026-09-10', stale=False)],
                    news=[], coverage=['示例来源：未检出新闻。'], missing=['ETF 申赎尚未覆盖。'],
                    sources=[dict(id='Q1', title='示例指数', url='https://example.com/quotes', publishedAt='2026-09-11')])
    client = Mock()
    client.complete_json.return_value = JsonCompletion(advice, 10, 20, Decimal('0.001'), 'test-model')
    snapshot = dict(revision=1, updatedAt='2026-09-13T00:00:00Z', positions=[], cash={'CNY': None, 'USD': None}, profile={})
    return build_report(snapshot, evidence, datetime(2026, 9, 13, 0, 30, tzinfo=timezone.utc), client)


class PresentationTests(unittest.TestCase):
    def test_units_dates_and_small_prices_preserve_meaning(self):
        self.assertEqual(number('958186336970', amount=True), '9,581.86亿')
        self.assertEqual(number(None, amount=True), '未提供')
        self.assertEqual(number('0', amount=True), '0.00')
        self.assertEqual(number('0.0045'), '0.0045')
        self.assertEqual(number('0.87', signed=True), '+0.87')
        self.assertEqual(beijing('2026-09-12T20:30:00Z'), '2026-09-13 04:30')
        self.assertEqual(beijing('2026-09-11'), '2026-09-11')

    def test_archive_line_breaks_never_remove_words_or_citations(self):
        text = ('保留原文与依据[Q1]。' * 30) + '复查条件。'
        lines = reading_lines(text)
        self.assertGreater(len(lines), 1)
        self.assertEqual(''.join(lines), text)

    def test_formatted_report_is_readable_and_both_mail_parts_escape_safely(self):
        report = example_report(dict(conclusion='市场涨跌分化。[Q1]', actions=[],
                                     events=['<img src=x onerror=alert(1)>'], holdings=[],
                                     limitations='ETF 申赎尚未覆盖。'))
        self.assertEqual(report['evidence']['analysisStatus'], 'ok')
        plain = '\n'.join(report['paragraphs'])
        self.assertIn('3,000.25 点', plain)
        self.assertIn('1.23亿 CNY', plain)
        self.assertLess(plain.index('先看重点'), plain.index('市场收盘概览'))
        config = MailConfig('example@163.com', 'fake', 'example@163.com', 'example@163.com')
        message = build_message(config, 'preview', '测试日报', ['测试', *report['paragraphs']], [], report)
        html = message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('max-width:680px', html)
        self.assertIn('viewport', html)
        self.assertIn('&lt;img', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('短期 · 1–4 周', html)
        self.assertNotIn('1.23亿 CNY', html)
        self.assertIn('ETF 申赎尚未覆盖', html)
        self.assertIn('<img', message.get_body(preferencelist=('plain',)).get_content())

    def test_failed_analysis_does_not_render_invalid_raw_advice(self):
        report = example_report()
        report['evidence']['analysisStatus'] = 'failed'
        text = '\n'.join(report_paragraphs(report))
        self.assertNotIn('先看重点', text)
        self.assertNotIn('<img', text)
        self.assertIn('本期仅供核对行情与新闻', text)
