"""Select the bounded email digest; the saved full report remains unchanged."""
import re
from jobs.flow_presentation import flow_highlights


def validate_summary(summary, sources):
    if not isinstance(summary, dict) or set(summary) != {'conclusion', 'actions', 'events', 'holdings', 'limitations'}:
        raise ValueError('invalid_email_summary')
    texts = []
    for key, limit in [('conclusion', 150), ('limitations', 120)]:
        value = summary[key]
        if not isinstance(value, str) or len(value) > limit or (key == 'conclusion' and not value.strip()):
            raise ValueError('invalid_email_summary')
        texts.append(value)
    for key in ('actions', 'events', 'holdings'):
        rows = summary[key]
        if not isinstance(rows, list) or len(rows) > 3:
            raise ValueError('invalid_email_summary')
        if any(not isinstance(row, str) or not row.strip() or len(row) > 150 for row in rows):
            raise ValueError('invalid_email_summary')
        texts.extend(rows)
    if sum(map(len, texts)) > 600:
        raise ValueError('email_summary_too_long')
    cited = set(re.findall(r'\[([A-Za-z]+\d+)\]', ' '.join(texts)))
    if not cited.issubset({source['id'] for source in sources}):
        raise ValueError('unknown_email_evidence')
    return summary


def email_sections(report):
    evidence = report.get('evidence', {})
    summary = None
    if evidence.get('analysisStatus') == 'ok':
        try:
            summary = validate_summary(evidence.get('emailSummary'), report['sources'])
        except ValueError:
            pass
    if summary:
        sections = [('今日结论', [summary['conclusion']]),
                    ('今日行动', summary['actions'] or ['暂无明确行动，继续观察。'])]
        for key, title in [('events', '重要事件与风险'), ('holdings', '持仓重要变化')]:
            if summary[key]:
                sections.append((title, summary[key]))
    elif evidence.get('analysisStatus') == 'ok':
        # Old reports have no digest. Never truncate conditions or send all sections.
        overview = (evidence.get('analysisRaw') or {}).get('overview', '')
        sections = [('今日结论', [overview] if isinstance(overview, str) and 0 < len(overview) <= 150 else []),
                    ('摘要状态', ['本期未生成可用的重点摘要，行动条件与风险请查看完整日报。'])]
    else:
        sections = [('分析状态', ['AI 建议未生成或未通过校验，请在完整日报中核对行情与新闻。'])]
    flows = flow_highlights(evidence.get('fundFlows'))
    if flows:
        sections.append(('资金流向重点', flows))
    warnings = [summary['limitations']] if summary and summary['limitations'] else []
    if any(q.get('stale') or q.get('missing') for q in evidence.get('quotes', [])):
        warnings.append('部分行情缺失或偏旧，不能作为今日操作的触发依据。')
    if evidence.get('missing') and not warnings:
        warnings.append('部分数据未覆盖，相关判断需核实；详情见完整日报的数据限制。')
    if warnings:
        sections.append(('数据提醒', warnings))
    return [(title, lines) for title, lines in sections if lines]


def email_sources(report):
    text = '\n'.join(line for _, lines in email_sections(report) for line in lines)
    cited = set(re.findall(r'\[([A-Za-z]+\d+)\]', text))
    return [(f"[{s['id']}] {s['title']}", s['url']) for s in report['sources'] if s['id'] in cited]
