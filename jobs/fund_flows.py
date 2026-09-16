"""Public A-share order-flow statistics and estimated Shanghai ETF subscriptions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CN = ZoneInfo('Asia/Shanghai')
INDUSTRY_URL = 'https://data.eastmoney.com/bkzj/hy.html'
SHARES_URL = 'https://query.sse.com.cn/commonQuery.do'
NAV_URL = 'https://api.fund.eastmoney.com/f10/lsjz'
# A representative observation basket, never presented as the entire ETF market.
ETF_BASKET = {'510300': '沪深300', '510500': '中证500', '588000': '科创50',
              '512480': '半导体', '512660': '军工', '512010': '医药',
              '515790': '光伏', '518880': '黄金', '511010': '国债'}
INDUSTRY_NOTE = '东方财富行业榜口径：主力净流入为大单与超大单交易统计，不代表全市场新增资金；行业与个股不可相加。'
ETF_NOTE = '沪市代表ETF及已持有的沪市ETF，非全市场排名。估算净申赎＝份额变化×前一交易日单位净值；实物申赎不等于现金进出。'


def fetch_json(url, params=None, referer=None):
    headers = {'User-Agent': 'MarketPilotDaily/0.1 (personal research)'}
    if referer:
        headers['Referer'] = referer
    target = url + ('?' + urlencode(params) if params else '')
    with urlopen(Request(target, headers=headers), timeout=15) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('flow_source_too_large')
        return json.loads(raw, parse_float=Decimal)


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('invalid_flow_number')
    return result


def closed_day(value, cutoff):
    day = date.fromisoformat(value)
    local = cutoff.astimezone(CN)
    return day <= local.date() and not (day == local.date() and local.hour < 16)


def stale(value, cutoff):
    return (cutoff.astimezone(CN).date() - date.fromisoformat(value)).days > 4


def parse_industries(raw, cutoff):
    rows, seen = [], set()
    for item in raw:
        code, name = item['f12'], item['f14']
        if code in seen or not re.fullmatch(r'BK\d+', code) or not isinstance(name, str) or not name.strip():
            raise ValueError('invalid_industry_identity')
        seen.add(code)
        stamp = datetime.fromtimestamp(int(item['f124']), CN)
        day = stamp.date().isoformat()
        if stamp > cutoff or not closed_day(day, cutoff) or stamp.hour < 15:
            continue
        try:
            net = str(decimal(item['f62']))
        except (ValueError, InvalidOperation):
            continue
        try:
            net5 = str(decimal(item.get('f164')))
        except (ValueError, InvalidOperation):
            net5 = None
        rows.append(dict(code=code, name=name, date=day, net=net, net5=net5, stale=stale(day, cutoff),
                         sourceIds=['F1'], sourceUrl=f'https://data.eastmoney.com/bkzj/{code}.html'))
    # Never compare rankings from different sessions.
    latest = max((row['date'] for row in rows), default=None)
    return sorted([row for row in rows if row['date'] == latest], key=lambda row: decimal(row['net']), reverse=True)


def read_industries(cutoff):
    raw, total = [], None
    for page in range(1, 7):
        data = fetch_json('https://push2.eastmoney.com/api/qt/clist/get', dict(
            pn=page, pz=100, po=1, np=1, fltt=2, invt=2, fid='f62', fs='m:90 s:4',
            fields='f12,f14,f62,f164,f124'), INDUSTRY_URL)['data']
        if total is not None and total != data['total']:
            raise ValueError('industry_universe_changed')
        total = data['total']
        raw.extend(data['diff'])
        if len(raw) >= total:
            break
    if not raw or len(raw) != total:
        raise ValueError('incomplete_industry_pages')
    rows = parse_industries(raw, cutoff)
    return dict(status='ok' if len(rows) == total else 'partial' if rows else 'missing',
                date=rows[0]['date'] if rows else None, rows=rows, expectedCount=total, note=INDUSTRY_NOTE)


def read_nav(code, cutoff):
    result = fetch_json(NAV_URL, dict(fundCode=code, pageIndex=1, pageSize=12,
                                    startDate='', endDate=cutoff.astimezone(CN).date().isoformat()),
                        'https://fund.eastmoney.com/')
    if result.get('ErrCode') != 0:
        raise ValueError('nav_unavailable')
    rows = {}
    for item in result['Data']['LSJZList']:
        day = item['FSRQ']
        if not closed_day(day, cutoff):
            continue
        nav = decimal(item['DWJZ'])
        if day in rows or nav <= 0:
            raise ValueError('invalid_nav')
        rows[day] = dict(nav=nav, corporateAction=bool(item.get('FHFCZ') or item.get('FHSP')))
    return rows


def read_shares(day):
    result = fetch_json(SHARES_URL, {'sqlId': 'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L',
                                    'STAT_DATE': day, 'isPagination': 'true',
                                    'pageHelp.pageSize': 10000, 'pageHelp.pageNo': 1}, 'https://www.sse.com.cn/')
    raw = result['result']
    if len(raw) != int(result['pageHelp']['total']):
        raise ValueError('incomplete_etf_shares')
    rows = {}
    for item in raw:
        code = item['SEC_CODE']
        if item['STAT_DATE'] != day or code in rows:
            raise ValueError('invalid_share_date_or_duplicate')
        shares = decimal(item['TOT_VOL']) * 10000  # SSE publishes units of ten thousand shares.
        if shares < 0:
            raise ValueError('invalid_shares')
        rows[code] = dict(shares=shares, name=item['SEC_NAME'])
    return rows


def estimate_etf(code, days, shares, navs, cutoff):
    """Require adjacent observed NAV sessions; never bridge missing share dates."""
    if len(days) < 2:
        raise ValueError('insufficient_etf_sessions')
    values = []
    for current, previous in zip(days, days[1:]):
        now, before = shares.get(current, {}).get(code), shares.get(previous, {}).get(code)
        nav, prior_nav = navs.get(current), navs.get(previous)
        valid = now and before and nav and prior_nav and not (nav['corporateAction'] or prior_nav['corporateAction'])
        # A large share/NAV discontinuity needs a split/merger check, not an inflow claim.
        if valid and (before['shares'] <= 0 or now['shares'] / before['shares'] >= 2 or now['shares'] / before['shares'] <= Decimal('.5')):
            valid = False
        values.append((now['shares'] - before['shares']) * prior_nav['nav'] if valid else None)
    if values[0] is None:
        raise ValueError('etf_comparison_unavailable')
    current, previous = days[:2]
    return dict(code=code, name=shares[current][code]['name'], date=current, previousDate=previous,
                net=str(values[0].quantize(Decimal('.01'))),
                net5=str(sum(values[:5]).quantize(Decimal('.01'))) if len(values) >= 5 and all(v is not None for v in values[:5]) else None,
                shareChange=str(shares[current][code]['shares'] - shares[previous][code]['shares']),
                navDate=previous, stale=stale(current, cutoff), sourceIds=['F2', 'F3'],
                sourceUrl=f'https://fund.eastmoney.com/{code}.html')


def attempt(function, *args):
    try:
        return function(*args)
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        return None


def read_etfs(positions, cutoff):
    codes = list(dict.fromkeys([*ETF_BASKET, *(p['symbol'].split('.')[0] for p in positions[:20]
                        if p.get('assetType') == 'etf' and p['symbol'].endswith('.SH'))]))
    with ThreadPoolExecutor(max_workers=3) as pool:
        navs = dict(zip(codes, pool.map(lambda code: attempt(read_nav, code, cutoff), codes)))
    days = sorted(navs.get('510300') or {}, reverse=True)[:6]
    with ThreadPoolExecutor(max_workers=3) as pool:
        shares = dict(zip(days, pool.map(lambda day: attempt(read_shares, day) or {}, days)))
    rows = []
    for code in codes:
        row = attempt(estimate_etf, code, days, shares, navs.get(code) or {}, cutoff)
        if row:
            rows.append(row)
    return dict(status='ok' if len(rows) == len(codes) else 'partial' if rows else 'missing',
                date=days[0] if days else None, rows=sorted(rows, key=lambda row: decimal(row['net']), reverse=True),
                expectedCount=len(codes), note=ETF_NOTE)


def read_stock(position, cutoff):
    symbol = position['symbol']
    code, exchange = symbol.split('.')
    secid = ('1.' if exchange == 'SH' else '0.') + code
    data = fetch_json('https://push2.eastmoney.com/api/qt/ulist.np/get', dict(
        secids=secid, fltt=2, fields='f12,f14,f62,f164,f124'), 'https://data.eastmoney.com/')['data']['diff']
    if len(data) != 1 or data[0]['f12'] != code:
        raise ValueError('stock_flow_mismatch')
    item = data[0]
    stamp = datetime.fromtimestamp(int(item['f124']), CN)
    day = stamp.date().isoformat()
    if stamp > cutoff or not closed_day(day, cutoff) or stamp.hour < 15:
        raise ValueError('stock_flow_not_closed')
    net = str(decimal(item['f62']))
    try:
        net5 = str(decimal(item.get('f164')))
    except (ValueError, InvalidOperation):
        net5 = None
    info = attempt(fetch_json, 'https://push2.eastmoney.com/api/qt/stock/get', dict(secid=secid, fields='f57,f127'))
    industry = (info or {}).get('data') or {}
    row = dict(code=code, name=position['name'], date=day, net=net, net5=net5,
               stale=stale(day, cutoff), sourceIds=['F4'], sourceUrl=f'https://data.eastmoney.com/zjlx/{code}.html')
    return row, industry.get('f127') if industry.get('f57') == code else None


def collect_flows(positions, cutoff):
    industries = attempt(read_industries, cutoff) or dict(status='missing', date=None, rows=[], expectedCount=0, note=INDUSTRY_NOTE)
    etfs = attempt(read_etfs, positions, cutoff) or dict(status='missing', date=None, rows=[], expectedCount=len(ETF_BASKET), note=ETF_NOTE)
    industry_names = {row['name']: row['code'] for row in industries['rows']}
    etf_codes = {row['code']: row for row in etfs['rows']}
    def holding(position):
        symbol, kind = position['symbol'], position.get('assetType')
        row = dict(symbol=symbol, name=position['name'], kind=kind or 'unknown', flow=None, industryCode=None, note='')
        if symbol.endswith('.US'):
            row['note'] = '美股资金数据尚未接入；不使用A股统计替代。'
        elif kind == 'etf':
            row['flow'] = etf_codes.get(symbol.split('.')[0]) if symbol.endswith('.SH') else None
            row['note'] = 'ETF按净申赎估算，不套用发行人的行业。' if row['flow'] else 'ETF份额或净值不足；深市ETF暂未覆盖。'
        elif kind == 'stock' and re.fullmatch(r'\d{6}\.(SH|SZ)', symbol):
            result = attempt(read_stock, position, cutoff)
            if result:
                row['flow'], name = result
                row['industryCode'] = industry_names.get(name)
            row['note'] = '所属行业未核验。' if not row['industryCode'] else ''
            if not row['flow']:
                row['note'] = '个股资金流向本次获取失败。'
        else:
            row['note'] = '证券类型或市场未覆盖，暂不推断资金方向。'
        return row
    with ThreadPoolExecutor(max_workers=3) as pool:
        holdings = list(pool.map(holding, positions[:20]))
    limitations = ['首版资金模块覆盖A股行业、沪市ETF观察池和沪深股票持仓；美股、深市ETF净申赎暂未覆盖。']
    for label, group in [('行业', industries), ('ETF', etfs)]:
        if group['status'] != 'ok':
            limitations.append(f"{label}资金数据覆盖 {len(group['rows'])}/{group['expectedCount']} 项；缺失不代表零流入，排名仅针对已取得的数据。")
        if any(row['stale'] for row in group['rows']):
            limitations.append(f'{label}资金数据偏旧，仅供历史参考。')
    if len(positions) > 20:
        limitations.append('持仓关联仅覆盖前20只证券。')
    return dict(industry=industries, etf=etfs, holdings=holdings, limitations=limitations, fetchedAt=cutoff.isoformat())


def flow_sources(flows):
    stamp = flows['fetchedAt']
    return [dict(id='F1', title='东方财富行业主力资金统计', url=INDUSTRY_URL, publishedAt=flows['industry']['date'] or stamp),
            dict(id='F2', title='上交所ETF份额（万份）', url=SHARES_URL + '?sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L', publishedAt=flows['etf']['date'] or stamp),
            dict(id='F3', title='天天基金单位净值（各ETF详情见资金表）', url='https://fund.eastmoney.com/jzzzl.html', publishedAt=flows['etf']['date'] or stamp),
            dict(id='F4', title='东方财富个股主力资金统计', url='https://data.eastmoney.com/zjlx/list.html', publishedAt=stamp)]
