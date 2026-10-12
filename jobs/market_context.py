"""Dated exchange margin balances and verified calendar context."""
from datetime import timedelta
from urllib.request import Request, urlopen
import xlrd
from jobs.calendar import CALENDAR, calendar_evidence, latest_closed_session
from jobs.fund_flows import attempt, decimal, fetch_json, stale
from jobs.settings import SETTINGS


def read_margin(exchange, cutoff):
    day = latest_closed_session(cutoff)
    if day is None:
        raise ValueError('calendar_outside_coverage')
    if exchange == 'SH':
        url = 'https://query.sse.com.cn/marketdata/tradedata/queryMargin.do'
        raw = fetch_json(url, {'isPagination': 'true', 'beginDate': (day - timedelta(days=14)).strftime('%Y%m%d'),
                              'endDate': day.strftime('%Y%m%d'), 'tabType': '', 'stockCode': '',
                              'pageHelp.pageSize': 100, 'pageHelp.pageNo': 1}, SETTINGS['sources']['sse']['referer'])
        if len(raw['result']) != int(raw['pageHelp']['total']):
            raise ValueError('incomplete_margin')
        rows = []
        for row in raw['result']:
            stamp = row['opDate']
            date = f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}'
            if date > day.isoformat():
                raise ValueError('future_margin')
            rows.append((date, row['rzye'], row['rqylje'], row['rzrqjyzl']))
        if not rows:
            raise ValueError('missing_margin')
        date, financing, securities, total = max(rows)
        source_id = 'D2'
        source_url = 'https://www.sse.com.cn/market/othersdata/margin/sum/'
    else:
        raw = fetch_json(SETTINGS['sources']['szse']['reportUrl'], dict(
            SHOWTYPE='JSON', CATALOGID='1837_xxpl', txtDate=day.isoformat(), tab1PAGENO=1),
            SETTINGS['sources']['szse']['referer'])[0]
        if raw['metadata']['catalogid'] != '1837_xxpl' or raw['metadata']['subname'].strip() != day.isoformat() or len(raw['data']) != 1:
            raise ValueError('invalid_sz_margin_date')
        if not all('亿元' in raw['metadata']['cols'][key] for key in ('jrrzye', 'jrrjye', 'jrrzrjye')):
            raise ValueError('unknown_sz_margin_unit')
        row = raw['data'][0]
        financing, securities, total = [decimal(row[key].replace(',', '')) * 100000000
                                         for key in ('jrrzye', 'jrrjye', 'jrrzrjye')]
        date, source_id = day.isoformat(), 'D3'
        source_url = 'https://www.szse.cn/disclosure/margin/margin/index.html'
    values = [decimal(value) for value in (financing, securities, total)]
    if any(value < 0 for value in values):
        raise ValueError('negative_margin')
    # Shenzhen's rounded billion-yuan figures may differ by up to one unit in the final decimal.
    if abs(values[0] + values[1] - values[2]) > 2000000:
        raise ValueError('inconsistent_margin_total')
    return dict(exchange=exchange, date=date, financing=str(values[0]), securities=str(values[1]), total=str(values[2]),
                stale=stale(date, cutoff), sourceId=source_id, sourceUrl=source_url)


def collect_context(cutoff):
    margin = [row for exchange in ('SH', 'SZ') if (row := attempt(read_margin, exchange, cutoff))]
    valuation = attempt(read_valuation, cutoff)
    return dict(calendar=calendar_evidence(cutoff), margin=margin, valuation=valuation,
                limitations=['沪深两融余额分别展示；来源日期不一致时不合计，余额不等于新增资金。'] +
                (['部分交易所两融余额本次未取得。'] if len(margin) < 2 else []) +
                (['沪深300估值本次未取得。'] if valuation is None else []))


def read_valuation(cutoff):
    url = 'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/indicator/000300indicator.xls'
    with urlopen(Request(url, headers={'User-Agent': 'MarketPilotDaily/0.1 (personal research)'}), timeout=15) as response:
        raw = response.read(1000001)
    if len(raw) > 1000000:
        raise ValueError('valuation_too_large')
    sheet = xlrd.open_workbook(file_contents=raw).sheet_by_index(0)
    if sheet.ncols != 10 or '总股本' not in str(sheet.cell_value(0, 6)) or '计算用股本' not in str(sheet.cell_value(0, 7)):
        raise ValueError('valuation_schema_changed')
    last = latest_closed_session(cutoff)
    if last is None:
        raise ValueError('calendar_outside_coverage')
    rows = []
    for index in range(1, sheet.nrows):
        row = sheet.row_values(index)
        stamp = str(row[0])
        day = f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}'
        if row[1] != '000300':
            raise ValueError('valuation_identity_mismatch')
        if day <= last.isoformat():
            values = [decimal(value) for value in row[6:10]]
            if any(value < 0 for value in values):
                raise ValueError('invalid_valuation')
            rows.append(dict(date=day, peTotal=str(values[0]), peIndex=str(values[1]),
                             dividendTotal=str(values[2]), dividendIndex=str(values[3]),
                             sourceId='D4', sourceUrl=url, stale=stale(day, cutoff)))
    if not rows or len({row['date'] for row in rows}) != len(rows):
        raise ValueError('missing_or_duplicate_valuation')
    return max(rows, key=lambda row: row['date'])


def context_sources(context):
    sources = [dict(id='D1', title='交易所休市与美联储计划会议（各项附官方来源）', url=CALENDAR['cnSource'],
                 publishedAt=CALENDAR['verifiedOn']),
            *[dict(id=row['sourceId'], title=('上交所' if row['exchange'] == 'SH' else '深交所') + '融资融券余额',
                   url=row['sourceUrl'], publishedAt=row['date']) for row in context['margin']]]
    if context.get('valuation'):
        row = context['valuation']
        sources.append(dict(id='D4', title='中证沪深300指数估值', url=row['sourceUrl'], publishedAt=row['date']))
    return sources


def context_sections(context):
    if not context:
        return []
    calendar = context['calendar']
    lines = [f"最近已收盘交易日：沪深{calendar['chinaSession'] or '未核验'}；美股{calendar['usSession'] or '未核验'}。[D1]",
             calendar['limitation'], '官方日历：' + '；'.join(calendar['sourceUrls'])]
    lines += [f"{event['title']}：{event['start']}至{event['end']}（当地日期，计划可能调整）。[D1]" for event in calendar['events']]
    balances = [f"{'沪市' if row['exchange'] == 'SH' else '深市'}｜{row['date']}｜融资余额{decimal(row['financing']) / 100000000:.2f}亿元｜融券余额{decimal(row['securities']) / 100000000:.2f}亿元｜两融合计{decimal(row['total']) / 100000000:.2f}亿元"
                + ('｜偏旧' if row['stale'] else '') + f"[{row['sourceId']}]" for row in context['margin']]
    result = [('交易日与已核验事件', lines), ('融资融券余额', balances + context['limitations'])]
    if context.get('valuation'):
        row = context['valuation']
        result.append(('沪深300指数估值', [f"{row['date']}｜市盈率（总股本）{row['peTotal']}倍；市盈率（计算用股本）{row['peIndex']}倍；股息率分别{row['dividendTotal']}%、{row['dividendIndex']}%。[D4]",
                      '该指标仅对应沪深300，不能代替持仓ETF跟踪指数估值；未计算历史估值分位。']))
    return result
