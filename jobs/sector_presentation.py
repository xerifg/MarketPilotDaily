"""Shared sector validation and plain-text views for full reports and email."""
import re
from jobs.flow_presentation import amount


def deterministic_sector_analysis(sectors):
    """A factual explanation remains available when an optional model section is invalid."""
    rows = {row['code']: row for row in sectors['rows']}
    result = []
    for code in sectors['detailCodes']:
        row = rows[code]
        cited = ''.join(f'[{source}]' for source in row['sourceIds'])
        returns = '、'.join(f"近{period}日{row['returns'][str(period)]}%" for period in (1, 5, 20) if str(period) in row['returns']) or '历史走势未取得'
        flow = f"{row.get('flowDate') or row['date']}净额{amount(row['net'])}" if row.get('net') is not None else '资金未取得'
        text = (f'走势与资金：{row.get("date") or "日期未知"}，{returns}；{flow}。{cited}\n'
                '驱动依据与反证：未核验直接驱动，不从单日涨跌推断原因。\n'
                '持续性与风险：核对比较窗口与资金日期；数据不齐时不能确认持续趋势。\n'
                '持仓影响：用于复核关联方向，不据此设置买卖数量或仓位目标。\n'
                '观察与复查条件：下一次完整收盘及资金数据发布后复查。')
        digest = '、'.join(f"近{period}日{row['returns'][str(period)]}%" for period in (5, 20) if str(period) in row['returns']) or '历史未取得'
        result.append(dict(code=code, text=text, email=f'{digest}；下次收盘及资金更新后复查。[{row["sourceIds"][0]}]'))
    return result


def validate_sector_analysis(value, sectors, sources):
    codes = sectors['detailCodes']
    if not isinstance(value, list) or len(value) != len(codes):
        raise ValueError('invalid_sector_analysis')
    known = {source['id'] for source in sources}
    by_code = {}
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'code', 'text', 'email'}
                or not isinstance(row['code'], str) or row['code'] not in codes or row['code'] in by_code):
            raise ValueError('invalid_sector_analysis')
        for key, limit in [('text', 350), ('email', 100)]:
            text = row[key]
            if not isinstance(text, str) or not text.strip() or len(text) > limit:
                raise ValueError('invalid_sector_analysis')
            cited = set(re.findall(r'\[([A-Za-z]+\d+)\]', text))
            if not cited or not cited.issubset(known):
                raise ValueError('unknown_sector_evidence')
        by_code[row['code']] = row
    return [by_code[code] for code in codes]


def sector_line(row):
    def pct(value):
        return (f'{value}%' if value is not None else '未取得')
    periods = row['returns']
    text = f"{row['name']}（{row['code']}）｜{row['date'] or '日期未知'}｜当日 {pct(row.get('changePct'))}｜近5日 {pct(periods.get('5'))}｜近20日 {pct(periods.get('20'))}"
    text += '｜相对沪深300（百分点）：' + '、'.join(f"{period}日 {row['relative'].get(str(period), '未计算')}" for period in (1, 5, 20))
    if row['kind'] == 'etf':
        text += f"｜跟踪 {row['tracking']}｜ETF估算净申赎 {amount(row['net'])}（{row.get('flowDate') or '日期未知'}）"
    else:
        text += f"｜主力当日 {amount(row['net'])}、近5日 {amount(row['net5'])}｜上涨／下跌 {row.get('advancers') if row.get('advancers') is not None else '未知'}/{row.get('decliners') if row.get('decliners') is not None else '未知'}家"
    if row.get('flowDate') and (row.get('flowStale') or row['flowDate'] != row['date']):
        text += '｜资金偏旧或与行情日期不一致，需分别核对'
    if not row['date']:
        text += '｜历史行情未取得'
    elif row['stale']:
        text += '｜数据偏旧，仅供历史参考'
    return text + ''.join(f'[{source}]' for source in row['sourceIds'])


def full_sector_sections(evidence):
    sectors = evidence.get('sectors')
    if not sectors:
        return []
    sections = [('板块分析 · 走势与持仓影响', [sectors['note']])]
    analysis = {row['code']: row['text'] for row in evidence.get('sectorAnalysis', [])}
    rows = {row['code']: row for row in sectors['rows']}
    for code in sectors['detailCodes']:
        row = rows[code]
        sections.append((row['name'] + ' · 板块观察', [sector_line(row),
                         '关注原因：' + '；'.join(row['reasons']),
                         analysis.get(row['code'], '本项未生成可用的板块解读，仅展示已核验数据。')]))
    if not sectors['selectedCodes']:
        sections.append(('板块状态', ['本期无可用的重点板块数据。']))
    def pct(value):
        return f'{value}%' if value is not None else '未取得'
    snapshots = [f"{row['name']}（{row['code']}）｜{row['date'] or '日期未知'}｜当日 {pct(row.get('changePct'))}｜近5日 {pct(row['returns'].get('5'))}｜近20日 {pct(row['returns'].get('20'))}"
                 + ('｜关注：' + '；'.join(row['reasons']) if row['reasons'] else '')
                 + ('｜历史行情未取得' if not row['date'] else '｜数据偏旧' if row['stale'] else '') + '[S1]' for row in sectors['rows']]
    for start in range(0, len(snapshots), 25):
        sections.append(('板块快照 · 历史走势仅补采重点与持仓关联项' + ('（续）' if start else ''), snapshots[start:start + 25]))
    sections.append(('板块与持仓关联', [f"{holding['name']}（{holding['symbol']}）：{holding['code'] or '未关联'}；{holding['note']}"
                     for holding in sectors['holdings']] or ['尚无持仓，仅提供市场观察。']))
    sections.append(('板块数据限制', sectors['limitations']))
    profiles = []
    for row in sectors['rows']:
        if row['kind'] != 'etf' or not row.get('fundamentals'):
            continue
        facts = row['fundamentals']
        def pct(key):
            return f'{facts[key]}%' if facts.get(key) is not None else '未取得'
        profiles.append(f"{row['name']}｜管理费率{pct('managementFeePct')}/年；托管费率{pct('custodyFeePct')}/年；年化历史跟踪误差{pct('trackingErrorPct')}。[S3]"
                        + (f"\n基金资料：{facts['profileUrl']}；定期报告：{facts['reportsUrl']}" if facts.get('profileUrl') else ''))
        for report in facts.get('reports') or []:
            profiles[-1] += f"\n已核验公告：{report['publishedAt'][:10]}｜{report['title']}｜{report['url']}[S3]"
    if profiles:
        sections.append(('ETF基本资料与报告入口', profiles + ['费用与跟踪误差为采集时的资料快照；定期报告正文及成分公司财报未自动提取，不以基金管理人财报代替。']))
    return sections


def sector_email_lines(evidence):
    if not evidence.get('sectors'):
        return []
    rows = evidence.get('sectorAnalysis', [])
    names = {row['code']: row['name'] for row in evidence['sectors']['rows']}
    if rows:
        # The validated order prioritizes held sectors; never truncate conditions.
        lines = []
        for row in rows[:3]:
            line = names[row['code']] + '：' + row['email']
            if sum(map(len, lines)) + len(line) <= 300:
                lines.append(line)
        return lines
    return ['本期未生成可用的板块解读，走势、持仓关联与数据缺口请查看完整日报。']
