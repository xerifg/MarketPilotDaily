import { Fragment } from 'react';
import { number, readingLines, safeUrl, type SectorAnalysis, type SectorRow, type Sectors as SectorData, type Source } from './report-format';

function Percent({ value, relative = false }: { value?: string | null; relative?: boolean }) {
  return <span className={Number(value) > 0 ? 'quote-up' : Number(value) < 0 ? 'quote-down' : ''}>
    {number(value, false, true)}{value != null ? relative ? ' 个百分点' : '%' : ''}
  </span>;
}

function AnalysisText({ text, sources }: { text: string; sources: Source[] }) {
  return <>{readingLines(text).map((line, i) => <p className="report-paragraph" key={i}>{line.split(/(\[[QNPFS]\d+\])/g).map((part, j) => {
    const source = sources.find(row => `[${row.id}]` === part);
    return source ? <a className="citation" key={j} href={safeUrl(source.url)} target="_blank" rel="noopener noreferrer">{part}</a> : <Fragment key={j}>{part}</Fragment>;
  })}</p>)}</>;
}

function SectorFacts({ row }: { row: SectorRow }) {
  return <>
    <p className="flow-meta">行情日期 {row.date ?? '未知'} · 当日 <Percent value={row.changePct} /> · 近5日 <Percent value={row.returns['5']} /> · 近20日 <Percent value={row.returns['20']} /></p>
    <p className="flow-meta">相对沪深300：{[1, 5, 20].map(period => <Fragment key={period}>{period}日 <Percent value={row.relative[period]} relative />{' '}</Fragment>)}</p>
    <p className="flow-meta">{row.kind === 'etf' ? `ETF估算净申赎 · ${row.flowDate ?? '日期未知'}` : `主力资金 · ${row.date ?? '日期未知'}`}：当日 {number(row.net, true, true)}元 · 近5日 {number(row.net5, true, true)}元</p>
    {row.kind === 'industry' ? <p className="flow-meta">上涨／下跌家数：{row.advancers ?? '未知'} / {row.decliners ?? '未知'} · 成交额 {number(row.amount, true)}元</p>
      : <p className="flow-meta">跟踪标的：<a href={safeUrl(row.trackingUrl ?? '')} target="_blank" rel="noopener noreferrer">{row.tracking}</a>；ETF价格不等于跟踪指数收益。</p>}
    {(row.stale || row.flowStale || row.flowDate && row.flowDate !== row.date) && <p className="quote-stale">数据偏旧或资金与行情日期不一致，请分别核对。</p>}
    <details className="flow-details"><summary>查看比较窗口与行情来源</summary>
      {[1, 5, 20].map(period => <p key={period}>{period}日：{row.windows[period] ? `${row.windows[period].start} 至 ${row.windows[period].end}` : '历史行情不足或日期不一致'}</p>)}
      <a className="citation" href={safeUrl(row.sourceUrl)} target="_blank" rel="noopener noreferrer">行情来源 {row.sourceIds.map(id => `[${id}]`).join('')}</a>
    </details>
  </>;
}

export function Sectors({ sectors, analysis = [], sources }: { sectors: SectorData; analysis?: SectorAnalysis[]; sources: Source[] }) {
  const rows = new Map(sectors.rows.map(row => [row.code, row]));
  const selected = sectors.detailCodes.map(code => rows.get(code)).filter((row): row is SectorRow => !!row);
  return <section className="report-section" id="report-sectors"><h3>板块分析 · 走势与持仓影响</h3>
    <p className="flow-meta">行业统计日 {sectors.date ?? '未知'} · 覆盖 {sectors.rows.filter(row => row.kind === 'industry').length}/{sectors.expectedCount} 项 · 沪深300最新日期 {sectors.benchmarkDate ?? '未知'}</p>
    <p className="report-note">{sectors.note}</p>
    {selected.length ? selected.map(row => <div className="holding-review" key={row.code}>
      <h4>{row.name} <small>{row.code}</small></h4>
      <p className="flow-meta">关注原因：{row.reasons.join('；')}</p><SectorFacts row={row} />
      {analysis.find(item => item.code === row.code) ? <AnalysisText text={analysis.find(item => item.code === row.code)!.text} sources={sources} />
        : <p className="report-note">本项未生成可用的板块解读，仅展示已核验数据。</p>}
    </div>) : <p className="report-note">本期无可用的重点板块数据。</p>}
    <details className="flow-details"><summary>查看全部板块快照</summary>
      <p>历史走势仅补采重点与持仓关联项；“未提供”表示未采集或数据不足。正文优先解读最多5个板块，其他关联项见下表。</p>
      <div className="flow-table-scroll" tabIndex={0} role="region" aria-label="板块行情明细，可横向滚动"><table className="flow-table">
        <thead><tr><th scope="col">板块／ETF</th><th scope="col">日期</th><th scope="col">当日</th><th scope="col">近5日</th><th scope="col">近20日</th><th scope="col">上涨／下跌</th><th scope="col">关注原因</th></tr></thead>
        <tbody>{sectors.rows.map(row => <tr key={row.code}><th scope="row"><a href={safeUrl(row.sourceUrl)} target="_blank" rel="noopener noreferrer">{row.name}</a><small>{row.code}{row.stale ? ' · 偏旧' : ''}</small></th>
          <td>{row.date ?? '未知'}</td><td><Percent value={row.changePct} /></td><td><Percent value={row.returns['5']} /></td><td><Percent value={row.returns['20']} /></td><td>{row.advancers ?? '未知'} / {row.decliners ?? '未知'}</td><td>{row.reasons.join('；') || '—'}</td></tr>)}</tbody>
      </table></div>
    </details>
    <details className="flow-details"><summary>持仓与板块关联 · {sectors.holdings.length} 项</summary>
      {sectors.holdings.length ? sectors.holdings.map(holding => <p key={holding.symbol}><strong>{holding.name}</strong>（{holding.symbol}）· {holding.code ? rows.get(holding.code)?.name ?? holding.code : '未关联'}<br />{holding.note}</p>) : <p>尚无持仓，仅提供市场观察。</p>}
    </details>
    <details className="flow-details"><summary>板块数据覆盖与限制</summary><ul>{sectors.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></details>
  </section>;
}
