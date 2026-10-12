"""Public A-share order-flow statistics and estimated Shanghai ETF subscriptions."""
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import re
import sys
import time
from http.client import IncompleteRead
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from pathlib import Path
from zoneinfo import ZoneInfo
from jobs.settings import SETTINGS
from jobs.calendar import session_lag, trading_day

CN = ZoneInfo('Asia/Shanghai')
SOURCES = SETTINGS['sources']
INDUSTRY_URL = SOURCES['eastmoney']['industryUrl']
SHARES_URL = SOURCES['sse']['sharesUrl']
NAV_URL = SOURCES['fund']['navUrl']
# A representative observation basket, never presented as the entire ETF market.
ETF_BASKET = SETTINGS['watchlist']['etfs']
INDUSTRY_NOTE = '东方财富行业榜口径：主力净流入为大单与超大单交易统计，不代表全市场新增资金；行业与个股不可相加。'
ETF_NOTE = '沪市代表ETF及已持有的沪深ETF，非全市场排名。估算净申赎＝份额变化×前一交易日单位净值；实物申赎不等于现金进出。'


def fetch_json(url, params=None, referer=None):
    headers = {'User-Agent': 'MarketPilotDaily/0.1 (personal research)'}
    if referer:
        headers['Referer'] = referer
    target = url + ('?' + urlencode(params) if params else '')
    for retry in range(3):
        try:
            with urlopen(Request(target, headers=headers), timeout=SETTINGS['requests']['flowTimeoutSeconds']) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('flow_source_too_large')
                if params and params.get('cb') == 'marketpilot':
                    text = raw.decode('utf-8').strip()
                    wrapped = re.fullmatch(r'marketpilot\((.*)\);?', text, re.S)
                    raw = wrapped[1] if wrapped else text
                return json.loads(raw, parse_float=Decimal)
        except (OSError, IncompleteRead) as error:
            if retry == 2 or isinstance(error, HTTPError) and error.code not in (408, 429, 500, 502, 503, 504):
                raise
            time.sleep(2 ** retry)


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
    lag = session_lag(value, cutoff)
    return (lag if lag is not None else (cutoff.astimezone(CN).date() - date.fromisoformat(value)).days) > SETTINGS['collection']['staleAfterDays']


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
        optional = {}
        for field, key in [('f3', 'changePct'), ('f6', 'amount'), ('f104', 'advancers'), ('f105', 'decliners')]:
            try:
                value = decimal(item.get(field))
                if key != 'changePct' and (value < 0 or key in ('advancers', 'decliners') and value != int(value)):
                    raise ValueError('invalid_sector_statistic')
                optional[key] = str(value) if key in ('changePct', 'amount') else int(value)
            except (ValueError, InvalidOperation):
                optional[key] = None
        rows.append(dict(code=code, name=name, date=day, net=net, net5=net5, stale=stale(day, cutoff), **optional,
                         sourceIds=['F1'], sourceUrl=SOURCES['eastmoney']['industryDetailUrl'].format(code=code)))
    # Never compare rankings from different sessions.
    latest = max((row['date'] for row in rows), default=None)
    return sorted([row for row in rows if row['date'] == latest], key=lambda row: decimal(row['net']), reverse=True)


def read_industries(cutoff):
    raw, total = [], None
    for page in range(1, 7):
        data = fetch_json(SOURCES['eastmoney']['industryApiUrl'], dict(
            pn=page, pz=100, po=1, np=1, fltt=2, invt=2, fid='f62', fs='m:90 s:4',
            ut='8dec03ba335b81bf4ebdf7b29ec27d15', cb='marketpilot', _=int(time.time() * 1000),
            fields='f12,f14,f3,f6,f104,f105,f62,f164,f124'), INDUSTRY_URL)['data']
        if total is not None and total != data['total']:
            raise ValueError('industry_universe_changed')
        total = data['total']
        raw.extend(data['diff'])
        if len(raw) >= total:
            break
    if not raw or len(raw) != total:
        raise ValueError('incomplete_industry_pages')
    rows = parse_industries(raw, cutoff)
    historical = not rows
    if historical:
        # After the market opens, the quote snapshot becomes intraday. Recover the previous close from daily history.
        rows = read_industry_history(raw, cutoff)
    group = dict(status='ok' if len(rows) == total else 'partial' if rows else 'missing',
                 date=rows[0]['date'] if rows else None, rows=rows, expectedCount=total, note=INDUSTRY_NOTE)
    if historical and rows:
        group['note'] += ' 盘中／晚于截止时点的快照已排除；本期按行业日终资金历史重建，涨跌、上下家数与成交额未回填。'
        group['historyFallback'] = True
    elif not rows:
        group['note'] += ' 本次返回的快照晚于截止时点或尚未收盘，不能作为已收盘行业榜；未用盘中数据填补日终数据。'
    if group['status'] == 'ok':
        path = industry_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(dict(apiUrl=SOURCES['eastmoney']['industryApiUrl'], total=total, rows=rows,
                                           fetchedAt=cutoff.isoformat()), ensure_ascii=False, default=str), encoding='utf-8')
        except OSError:
            print('source_cache_write_failed=industry', file=sys.stderr)
    return group


def read_industry_history(items, cutoff):
    with ThreadPoolExecutor(max_workers=SETTINGS['collection']['concurrency']) as pool:
        tasks = [pool.submit(attempt, read_industry_close, item, cutoff) for item in items]
        completed, pending = wait(tasks, timeout=120)
        for task in pending:
            task.cancel()
        rows = [row for task in completed if (row := task.result())]
    latest = max((row['date'] for row in rows), default=None)
    return sorted([row for row in rows if row['date'] == latest], key=lambda row: decimal(row['net']), reverse=True)


def refresh_cached_industries(group, cutoff):
    if not any(row['stale'] for row in group['rows']):
        return group
    items = [dict(f12=row['code'], f14=row['name']) for row in group['rows']]
    rows = read_industry_history(items, cutoff)
    if not rows or rows[0]['date'] <= group['date']:
        return group
    # The daily values are verified, but a cached classification cannot prove the current full universe.
    return dict(status='partial', date=rows[0]['date'], rows=rows, expectedCount=group['expectedCount'],
                historyFallback=True, cachedUniverse=True, universeDate=group['date'],
                note=INDUSTRY_NOTE + f" 行业目录沿用{group['date']}公开快照，各项资金按日终历史重新核验；当前行业分类完整性未核验。涨跌、上下家数与成交额未回填。")


def read_industry_close(item, cutoff):
    code = item['f12']
    data = fetch_json(SOURCES['eastmoney']['flowHistoryApiUrl'], dict(
        secid='90.' + code, lmt=8, klt=101, fields1='f1,f2,f3,f7', fields2='f51,f52,f53,f54,f55,f56',
        cb='marketpilot', ut='b2884a393a59ad64002292a3e90d46a5'), INDUSTRY_URL)['data']
    if data['code'] != code or data['name'] != item['f14'] or data['market'] != 90:
        raise ValueError('industry_flow_history_identity_mismatch')
    flows = {}
    for line in data['klines']:
        values = line.split(',')
        if len(values) < 6:
            raise ValueError('invalid_industry_flow_history')
        day = values[0]
        if not closed_day(day, cutoff):
            continue
        if trading_day(date.fromisoformat(day)) is not True:
            raise ValueError('invalid_industry_flow_session')
        if day in flows:
            raise ValueError('duplicate_industry_flow_date')
        flows[day] = decimal(values[1])
    if not flows:
        raise ValueError('missing_industry_close')
    day = max(flows)
    lag = session_lag(day, cutoff)
    window = sorted(flows, reverse=True)[:5]
    consecutive = lag is not None and [session_lag(value, cutoff) for value in window] == list(range(lag, lag + 5))
    net5 = str(sum(flows[value] for value in window)) if len(window) == 5 and consecutive else None
    return dict(code=code, name=data['name'], date=day, net=str(flows[day]), net5=net5,
                changePct=None,
                amount=None, advancers=None, decliners=None, stale=stale(day, cutoff) or lag is None or lag > 0,
                sourceIds=['F1'], sourceUrl=SOURCES['eastmoney']['industryDetailUrl'].format(code=code))


def industry_cache_path():
    # Only the complete public industry universe is cached; no portfolio or holding-specific queries.
    return Path(__file__).resolve().parents[1] / 'local-data/public-industry.json'


def read_cached_industries(cutoff):
    path = industry_cache_path()
    if not path.exists() or path.stat().st_size > 2000000:
        return None
    saved = json.loads(path.read_text(encoding='utf-8'))
    if saved['apiUrl'] != SOURCES['eastmoney']['industryApiUrl'] or len(saved['rows']) != saved['total']:
        raise ValueError('industry_cache_mismatch')
    rows, seen = [], set()
    for item in saved['rows']:
        code, day = item['code'], item['date']
        if not re.fullmatch(r'BK\d+', code) or code in seen or not isinstance(item['name'], str) or not item['name'].strip():
            raise ValueError('invalid_industry_cache_identity')
        seen.add(code)
        if not closed_day(day, cutoff):
            continue
        net = str(decimal(item['net']))
        net5 = str(decimal(item['net5'])) if item.get('net5') is not None else None
        rows.append({**item, 'net': net, 'net5': net5, 'sourceIds': ['F1'],
                     'sourceUrl': SOURCES['eastmoney']['industryDetailUrl'].format(code=code)})
    if not rows:
        return None
    for row in rows:
        lag = session_lag(row['date'], cutoff)
        row['stale'] = row['stale'] or lag is None or lag > 0
    latest = max(row['date'] for row in rows)
    rows = sorted([row for row in rows if row['date'] == latest], key=lambda row: decimal(row['net']), reverse=True)
    return dict(status='partial', date=rows[0]['date'], rows=rows, expectedCount=saved['total'],
                note=INDUSTRY_NOTE + f" 本次未取得截至截止时点的已收盘榜，展示最近成功快照（采集于{saved['fetchedAt']}）；日期早于最近已收盘交易日时仅供历史参考。",
                cached=True, cachedAt=saved['fetchedAt'])


def read_nav(code, cutoff):
    result = fetch_json(NAV_URL, dict(fundCode=code, pageIndex=1, pageSize=12,
                                    startDate='', endDate=cutoff.astimezone(CN).date().isoformat()),
                        SOURCES['fund']['referer'])
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
                                    'pageHelp.pageSize': 10000, 'pageHelp.pageNo': 1}, SOURCES['sse']['referer'])
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


def read_sz_shares(code, days):
    result = fetch_json(SOURCES['szse']['reportUrl'], dict(
        SHOWTYPE='JSON', CATALOGID='scsj_fund_jjgm', jjlb='ETF', txtDm=code,
        txtStart=min(days), txtEnd=max(days), tab1PAGENO=1), SOURCES['szse']['referer'])[0]
    if result['metadata']['catalogid'] != 'scsj_fund_jjgm' or len(result['data']) != int(result['metadata']['recordcount']):
        raise ValueError('incomplete_sz_etf_shares')
    if '万份' not in result['metadata']['cols']['current_size']:
        raise ValueError('unknown_sz_share_unit')
    rows = {}
    for item in result['data']:
        day = item['size_date']
        if item['fund_code'] != code or day in rows or not min(days) <= day <= max(days):
            raise ValueError('invalid_sz_share_identity_or_date')
        shares = decimal(item['current_size'].replace(',', '')) * 10000
        if shares < 0:
            raise ValueError('invalid_shares')
        rows[day] = dict(shares=shares, name=item['security_short_name'])
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
                sourceUrl=SOURCES['fund']['detailUrl'].format(code=code))


def attempt(function, *args):
    try:
        return function(*args)
    except (OSError, IncompleteRead, ValueError, TypeError, KeyError, InvalidOperation, OverflowError) as error:
        # Public Actions logs must never contain request parameters, holdings or response bodies.
        code = f'http_{error.code}' if isinstance(error, HTTPError) else type(error).__name__
        name = getattr(function, '__name__', 'source')
        if name != 'estimate_etf':
            print(f'source_failure={name} error={code}', file=sys.stderr)
        return None


def read_etfs(positions, cutoff):
    codes = list(dict.fromkeys([*ETF_BASKET, *(p['symbol'].split('.')[0] for p in positions[:SETTINGS['collection']['maxPositions']]
                        if p.get('assetType') == 'etf' and re.fullmatch(r'\d{6}\.(SH|SZ)', p['symbol']))]))
    sz_codes = {p['symbol'][:6] for p in positions[:SETTINGS['collection']['maxPositions']]
                if p.get('assetType') == 'etf' and p['symbol'].endswith('.SZ')}
    with ThreadPoolExecutor(max_workers=SETTINGS['collection']['concurrency']) as pool:
        navs = dict(zip(codes, pool.map(lambda code: attempt(read_nav, code, cutoff), codes)))
    days = sorted(navs.get(SETTINGS['watchlist']['etfReferenceCode']) or {}, reverse=True)[:8]
    with ThreadPoolExecutor(max_workers=SETTINGS['collection']['concurrency']) as pool:
        shares = dict(zip(days, pool.map(lambda day: attempt(read_shares, day) or {}, days)))
    if days:
        with ThreadPoolExecutor(max_workers=SETTINGS['collection']['concurrency']) as pool:
            sz_rows = dict(zip(sorted(sz_codes), pool.map(lambda code: attempt(read_sz_shares, code, days) or {}, sorted(sz_codes))))
        for code, history in sz_rows.items():
            for day in days:
                if day in history:
                    shares[day][code] = history[day]
    rows = []
    selected_days = days
    for offset in range(max(0, len(days) - 1)):
        selected_days = days[offset:offset + 6]
        current, previous = selected_days[:2]
        if not any(code in shares[current] and code in shares[previous]
                   and current in (navs.get(code) or {}) and previous in (navs.get(code) or {}) for code in codes):
            continue
        for code in codes:
            row = attempt(estimate_etf, code, selected_days, shares, navs.get(code) or {}, cutoff)
            if row:
                row['shareSourceUrl'] = SOURCES['szse']['sharesUrl'] if code in sz_codes else SOURCES['sse']['referer'] + 'market/funddata/volumn/etfvolumn/'
                rows.append(row)
        break  # Do not evade corporate-action checks by selecting an older estimate.
    note = ETF_NOTE
    if rows and selected_days[0] != days[0]:
        note += f' 最新净值日{days[0]}的份额或净值未配齐，采用最近可核验的相邻交易日{selected_days[1]}至{selected_days[0]}。'
    return dict(status='ok' if len(rows) == len(codes) else 'partial' if rows else 'missing',
                date=rows[0]['date'] if rows else days[0] if days else None,
                rows=sorted(rows, key=lambda row: decimal(row['net']), reverse=True),
                expectedCount=len(codes), note=note)


def read_stock(position, cutoff):
    symbol = position['symbol']
    code, exchange = symbol.split('.')
    secid = ('1.' if exchange == 'SH' else '0.') + code
    data = fetch_json(SOURCES['eastmoney']['stockFlowApiUrl'], dict(
        secids=secid, fltt=2, fields='f12,f14,f62,f164,f124'), SOURCES['eastmoney']['referer'])['data']['diff']
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
    info = attempt(fetch_json, SOURCES['eastmoney']['stockInfoApiUrl'], dict(secid=secid, fields='f57,f127'))
    industry = (info or {}).get('data') or {}
    row = dict(code=code, name=position['name'], date=day, net=net, net5=net5,
               stale=stale(day, cutoff), sourceIds=['F4'], sourceUrl=SOURCES['eastmoney']['stockDetailUrl'].format(code=code))
    return row, industry.get('f127') if industry.get('f57') == code else None


def collect_flows(positions, cutoff):
    industries = attempt(read_industries, cutoff)
    if not industries or not industries['rows']:
        cached = attempt(read_cached_industries, cutoff)
        industries = (attempt(refresh_cached_industries, cached, cutoff) or cached) if cached else industries
    industries = industries or dict(status='missing', date=None, rows=[], expectedCount=None, note=INDUSTRY_NOTE)
    etfs = attempt(read_etfs, positions, cutoff) or dict(status='missing', date=None, rows=[], expectedCount=len(ETF_BASKET), note=ETF_NOTE)
    industry_names = {row['name']: row['code'] for row in industries['rows']}
    etf_codes = {row['code']: row for row in etfs['rows']}
    def holding(position):
        symbol, kind = position['symbol'], position.get('assetType')
        row = dict(symbol=symbol, name=position['name'], kind=kind or 'unknown', flow=None, industryCode=None, note='')
        if symbol.endswith('.US'):
            row['note'] = '美股资金数据尚未接入；不使用A股统计替代。'
        elif kind == 'etf':
            row['flow'] = etf_codes.get(symbol.split('.')[0]) if re.fullmatch(r'\d{6}\.(SH|SZ)', symbol) else None
            row['note'] = 'ETF按净申赎估算，不套用发行人的行业。' if row['flow'] else 'ETF份额或净值本次未配齐。'
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
    with ThreadPoolExecutor(max_workers=SETTINGS['collection']['concurrency']) as pool:
        holdings = list(pool.map(holding, positions[:SETTINGS['collection']['maxPositions']]))
    limitations = ['资金模块覆盖A股行业、沪市ETF观察池、已持有的沪深ETF和沪深股票；美股资金数据尚未取得可核验来源。']
    for label, group in [('行业', industries), ('ETF', etfs)]:
        if group['status'] != 'ok':
            expected = group['expectedCount'] if group['expectedCount'] is not None else '总数未知'
            limitations.append(f"{label}资金数据覆盖 {len(group['rows'])}/{expected} 项；缺失不代表零流入，排名仅针对已取得的数据。")
        if any(row['stale'] for row in group['rows']):
            limitations.append(f'{label}资金数据偏旧，仅供历史参考。')
    if len(positions) > SETTINGS['collection']['maxPositions']:
        limitations.append(f"持仓关联仅覆盖前{SETTINGS['collection']['maxPositions']}只证券。")
    return dict(industry=industries, etf=etfs, holdings=holdings, limitations=limitations, fetchedAt=cutoff.isoformat())


def flow_sources(flows):
    stamp = flows['fetchedAt']
    return [dict(id='F1', title='东方财富行业主力资金统计', url=INDUSTRY_URL, publishedAt=flows['industry']['date'] or stamp),
            dict(id='F2', title='沪深交易所ETF份额（万份，各项附来源）', url=SHARES_URL + '?sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L', publishedAt=flows['etf']['date'] or stamp),
            dict(id='F3', title='天天基金单位净值（各ETF详情见资金表）', url=SOURCES['fund']['sourceUrl'], publishedAt=flows['etf']['date'] or stamp),
            dict(id='F4', title='东方财富个股主力资金统计', url=SOURCES['eastmoney']['stockSourceUrl'], publishedAt=stamp)]
