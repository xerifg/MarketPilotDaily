"""Deterministic flow rankings shared by plain-text reports and email summaries."""
from decimal import Decimal


def amount(value):
    if value is None:
        return '未提供'
    n = Decimal(value)
    divisor, unit = (Decimal('1e8'), '亿元') if abs(n) >= Decimal('1e8') else (Decimal('1e4'), '万元')
    return f'{n / divisor:+,.2f}{unit}'


def citations(row):
    return ''.join(f'[{source}]' for source in row['sourceIds'])


def leaders(rows, positive):
    return sorted([row for row in rows if not row['stale'] and (Decimal(row['net']) > 0 if positive else Decimal(row['net']) < 0)],
                  key=lambda row: Decimal(row['net']), reverse=positive)[:3]


def holding_signals(flows):
    signals = []
    for holding in flows['holdings']:
        row = holding['flow']
        if row and not row['stale'] and row['net5'] is not None and Decimal(row['net']) * Decimal(row['net5']) < 0:
            signals.append((holding, row))
    return sorted(signals, key=lambda pair: abs(Decimal(pair[1]['net'])), reverse=True)[:2]


def flow_highlights(flows):
    if not flows:
        return []
    lines = []
    for key, title, incoming, outgoing in [('industry', '行业主力', '流入', '流出'), ('etf', '沪市ETF观察池·估算', '净申购', '净赎回')]:
        group = flows[key]
        rows = [row for row in group['rows'] if not row['stale']]
        if not rows:
            lines.append(f'{title}：数据缺失或偏旧，本期不作排名。')
            continue
        parts = []
        for positive, label in [(True, incoming), (False, outgoing)]:
            ranked = leaders(rows, positive)
            parts.append(label + '前三：' + ('、'.join(f"{r['name']} {amount(r['net'])}" for r in ranked) or '无符合项'))
        lines.append(f"{title}｜{group['date']}（{len(rows)}/{group['expectedCount']}项）：" + '；'.join(parts) + citations(rows[0]))
    for holding, row in holding_signals(flows):
        lines.append(f"持仓关注｜{holding['name']}：{row['date']} {amount(row['net'])}，近5日 {amount(row['net5'])}，方向相反，需复查。" + citations(row))
    unavailable = sum(not h['flow'] or h['flow']['stale'] for h in flows['holdings'])
    if unavailable:
        lines.append(f'持仓资金：{unavailable}只缺失、偏旧或未覆盖，详情见完整日报。')
    lines.append('口径：主力为大单交易统计；ETF为份额变化×前日净值估算，不等于现金进出。美股及深市ETF净申赎未覆盖。')
    return lines


def full_flow_sections(flows):
    if not flows:
        return []
    sections = []
    for key, title in [('industry', '行业资金流向'), ('etf', 'ETF净申赎估算')]:
        group = flows[key]
        lines = [group['note'], f"统计日 {group['date'] or '未知'}；覆盖 {len(group['rows'])}/{group['expectedCount']}项。"]
        lines += [f"{r['name']}（{r['code']}）｜当日 {amount(r['net'])}｜近5日 {amount(r['net5'])}"
                  + ('｜数据偏旧' if r['stale'] else '') + citations(r) for r in group['rows']]
        if not group['rows']:
            lines.append('本次未取得可用数据，不代表零流入。')
        for start in range(0, len(lines), 35):
            sections.append((title + ('（续）' if start else ''), lines[start:start + 35]))
    lines = []
    industries = {row['code']: row for row in flows['industry']['rows']}
    for holding in flows['holdings']:
        row = holding['flow']
        line = f"{holding['name']}（{holding['symbol']}）："
        line += (f"{row['date']} {amount(row['net'])}；近5日 {amount(row['net5'])}" + citations(row)
                 + ('；数据偏旧' if row['stale'] else '')) if row else '资金数据未取得。'
        industry = industries.get(holding['industryCode'])
        if industry:
            line += f"；所属行业 {industry['name']}（{industry['date']}）{amount(industry['net'])}" + citations(industry)
        lines.append(line + ' ' + holding['note'])
    sections.append(('持仓关联资金', lines or ['尚无持仓，本期仅提供市场观察。']))
    return sections
