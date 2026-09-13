"""Readable report views, derived from saved evidence without another AI call."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
import re
from zoneinfo import ZoneInfo
from jobs.market import BENCHMARKS

SECTIONS = [('overview', '先看重点'), ('today', '今日 · 行动计划'),
            ('short', '短期 · 1–4 周'), ('long', '长期 · 6–24 个月'), ('watch', '事件与风险')]
RISK_NOTE = '指标说明：可承受回撤不是仓位上限。本系统未计算账户历史回撤，也不能保证某个仓位对应某个回撤上限。'


def beijing(value):
    if len(value) == 10:
        return value
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')


def number(value, amount=False, signed=False):
    if value is None:
        return '未提供'
    try:
        n = Decimal(str(value))
        if not n.is_finite():
            return '未提供'
        unit = ''
        if amount:
            for divisor, label in [(100000000, '亿'), (10000, '万')]:
                if abs(n) >= divisor:
                    n, unit = n / divisor, label
                    break
        formatted = f'{n:,.2f}' if amount or signed else f'{n:,.6f}'.rstrip('0').rstrip('.')
        return ('+' if signed and n > 0 else '') + formatted + unit
    except InvalidOperation:
        return '未提供'


def reading_lines(text):
    # Preserve every word/citation in old reports; only add paragraph breaks.
    lines = []
    for line in text.splitlines():
        buffer = ''
        for sentence in re.split(r'(?<=。)', line):
            buffer += sentence
            if len(buffer) >= 120:
                lines.append(buffer.strip())
                buffer = ''
        if buffer.strip():
            lines.append(buffer.strip())
    return lines


def report_sections(report):
    evidence = report.get('evidence', {})
    analysis = evidence.get('analysisRaw') if evidence.get('analysisStatus') == 'ok' else None
    sections = []
    if analysis:
        sections.extend((title, reading_lines(analysis[key])) for key, title in SECTIONS)
        names = {row['symbol']: row['name'] for row in evidence.get('metrics', [])}
        sections.extend((f"持仓复查 · {names.get(row['symbol'], row['symbol'])}（{row['symbol']}）", reading_lines(row['advice']))
                        for row in analysis['holdings'])
    else:
        sections.append(('分析状态', ['AI 建议未生成或未通过校验，本期仅供核对行情与新闻。']))
    market = []
    names = {row['symbol']: row['name'] for row in evidence.get('metrics', [])}
    for quote in evidence.get('quotes', []):
        name = BENCHMARKS.get(quote['symbol'], names.get(quote['symbol'], quote['symbol']))
        if quote.get('missing'):
            market.append(f'{name}：行情缺失')
        else:
            unit = '点' if quote['symbol'] in ('000001.SH', '399001.SZ') else quote['currency']
            market.append(f"{name}｜{number(quote['close'])} {unit}｜{number(quote['changePct'], signed=True)}%"
                          f"\n收盘日 {quote['sessionDate']}，对比 {quote['previousSessionDate']}；成交额 {number(quote.get('amount'), amount=True)}"
                          f"{' ' + quote['currency'] if quote.get('amount') else ''} [{quote['id']}]"
                          + (' · 数据偏旧，不能作为今日触发依据' if quote.get('stale') else ''))
    sections.append(('市场收盘概览', market or ['本期无可用行情。']))
    sections.append(('过去 24 小时 · 新闻线索', [f"{item['title']} [{item['id']}]\n{beijing(item['publishedAt'])}（北京时间）"
                    for item in evidence.get('news', [])] or ['本期未检出符合时间窗口的相关新闻；不代表没有市场事件。']))
    sections.append(('数据覆盖与限制', evidence.get('coverage', []) + evidence.get('missing', [])))
    return sections


def report_paragraphs(report):
    return [f"信息截止 {beijing(report['cutoffAt'])}（北京时间）｜新闻窗口：此前 24 小时｜行情：最近可取得收盘价",
            RISK_NOTE,
            *[title + '\n' + '\n\n'.join(lines) for title, lines in report_sections(report)],
            '使用说明：先核实数据，再决定操作；本日报不自动交易。仓位按同币种计算，人民币与美元没有直接相加。']


def email_body(report):
    """Return HTML content only. Mail transport validates all external links."""
    def paragraph(text):
        return '<p style="margin:10px 0;color:#30473e;font-size:15px;line-height:1.85;overflow-wrap:anywhere">' + escape(text).replace('\n', '<br>') + '</p>'
    markup = paragraph(f"信息截止 {beijing(report['cutoffAt'])}（北京时间） · 新闻窗口：此前 24 小时")
    markup += paragraph('行情为最近可取得收盘价；涨跌不等于资金净流入。')
    markup += paragraph(RISK_NOTE)
    for title, lines in report_sections(report):
        markup += '<h2 style="margin:28px 0 12px;padding-bottom:10px;border-bottom:1px solid #dfe8e1;font-size:19px;color:#204b3c">' + escape(title) + '</h2>'
        if title == '市场收盘概览':
            for line in lines:
                headline, _, details = line.partition('\n')
                cells = headline.split('｜')
                markup += '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:10px 0;border:1px solid #dfe8e1;border-radius:6px"><tr><td style="padding:12px 14px">'
                markup += '<strong style="font-size:14px;color:#30473e">' + escape(cells[0]) + '</strong>'
                if len(cells) == 3:
                    markup += '<p style="margin:6px 0;font-size:20px;color:#204b3c">' + escape(cells[1]) + ' <span style="font-size:15px">' + escape(cells[2]) + '</span></p>'
                markup += '<p style="margin:6px 0;font-size:12px;line-height:1.8;color:#52665b">' + escape(details) + '</p></td></tr></table>'
        else:
            markup += ''.join(paragraph(line) for line in lines)
    markup += paragraph('先核实数据，再决定操作；本日报不自动交易。仓位按同币种计算，人民币与美元没有直接相加。')
    return markup
