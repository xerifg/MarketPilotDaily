"""Shared sector validation and plain-text views for full reports and email."""
import re
from jobs.flow_presentation import amount


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
    if row.get('flowStale') or row.get('flowDate') and row['flowDate'] != row['date']:
        text += '｜资金偏旧或与行情日期不一致，需分别核对'
    if row['stale']:
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
    snapshots = [f"{row['name']}（{row['code']}）｜{row['date'] or '日期未知'}｜当日 {row.get('changePct') if row.get('changePct') is not None else '未取得'}%｜近5日 {row['returns'].get('5', '未取得')}%｜近20日 {row['returns'].get('20', '未取得')}%"
                 + ('｜关注：' + '；'.join(row['reasons']) if row['reasons'] else '')
                 + ('｜数据偏旧' if row['stale'] else '') + '[S1]' for row in sectors['rows']]
    for start in range(0, len(snapshots), 25):
        sections.append(('板块快照 · 历史走势仅补采重点与持仓关联项' + ('（续）' if start else ''), snapshots[start:start + 25]))
    sections.append(('板块与持仓关联', [f"{holding['name']}（{holding['symbol']}）：{holding['code'] or '未关联'}；{holding['note']}"
                     for holding in sectors['holdings']] or ['尚无持仓，仅提供市场观察。']))
    sections.append(('板块数据限制', sectors['limitations']))
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
