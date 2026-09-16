from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from jobs.daily import main
from jobs.mail import MailConfig, build_message
from jobs.email_summary import validate_summary
from jobs.test_presentation import example_report


def summary():
    return dict(conclusion='市场分化，今日先观察。[Q1]',
                actions=['指数观察：补齐最新行情后再评估；若证据相反则取消原判断，下一次收盘后复查。[Q1]'],
                events=['示例事件可能影响市场，仍需核实。[N1]'],
                holdings=[], limitations='ETF 申赎数据缺失，暂不能判断申赎方向。')


class EmailSummaryTests(unittest.TestCase):
    def message(self, report):
        config = MailConfig('example@163.com', 'fake', 'example@163.com', 'example@163.com')
        return build_message(config, 'summary-test', '日报', ['发送时间', *report['paragraphs']],
                             [(s['title'], s['url']) for s in report['sources']], report)

    def report(self):
        report = example_report()
        report['sources'].extend([
            dict(id='N1', title='重要事件来源', url='https://example.com/important', publishedAt='2026-09-13'),
            dict(id='N2', title='未引用的新闻', url='https://example.com/unused', publishedAt='2026-09-13')])
        report['evidence']['emailSummary'] = summary()
        return report

    def test_both_parts_only_send_summary_with_relevant_sources_and_complete_conditions(self):
        report = self.report()
        before = deepcopy(report)
        message = self.message(report)
        for part in ('plain', 'html'):
            body = message.get_body(preferencelist=(part,)).get_content()
            for expected in ('今日结论', '今日行动', '重要事件与风险', '下一次收盘后复查',
                             '若证据相反则取消原判断', 'ETF 申赎数据缺失',
                             'https://example.com/important', '查看完整日报', '2026-09-13 08:30'):
                self.assertIn(expected, body)
            for omitted in ('短期 · 1–4 周', '长期 · 6–24 个月', '市场收盘概览', '1.23亿',
                            '过去 24 小时', 'https://example.com/unused', '持仓复查'):
                self.assertNotIn(omitted, body)
        self.assertEqual(report, before)

    def test_quiet_day_stays_short_and_omits_empty_sections(self):
        report = self.report()
        report['evidence']['missing'] = []
        report['evidence']['emailSummary'] = dict(conclusion='现有证据未显示需调整的事项，继续观察。',
                                                 actions=[], events=[], holdings=[], limitations='')
        body = self.message(report).get_body(preferencelist=('plain',)).get_content()
        self.assertIn('暂无明确行动，继续观察', body)
        for omitted in ('重要事件与风险', '持仓重要变化', '信息来源', '数据提醒'):
            self.assertNotIn(omitted, body)
        self.assertLess(len(body), 350)

    def test_legacy_invalid_and_failed_summary_never_fall_back_to_full_report(self):
        for kind in ('legacy', 'invalid', 'failed'):
            report = self.report()
            if kind == 'legacy':
                del report['evidence']['emailSummary']
            elif kind == 'invalid':
                report['evidence']['emailSummary']['conclusion'] = '未核实的结论[N99]'
            else:
                report['evidence']['analysisStatus'] = 'failed'
            with self.subTest(kind=kind):
                message = self.message(report)
                for part in ('plain', 'html'):
                    body = message.get_body(preferencelist=(part,)).get_content()
                    self.assertIn('查看完整日报', body)
                    self.assertNotIn('市场收盘概览', body)
                    self.assertNotIn('未核实的结论', body)
                    self.assertNotIn('https://example.com/important', body)
                    if kind == 'failed':
                        self.assertIn('未生成或未通过校验', body)
                        self.assertNotIn('市场涨跌分化', body)

    def test_stale_quotes_remain_visible_even_if_summary_omits_warning(self):
        report = self.report()
        report['evidence']['emailSummary']['limitations'] = ''
        report['evidence']['quotes'][0]['stale'] = True
        body = self.message(report).get_body(preferencelist=('plain',)).get_content()
        self.assertIn('行情缺失或偏旧', body)

    def test_summary_is_saved_with_full_report_in_same_generation(self):
        digest = summary()
        digest['events'] = []
        report = example_report(digest)
        self.assertEqual(report['evidence']['emailSummary'], digest)
        self.assertEqual(report['evidence']['analysisStatus'], 'ok')
        self.assertIn('长期 · 6–24 个月', '\n'.join(report['paragraphs']))

    def test_invalid_digest_does_not_discard_full_analysis(self):
        digest = summary()
        digest['actions'] = ['观察'] * 4
        report = example_report(digest)
        self.assertEqual(report['evidence']['analysisStatus'], 'ok')
        self.assertNotIn('emailSummary', report['evidence'])
        self.assertIn('市场收盘概览', '\n'.join(report['paragraphs']))

    def test_summary_bounds_and_citations_are_validated(self):
        report = self.report()
        for key, value in [('conclusion', ''), ('conclusion', '字' * 151), ('events', ['事项'] * 4),
                           ('actions', '错误格式'), ('holdings', [None]), ('events', ['未经核实[N99]']),
                           ('limitations', '字' * 121)]:
            digest = summary()
            digest[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_summary(digest, report['sources'])
        digest = dict(conclusion='字' * 150, actions=['字' * 150] * 3,
                      events=[], holdings=[], limitations='字')
        with self.assertRaisesRegex(ValueError, 'email_summary_too_long'):
            validate_summary(digest, report['sources'])

    def test_saved_report_delivery_sends_digest_without_regenerating(self):
        report = self.report()
        gateway = Mock()
        gateway.post.return_value = dict(id='test-2026-09-13', report=report, delivery=None)
        gateway.claim.return_value = True
        smtp = Mock()
        for operation in ('ehlo', 'mail', 'rcpt', 'data'):
            getattr(smtp, operation).return_value = (250, b'ok')
        env = dict(SMTP_USERNAME='example@163.com', SMTP_AUTH_CODE='fake', MAIL_FROM='example@163.com',
                   MAIL_TO='example@163.com', DEEPSEEK_API_KEY='fake', REPORT_MODE='test', REPORT_VERSION='1')
        with patch.dict('os.environ', env), patch('jobs.daily.Gateway', return_value=gateway), \
                patch('jobs.daily.build_report') as generate, patch('jobs.mail.smtplib.SMTP_SSL', return_value=smtp), \
                patch('builtins.print'):
            self.assertEqual(main(), 0)
        generate.assert_not_called()
        from email import policy
        from email.parser import BytesParser
        message = BytesParser(policy=policy.default).parsebytes(smtp.data.call_args.args[0])
        for part in ('plain', 'html'):
            body = message.get_body(preferencelist=(part,)).get_content()
            self.assertIn('今日结论', body)
            self.assertNotIn('市场收盘概览', body)
            self.assertNotIn('https://example.com/unused', body)
        gateway.finish.assert_called_once_with('test-2026-09-13', 'smtp_accepted', None)


if __name__ == '__main__':
    unittest.main()
