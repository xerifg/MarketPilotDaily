import { Fragment, useEffect, useState } from 'react';
import { api } from './api';
import { FundFlows } from './FundFlows';
import { beijing, benchmarks, number, readingLines, safeUrl, sections, type Report, type Source } from './report-format';

interface Run { id: string; reportDate: string; mode: string; version: number; state: string; deliveryState: string | null; createdAt: string; analysisStatus: string | null }
interface Budget { month: string; limitCny: number; settledCny: number; heldCny: number; availableCny: number; uncertainCalls: number; canAnalyze: boolean }
const states: Record<string, string> = { collecting: '正在采集与分析', ready: '已生成，等待发送', failed: '生成失败，需要检查',
  sending: '正在发送；长时间未更新时请核查', smtp_accepted: '163 服务器已接受',
  smtp_rejected: '163 拒绝接收', failed_before_data: '发送前失败', delivery_uncertain: '发送结果不确定，已停止自动重发' };

function Text({ text, sources }: { text: string; sources: Source[] }) {
  return <>{readingLines(text).map((line, i) => <p className="report-paragraph" key={i}>{line.split(/(\[[QNPF]\d+\])/g).map((part, j) => {
    const source = sources.find(s => `[${s.id}]` === part);
    return source ? <a key={j} className="citation" href={safeUrl(source.url)} target="_blank" rel="noopener noreferrer" title={source.title}>{part}</a> : <Fragment key={j}>{part}</Fragment>;
  })}</p>)}</>;
}

function ReportBody({ report }: { report: Report }) {
  const evidence = report.evidence;
  const advice = evidence?.analysisStatus === 'ok' ? evidence.analysisRaw : null;
  const names = Object.fromEntries((evidence?.metrics ?? []).map(m => [m.symbol, m.name]));
  return <article className="report-content">
    <header className="report-masthead"><span className="eyebrow">MARKETPILOT / DAILY BRIEF</span><h2>{report.title}</h2>
      <p>信息截止 {beijing(report.cutoffAt)}（北京时间）</p><p>新闻窗口：此前 24 小时 · 行情：最近可取得收盘价</p></header>
    {advice && <>
      {evidence?.presentationVersion !== 2 && <p className="report-note">历史分析原文，仅调整排版；其中条件与阈值未按新版规则重新生成，请核对后使用。</p>}
      <section className="report-summary"><h3>先看重点</h3><Text text={advice.overview} sources={report.sources} /></section>
      <p className="report-note">指标说明：可承受回撤不是仓位上限。本系统未计算账户历史回撤，也不能保证某个仓位对应某个回撤上限。</p>
      <nav className="report-nav" aria-label="日报目录"><a href="#plan-today">今日</a><a href="#plan-short">短期</a><a href="#plan-long">长期</a><a href="#report-market">行情</a>{evidence?.fundFlows && <a href="#report-flows">资金</a>}<a href="#report-news">新闻</a></nav>
      <div className="report-plans">{sections.map(([key, title, duration]) => <section className="report-plan" key={key} id={`plan-${key}`}>
        <h3><span>{title}</span><small>{duration}</small></h3><Text text={advice[key]} sources={report.sources} />
      </section>)}</div>
      {advice.holdings.length > 0 && <section className="report-section"><h3>我的持仓 · 逐项复查</h3>
        <p className="report-note">基于当时的持仓快照，后续修改不会改变这份历史分析。</p>
        {advice.holdings.map(row => <details className="holding-review" key={row.symbol} open={advice.holdings.length === 1}>
          <summary>{names[row.symbol] ?? row.symbol} <small>{row.symbol}</small></summary><Text text={row.advice} sources={report.sources} />
        </details>)}</section>}
      <section className="report-section"><h3>事件与风险 · 继续关注</h3><Text text={advice.watch} sources={report.sources} /></section>
    </>}
    {evidence?.quotes ? <>
      {!advice && <p className="report-note">AI 建议未生成或未通过校验，本期仅供核对行情与新闻。</p>}
      <section className="report-section" id="report-market"><h3>市场收盘概览</h3>
        <p className="report-note">涨跌不等于资金净流入。红涨绿跌；不同币种不直接合计。</p>
        <div className="report-quotes">{evidence.quotes.map(quote => <div className="quote-card" key={quote.symbol}>
          <h4>{benchmarks[quote.symbol] ?? names[quote.symbol] ?? quote.symbol}</h4>
          {quote.missing ? <p>行情缺失</p> : <><div className="quote-price"><strong>{number(quote.close)}</strong><span>{['000001.SH', '399001.SZ'].includes(quote.symbol) ? '点' : quote.currency}</span>
            <b className={Number(quote.changePct) > 0 ? 'quote-up' : Number(quote.changePct) < 0 ? 'quote-down' : ''}>{number(quote.changePct, false, true)}%</b></div>
            <p>收盘日 {quote.sessionDate}<br />对比 {quote.previousSessionDate}</p>
            <p>成交额 {number(quote.amount, true)}{quote.amount ? ` ${quote.currency}` : ''} <TextCitation id={quote.id} sources={report.sources} /></p>
            {quote.stale && <p className="quote-stale">数据偏旧，不能作为今日触发依据</p>}</>}
        </div>)}</div></section>
      {evidence.fundFlows && <FundFlows flows={evidence.fundFlows} />}
      <section className="report-section" id="report-news"><h3>过去 24 小时 · 新闻线索</h3><p className="report-note">依据 RSS 标题与摘要，尚未核验全文。英文标题保留原文。</p>
        {evidence.news?.length ? <ol className="report-news">{evidence.news.map(item => <li key={item.id}><a href={safeUrl(item.url)} target="_blank" rel="noopener noreferrer">{item.title}</a><small>{beijing(item.publishedAt)} · 北京时间 · [{item.id}]</small></li>)}</ol> : <p>本期未检出符合时间窗口的相关新闻，不代表没有市场事件。</p>}
      </section>
      <section className="report-section report-limits"><h3>数据缺口 · 判断前须核实</h3><ul>{evidence.missing?.map((item, i) => <li key={i}>{item}</li>)}</ul>
        <details><summary>查看采集覆盖情况</summary><ul>{evidence.coverage?.map((item, i) => <li key={i}>{item}</li>)}</ul></details></section>
    </> : report.paragraphs.map((paragraph, i) => <Text key={i} text={paragraph} sources={report.sources} />)}
    <details className="report-section report-sources"><summary>来源索引 · {report.sources.length} 项</summary><ul>{report.sources.map(source => <li key={source.id}>
      <a href={safeUrl(source.url)} target="_blank" rel="noopener noreferrer">[{source.id}] {source.title}</a><small>{beijing(source.publishedAt)}</small>
    </li>)}</ul></details>
    <footer className="report-end"><p>模型：{report.model} · 本次估算费用：{report.estimatedCostCny} 元</p><p>先核实数据，再决定操作。本日报不自动交易。</p></footer>
  </article>;
}
function TextCitation({ id, sources }: { id: string; sources: Source[] }) {
  const source = sources.find(s => s.id === id);
  return source ? <a className="citation" href={safeUrl(source.url)} target="_blank" rel="noopener noreferrer" title={source.title}>[{id}]</a> : null;
}

export function Reports({ paused }: { paused: boolean }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [id, setId] = useState('');
  const [report, setReport] = useState<Report | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshCount, setRefreshCount] = useState(0);
  async function refresh() {
    setLoading(true); setError('');
    const results = await Promise.allSettled([api<Run[]>('/api/reports'), api<Budget>('/api/budget')]);
    if (results[0].status === 'fulfilled') {
      const rows = results[0].value; setRuns(rows);
      setId(current => rows.some(row => row.id === current) ? current : rows.find(row => row.state === 'ready')?.id ?? '');
      setRefreshCount(count => count + 1);
    } else setError('日报记录读取失败，请刷新重试。');
    if (results[1].status === 'fulfilled') setBudget(results[1].value);
    else { setBudget(null); setError(previous => `${previous} 预算状态暂不可用，请刷新重试。`.trim()); }
    setLoading(false);
  }
  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    let active = true; setReport(null); setDetailError('');
    if (id) api<Report>(`/api/reports/${encodeURIComponent(id)}`).then(value => { if (active) setReport(value); })
      .catch(e => { if (active) setDetailError(e instanceof Error ? e.message : '读取失败'); });
    return () => { active = false; };
  }, [id, refreshCount]);
  const selected = runs.find(run => run.id === id);
  const daily = runs.find(run => run.mode === 'daily');
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const hour = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', hour: '2-digit', hourCycle: 'h23' }).format(new Date()));
  const missingDaily = !paused && !loading && !error && hour >= 6 && !runs.some(run => run.mode === 'daily' && run.reportDate === today && run.deliveryState === 'smtp_accepted');
  return <section className="panel report-panel">
    <div className="report-status-grid"><div><span>每日邮件</span><strong>{paused ? '已暂停' : '05:15 · 北京时间'}</strong><p>05:00 准备，GitHub 调度可能延迟</p></div>
      <div><span>{budget ? `${budget.month} · AI 预算` : 'AI 预算'}</span><strong>{budget ? `¥${budget.settledCny.toFixed(3)} / ¥${budget.limitCny}` : '读取中 / 暂不可用'}</strong><p>本项目估算，DeepSeek 账单为准{budget && budget.heldCny > 0 ? `；另占用 ¥${budget.heldCny.toFixed(3)}` : ''}</p></div>
      <div><span>最近正式日报</span><strong>{daily?.reportDate ?? '尚无记录'}</strong><p>{daily ? states[daily.deliveryState ?? daily.state] : '测试邮件不计入正式推送'}</p></div>
    </div>
    {budget && !budget.canAnalyze && <p className="report-note">可用预算不足以预留下一次分析费用，将暂停付费分析；已启用的每日任务仍可发送基础行情与新闻。</p>}
    {budget && budget.uncertainCalls > 0 && <p className="report-note">有 {budget.uncertainCalls} 次调用结果不确定，费用预留仍保留，避免超支。</p>}
    {missingDaily && <p className="report-note">截至本次刷新，今天尚无正式邮件被 163 接受的记录。若今天应开始推送，请<a href="https://github.com/xerifg/MarketPilotDaily/actions/workflows/daily.yml" target="_blank" rel="noopener noreferrer">检查定时任务</a>。</p>}
    <div className="list-toolbar"><h2>日报阅读</h2><button className="secondary" disabled={loading} onClick={refresh}>刷新记录</button></div>
    {error && <p className="error" role="alert">{error}</p>}
    {loading && <p role="status">正在读取记录…</p>}
    {!loading && !runs.length && <p>还没有生成记录。首次任务完成后可在这里查看真实报告。</p>}
    {runs.length > 0 && <details className="report-history"><summary>切换日报 · 最近 {runs.length} 次记录{selected ? ` · 当前 ${selected.reportDate}${selected.mode === 'test' ? ` 测试第${selected.version}版` : ''}` : ''}</summary>
      {runs.map(run => <div className={`report-row${run.id === id ? ' selected' : ''}`} key={run.id}>
        <div><strong>{run.reportDate}{run.mode === 'test' ? ` · 测试日报 第${run.version}版` : ''}</strong>
          <p>{run.analysisStatus === 'failed' && run.mode === 'test' && !run.deliveryState ? 'AI 校验未通过，测试未发送' : `${run.analysisStatus === 'failed' ? '仅基础信息 · ' : ''}${states[run.deliveryState ?? run.state] ?? '状态待核查'}`}
            {run.state === 'collecting' && Date.now() - Date.parse(run.createdAt) > 45 * 60000 ? '（已超过45分钟，需要核查任务）' : ''}</p></div>
        {run.state === 'ready' && <button className="text-button" aria-pressed={run.id === id} aria-label={`阅读${run.reportDate}第${run.version}版日报`} onClick={() => setId(run.id)}>{run.id === id ? '正在阅读' : '阅读日报'}</button>}
      </div>)}
    </details>}
    {detailError && <p className="error" role="alert">{detailError}</p>}
    {id && !report && !detailError && <p role="status">正在打开日报…</p>}
    {report && <ReportBody report={report} />}
  </section>;
}
