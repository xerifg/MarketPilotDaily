import { useEffect, useState } from 'react';
import { api } from './api';

export function ReportShare({ id }: { id: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const path = `/api/reports/${encodeURIComponent(id)}/share`;
  useEffect(() => {
    let active = true;
    api<{ url: string | null }>(path).then(value => { if (active) setUrl(value.url); })
      .catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [path]);
  async function change(method: string) {
    setBusy(true); setError(''); setCopied(false);
    try { setUrl((await api<{ url: string | null }>(path, method, {})).url); }
    catch (e) { setError(e instanceof Error ? e.message : '分享设置失败，请重试。'); }
    finally { setBusy(false); }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(url!); setCopied(true); }
    catch { setError('复制失败，可打开分享页后复制地址栏中的链接。'); }
  }
  return <section className="report-section" aria-label="日报分享">
    <h3>免登录阅读</h3>
    <p className="report-note">持有链接的人可阅读本篇完整分析，包括持仓名称、建议及文中比例。关闭分享后，邮件和已转发的旧链接都会失效；再次开启会生成新链接。</p>
    {url ? <div className="list-toolbar"><a href={url} target="_blank" rel="noopener noreferrer">打开分享页</a>
      <button className="secondary" disabled={busy} onClick={copy}>{copied ? '已复制' : '复制链接'}</button>
      <button className="secondary" disabled={busy} onClick={() => change('DELETE')}>关闭分享</button></div>
      : <button className="secondary" disabled={busy} onClick={() => change('POST')}>{busy ? '读取中…' : '开启本篇分享'}</button>}
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
