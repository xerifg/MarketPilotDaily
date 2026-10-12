"""A-share sector observations. Compare returns only over identical sessions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from jobs.fund_flows import attempt, closed_day, decimal, fetch_json, stale
from jobs.settings import SETTINGS
from jobs.calendar import trading_day

EASTMONEY = SETTINGS['sources']['eastmoney']
NOTE = '行业沿用东方财富行业榜分类；强势按当日上涨且主力流入筛选前三，走弱按当日下跌且主力流出筛选两项，均按涨跌幅排序。历史走势仅补采重点与持仓关联项。ETF按核验的跟踪标的单列，不等同于东方财富行业。'


def parse_history(raw, code, cutoff):
    if raw['code'] != code:
        raise ValueError('sector_history_identity_mismatch')
    rows = {}
    for line in raw['klines']:
        fields = line.split(',')
        if len(fields) < 3:
            raise ValueError('invalid_sector_history')
        day = fields[0]
        if not closed_day(day, cutoff) or trading_day(datetime.fromisoformat(day).date()) is False:
            continue
        close = decimal(fields[2])
        if day in rows or close <= 0:
            raise ValueError('invalid_sector_history')
        rows[day] = close
    return dict(sorted(rows.items()))


def read_history(secid, cutoff):
    if not secid.startswith('90.'):
        symbol = secid.split('.')[1] + ('.SH' if secid.startswith('1.') else '.SZ')
        data = fetch_json(SETTINGS['sources']['tickflow']['klinesUrl'],
                          dict(symbol=symbol, period='1d', count=32, adjust='none'))['data']
        if len(data['timestamp']) != len(data['close']):
            raise ValueError('inconsistent_sector_history')
        # Reuse the working quote provider for ETFs and the benchmark, independently of industry APIs.
        lines = [f"{datetime.fromtimestamp(stamp / 1000, ZoneInfo('Asia/Shanghai')).date()},0,{close}"
                 for stamp, close in zip(data['timestamp'], data['close'])]
        return parse_history(dict(code=symbol[:6], klines=lines), symbol[:6], cutoff)
    end = cutoff.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')
    data = fetch_json(EASTMONEY['klineApiUrl'], dict(secid=secid, klt=101, fqt=0, lmt=32,
                      end=cutoff.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d'), fields1='f1,f2,f3,f4,f5,f6',
                      fields2='f51,f52,f53,f54,f55,f56,f57', cb='marketpilot',
                      ut='b2884a393a59ad64002292a3e90d46a5', _=int(datetime.now(timezone.utc).timestamp() * 1000)), EASTMONEY['referer'])['data']
    return parse_history(data, secid.split('.')[1], cutoff)


def performance(history, benchmark):
    if len(history) < 2:
        return {}
    days = sorted(history)
    result = dict(date=days[-1], returns={}, relative={}, windows={})
    for period in (1, 5, 20):
        if len(days) <= period:
            continue
        window = days[-period - 1:]
        comparison_days = [day for day in sorted(benchmark) if window[0] <= day <= window[-1]]
        if benchmark and comparison_days != window:
            continue
        value = (history[window[-1]] / history[window[0]] - 1) * 100
        result['returns'][str(period)] = str(value.quantize(Decimal('.01')))
        result['windows'][str(period)] = dict(start=window[0], end=window[-1])
        # Endpoints alone cannot detect a suspended or missing session.
        if comparison_days == window:
            base = (benchmark[window[-1]] / benchmark[window[0]] - 1) * 100
            result['relative'][str(period)] = str((value - base).quantize(Decimal('.01')))
    return result


def read_reports(symbol, cutoff):
    code = symbol[:6]
    raw = fetch_json('https://api.fund.eastmoney.com/f10/JJGG', dict(
        fundcode=code, pageIndex=1, pageSize=5, type=3), f'https://fundf10.eastmoney.com/jjgg_{code}.html')
    if raw['ErrCode'] != 0:
        raise ValueError('fund_reports_unavailable')
    rows = []
    seen = set()
    for item in raw['Data']:
        if item['FUNDCODE'] != code or str(item['NEWCATEGORY']) != '3' or not re.fullmatch(r'AN\d+', item['ID']) or item['ID'] in seen:
            raise ValueError('invalid_fund_report_identity')
        seen.add(item['ID'])
        stamp = datetime.fromisoformat(item['PUBLISHDATE']).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        if stamp <= cutoff:
            rows.append(dict(title=item['TITLE'], publishedAt=stamp.isoformat(),
                             url=f"https://fund.eastmoney.com/gonggao/{code},{item['ID']}.html"))
    return sorted(rows, key=lambda row: row['publishedAt'], reverse=True)[:2]


def read_tracking(symbol, cutoff=None):
    code = symbol.split('.')[0]
    url = SETTINGS['sources']['fund']['detailUrl'].format(code=code)
    with urlopen(Request(url, headers={'User-Agent': 'MarketPilotDaily/0.1 (personal research)'}),
                 timeout=SETTINGS['requests']['flowTimeoutSeconds']) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('fund_profile_too_large')
    text = unescape(re.sub(r'<[^>]*>', ' ', raw.decode('utf-8')))
    match = re.search(r'跟踪标的\s*[：:]\s*([^|\r\n]+?)\s*(?:年化跟踪误差|\|)', text)
    if f'({code})' not in text or not match:
        raise ValueError('unverified_etf_tracking')
    target = ' '.join(match[1].split()).strip()
    if not 1 <= len(target) <= 80 or target in ('无', '暂无', '--'):
        raise ValueError('unverified_etf_tracking')
    result = dict(target=target, sourceUrl=url)
    error = re.search(r'年化跟踪误差\s*[：:]\s*([\d.]+)%', text)
    if error:
        result['trackingErrorPct'] = str(decimal(error[1]))
    profile_url = f'https://fundf10.eastmoney.com/jbgk_{code}.html'
    try:
        with urlopen(Request(profile_url, headers={'User-Agent': 'MarketPilotDaily/0.1 (personal research)'}),
                     timeout=SETTINGS['requests']['flowTimeoutSeconds']) as response:
            raw = response.read(2000001)
        if len(raw) > 2000000:
            raise ValueError('fund_profile_too_large')
        profile = unescape(re.sub(r'<[^>]*>', ' ', raw.decode('utf-8')))
        if code not in re.sub(r'\s', '', profile[:2500]):
            raise ValueError('fund_profile_identity_mismatch')
        for label, key in [('管理费率', 'managementFeePct'), ('托管费率', 'custodyFeePct')]:
            match = re.search(label + r'\s+([\d.]+)%', profile)
            if match:
                result[key] = str(decimal(match[1]))
        result['profileUrl'] = profile_url
        result['reportsUrl'] = f'https://fundf10.eastmoney.com/jjgg_{code}.html'
    except (OSError, ValueError):
        result['profileMissing'] = True
    result['reports'] = attempt(read_reports, symbol, cutoff or datetime.now(timezone.utc)) or []
    return result


def collect_sectors(positions, flows, cutoff):
    rows = []
    for row in flows['industry']['rows']:
        rows.append({**row, 'kind': 'industry', 'sourceIds': ['S1', 'F1', 'S2'],
                     'sourceUrl': EASTMONEY['sectorQuoteUrl'].format(code=row['code']),
                     'returns': {}, 'relative': {}, 'windows': {}, 'reasons': [], 'holdings': []})
    by_code = {row['code']: row for row in rows}
    holdings = []
    for holding in flows['holdings']:
        code = holding['industryCode']
        mapped = code in by_code
        holdings.append(dict(symbol=holding['symbol'], name=holding['name'], code=code if mapped else None,
                             note='同源行业名称已核验。' if mapped else '所属行业或ETF跟踪标的尚未核验；不推断板块。'))
        if mapped:
            by_code[code]['holdings'].append(holding['symbol'])
            by_code[code]['reasons'] = ['持仓关联']
    available = [row for row in rows if not row['stale'] and row.get('changePct') is not None]
    for positive, limit, label in [(True, 3, '当日上涨且主力流入'), (False, 2, '当日下跌且主力流出')]:
        ranked = [row for row in available if (decimal(row['changePct']) > 0 and decimal(row['net']) > 0
                  if positive else decimal(row['changePct']) < 0 and decimal(row['net']) < 0)]
        for row in sorted(ranked, key=lambda row: decimal(row['changePct']), reverse=positive)[:limit]:
            row['reasons'].append(label)
    selected = [row for row in rows if row['reasons']]
    etfs = [position for position in positions[:SETTINGS['collection']['maxPositions']]
            if position.get('assetType') == 'etf' and re.fullmatch(r'\d{6}\.(SH|SZ)', position['symbol'])]
    benchmark = (attempt(read_history, '1.000300', cutoff) or {}) if selected or etfs else {}
    with ThreadPoolExecutor(max_workers=SETTINGS['collection']['concurrency']) as pool:
        histories = list(pool.map(lambda row: attempt(read_history, '90.' + row['code'], cutoff) or {}, selected))
    for row, history in zip(selected, histories):
        stats = performance(history, benchmark)
        if stats.get('date') == row['date']:
            row.update({key: stats[key] for key in ('returns', 'relative', 'windows')})
    # Read the tracked object from a fund profile, not from the fund manager's industry.
    def etf_row(position):
        symbol = position['symbol']
        tracking = attempt(read_tracking, symbol, cutoff)
        verified = bool(tracking)
        tracking = tracking or dict(target='未核验', sourceUrl=SETTINGS['sources']['fund']['detailUrl'].format(code=symbol[:6]))
        secid = ('1.' if symbol.endswith('.SH') else '0.') + symbol.split('.')[0]
        history = attempt(read_history, secid, cutoff) or {}
        stats = performance(history, benchmark)
        flow = next((holding['flow'] for holding in flows['holdings'] if holding['symbol'] == symbol), None)
        day = stats.get('date')
        return dict(code=symbol, name=position['name'], kind='etf', tracking=tracking['target'],
                    trackingVerified=verified, fundamentals={key: tracking.get(key) for key in
                        ('managementFeePct', 'custodyFeePct', 'trackingErrorPct', 'profileUrl', 'reportsUrl', 'reports')},
                    trackingUrl=tracking['sourceUrl'], date=day, stale=stale(day, cutoff) if day else True,
                    changePct=stats.get('returns', {}).get('1'), amount=None, advancers=None, decliners=None,
                    returns=stats.get('returns', {}), relative=stats.get('relative', {}), windows=stats.get('windows', {}),
                    net=flow['net'] if flow else None, net5=flow['net5'] if flow else None,
                    flowDate=flow['date'] if flow else None, flowStale=flow['stale'] if flow else True,
                    sourceIds=['S1', 'S2', 'S3', *(['F2', 'F3'] if flow else [])],
                    sourceUrl=SETTINGS['sources']['tickflow']['klinesUrl'] + '?' + urlencode(
                        dict(symbol=symbol, period='1d', count=32, adjust='none')),
                    reasons=['持仓ETF跟踪标的已核验' if verified else '持仓ETF价格观察；跟踪标的未核验'], holdings=[symbol])
    with ThreadPoolExecutor(max_workers=SETTINGS['collection']['concurrency']) as pool:
        tracked = list(pool.map(etf_row, etfs))
    for row in tracked:
        if row:
            rows.append(row)
            selected.append(row)
            for holding in holdings:
                if holding['symbol'] == row['code'] and row['trackingVerified']:
                    holding.update(code=row['code'], note=f"跟踪 {row['tracking']}；按ETF价格观察，不视为单一行业指数。")
    limitations = ['板块历史涨跌为不复权价格变化；ETF分红与拆分可能影响，不代表含分红总回报。',
                   '相对强弱为同一交易窗口收益率之差（百分点）；日期或交易序列不一致时不计算。',
                   '上涨／下跌家数仅描述当天分布；未包含平盘及停牌统计，不能据此断言少数权重股拉动。']
    if not available:
        limitations.append('本期行业行情缺失或偏旧，无法筛选当前强弱板块。')
    if not benchmark:
        limitations.append('沪深300历史行情获取失败，相对强弱未计算。')
    if any(len(row['returns']) < 3 for row in selected):
        limitations.append('部分重点板块历史行情不足或与快照日期不一致，缺少的周期不作趋势判断。')
    if any(holding['code'] is None for holding in holdings):
        limitations.append('部分持仓所属行业或ETF跟踪标的未核验，详见持仓关联表。')
    selected.sort(key=lambda row: not bool(row['holdings']))
    return dict(rows=rows, selectedCodes=[row['code'] for row in selected],
                detailCodes=[row['code'] for row in selected if not row['stale']][:5], holdings=holdings, note=NOTE,
                expectedCount=flows['industry'].get('expectedCount', 0), date=flows['industry'].get('date'),
                benchmarkDate=max(benchmark, default=None), limitations=limitations, fetchedAt=cutoff.isoformat())


def sector_sources(sectors):
    stamp = sectors['date'] or sectors['fetchedAt']
    sources = [dict(id='S1', title='行业（东方财富）与ETF（TickFlow）行情（各项附来源）',
                    url='https://quote.eastmoney.com/center/boardlist.html', publishedAt=stamp),
               dict(id='S2', title='沪深300比较基准（TickFlow）', url=SETTINGS['sources']['tickflow']['klinesUrl'] + '?' + urlencode(
                   dict(symbol='000300.SH', period='1d', count=32, adjust='none')),
                    publishedAt=sectors['benchmarkDate'] or sectors['fetchedAt'])]
    if any(row['kind'] == 'etf' for row in sectors['rows']):
        sources.append(dict(id='S3', title='天天基金ETF跟踪标的、费用与定期报告入口（各项附详情页）',
                            url='https://fund.eastmoney.com/', publishedAt=sectors['fetchedAt']))
    return sources


def sector_evidence(sectors):
    if not sectors:
        return None
    # Prioritize held sectors; send only selected observations, not the whole universe.
    rows = sorted([row for row in sectors['rows'] if row['code'] in sectors['selectedCodes']],
                  key=lambda row: not bool(row['holdings']))
    return {key: sectors[key] for key in ('note', 'date', 'benchmarkDate', 'detailCodes', 'holdings', 'limitations')} | {'rows': rows}
