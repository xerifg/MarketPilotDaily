import { useEffect, useState } from 'react';
import { api } from './api';

interface Run { id: string; reportDate: string; mode: string; version: number; state: string; deliveryState: string | null; createdAt: string; analysisStatus: string | null }
interface Report { title: string; paragraphs: string[]; cutoffAt: string; model: string; estimatedCostCny: string;
  sources: { id: string; title: string; url: string; publishedAt: string }[] }
const states: Record<string, string> = { collecting: '正在采集与分析', ready: '已生成，等待发送', failed: '生成失败，需要检查',
  sending: '正在发送；若长时间未更新，请核查后再操作', smtp_accepted: '163 服务器已接受（请核对收件箱）',
  smtp_rejected: '163 拒绝接收', failed_before_data: '发送前失败', delivery_uncertain: '发送结果不确定，已停止自动重发' };

export function Reports({ paused }: { paused: boolean }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [id, setId] = useState('');
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  async function refresh() {
    setLoading(true); setError('');
    try { setRuns(await api<Run[]>('/api/reports')); }
    catch (e) { setError(e instanceof Error ? e.message : '读取失败'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    let active = true; setReport(null);
    if (id) api<Report>(`/api/reports/${encodeURIComponent(id)}`).then(value => { if (active) setReport(value); })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : '读取失败'); });
    return () => { active = false; };
  }, [id]);
  return <section className="panel report-panel">
    <div className="list-toolbar"><h2>最近 30 次日报记录</h2><button className="secondary" disabled={loading} onClick={refresh}>刷新记录</button></div>
    <p className="muted">{paused ? '每日邮件已暂停，可在投资设置中恢复。手动测试独立于每日推送。' : '每日任务计划 08:15 开始准备，以北京时间 08:30 为发送目标；GitHub 调度可能延迟。'}</p>
    {error && <p className="error" role="alert">{error}</p>}
    {loading && <p role="status">正在读取记录…</p>}
    {!loading && !runs.length && <p>还没有生成记录。首次任务完成后可在这里查看真实报告。</p>}
    {runs.map(run => <div className="report-row" key={run.id}>
      <div><strong>{run.reportDate}{run.mode === 'test' ? ` · 测试日报 第${run.version}版` : ''}</strong>
        <p>{!run.deliveryState && run.analysisStatus === 'failed' ? 'AI 内容校验未通过，未发送' : states[run.deliveryState ?? run.state] ?? '状态待核查'}
          {run.state === 'collecting' && Date.now() - Date.parse(run.createdAt) > 45 * 60000 ? '（已超过45分钟，需要核查任务）' : ''}</p></div>
      {run.state === 'ready' && <button className="text-button" aria-label={`阅读${run.reportDate}第${run.version}版日报`} onClick={() => { setError(''); setId(run.id); }}>阅读日报</button>}
    </div>)}
    {report && <article className="report-content"><h2>{report.title}</h2>
      <p className="muted">截止：{new Date(report.cutoffAt).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}（北京时间）<br />
        模型：{report.model} · 估算费用：{report.estimatedCostCny} 元</p>
      {report.paragraphs.map((paragraph, i) => <p className="report-paragraph" key={i}>{paragraph}</p>)}
      <h3>来源与时间</h3><ul>{report.sources.map(source => <li key={source.id}>
        <a href={source.url} target="_blank" rel="noopener noreferrer">[{source.id}] {source.title}</a> · {source.publishedAt}
      </li>)}</ul></article>}
  </section>;
}
