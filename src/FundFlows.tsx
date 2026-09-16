import { useState } from 'react';
import { number, safeUrl, type FlowGroup, type FlowRow, type FundFlows as FlowData } from './report-format';

function FlowAmount({ value }: { value: string | null }) {
  return <span className={value !== null && Number(value) > 0 ? 'quote-up' : value !== null && Number(value) < 0 ? 'quote-down' : ''}>
    {number(value, true, true)}{value !== null ? '元' : ''}</span>;
}

function FlowTable({ rows, etf = false }: { rows: FlowRow[]; etf?: boolean }) {
  return <div className="flow-table-scroll" tabIndex={0} role="region" aria-label={etf ? 'ETF净申赎数据表，可横向滚动' : '行业资金数据表，可横向滚动'}>
    <table className="flow-table"><thead><tr><th>{etf ? 'ETF' : '行业'}</th><th>统计日</th>
      <th>{etf ? '当日净申赎估算' : '当日主力净流入'}</th><th>近5日合计</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.code}><th scope="row"><a href={safeUrl(row.sourceUrl)} target="_blank" rel="noopener noreferrer">{row.name}</a>
        <small>{row.code} · {row.sourceIds.map(id => `[${id}]`).join('')}</small></th><td>{row.date}{row.stale && <small className="quote-stale">数据偏旧</small>}</td>
        <td><FlowAmount value={row.net} />{etf && <small>份额变化 {number(row.shareChange, false, true)}份<br />净值日期 {row.navDate}</small>}</td>
        <td><FlowAmount value={row.net5} /></td></tr>)}</tbody></table>
  </div>;
}

function FlowRanking({ group, etf = false }: { group: FlowGroup; etf?: boolean }) {
  const [period, setPeriod] = useState<'net' | 'net5'>('net');
  const fresh = group.rows.filter(row => !row.stale && row[period] !== null);
  const incoming = fresh.filter(row => Number(row[period]) > 0).sort((a, b) => Number(b[period]) - Number(a[period])).slice(0, 3);
  const outgoing = fresh.filter(row => Number(row[period]) < 0).sort((a, b) => Number(a[period]) - Number(b[period])).slice(0, 3);
  const ordered = [...group.rows].sort((a, b) => (b[period] === null ? -Infinity : Number(b[period])) - (a[period] === null ? -Infinity : Number(a[period])));
  return <div className="flow-group"><h4>{etf ? 'ETF净申赎 · 沪市观察池' : '行业资金 · 主力交易统计'}</h4>
    <p className="report-note">{group.note}</p>
    <p className="flow-meta">统计日 {group.date ?? '未知'} · 已取得 {group.rows.length}/{group.expectedCount} 项{group.status !== 'ok' ? ' · 覆盖不完整' : ''}</p>
    {group.rows.length ? <>
      <div className="flow-period" aria-label="资金排名周期"><button type="button" className="text-button" aria-pressed={period === 'net'} onClick={() => setPeriod('net')}>当日</button>
        <button type="button" className="text-button" aria-pressed={period === 'net5'} onClick={() => setPeriod('net5')}>近5日</button></div>
      <div className="flow-leaders">{([[etf ? '净申购前三' : '净流入前三', incoming], [etf ? '净赎回前三' : '净流出前三', outgoing]] as const).map(([title, rows]) =>
        <div key={title}><h5>{title}</h5>{rows.length ? <ol>{rows.map(row =>
          <li key={row.code}><span>{row.name}</span><FlowAmount value={row[period]} /></li>)}</ol> : <p>没有符合条件的有效数据</p>}</div>)}</div>
      <details className="flow-details"><summary>查看全部 {group.rows.length} 项资金数据与来源</summary><FlowTable rows={ordered} etf={etf} /></details>
    </> : <p>本次未取得可用数据，不代表零流入。</p>}
  </div>;
}

export function FundFlows({ flows }: { flows: FlowData }) {
  const industries = Object.fromEntries(flows.industry.rows.map(row => [row.code, row]));
  return <section className="report-section" id="report-flows"><h3>资金流向</h3>
    <FlowRanking group={flows.industry} /><FlowRanking group={flows.etf} etf />
    <div className="flow-group"><h4>持仓关联资金</h4>{flows.holdings.length ? flows.holdings.map(holding => {
      const row = holding.flow;
      const industry = holding.industryCode ? industries[holding.industryCode] : undefined;
      return <div className="holding-review" key={holding.symbol}><strong>{holding.name}</strong><small>{holding.symbol}</small>
        {row ? <><p>{holding.kind === 'etf' ? '净申赎估算' : '主力净流入'} · {row.date}：<FlowAmount value={row.net} />；近5日：<FlowAmount value={row.net5} />
          {' '}<a className="citation" href={safeUrl(row.sourceUrl)} target="_blank" rel="noopener noreferrer">来源 {row.sourceIds.map(id => `[${id}]`).join('')}</a></p>
          {row.stale ? <p className="quote-stale">数据偏旧，仅供历史参考</p> : row.net5 !== null && Number(row.net) * Number(row.net5) < 0 && <p className="report-note">当日与近5日资金方向相反，需复查持续性。</p>}</> : <p>资金数据未取得</p>}
        {industry && <p>所属行业 {industry.name} · {industry.date}：<FlowAmount value={industry.net} />{industry.stale ? '（偏旧）' : ''}</p>}
        {holding.note && <p className="flow-meta">{holding.note}</p>}</div>;
    }) : <p>尚无持仓，本期仅提供市场观察。</p>}</div>
    <details className="flow-details"><summary>资金数据覆盖与限制</summary><ul>{flows.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></details>
  </section>;
}
