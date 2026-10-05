import { z } from 'zod';
import { HttpError, readBody, type AuthEnv } from './auth';
import { safeUrl, type Source } from '../src/report-format';

const tokenPattern = /^[a-f0-9]{64}$/;
const newToken = () => (crypto.randomUUID() + crypto.randomUUID()).replaceAll('-', '');
const escape = (value: string) => value.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

export async function shareLink(db: D1Database, id: string, origin: string, create = false) {
  if (create) await db.prepare(`INSERT INTO report_shares (report_id, token)
    SELECT id, ? FROM reports WHERE id = ? ON CONFLICT(report_id) DO NOTHING`).bind(newToken(), id).run();
  const row = await db.prepare('SELECT token, revoked FROM report_shares WHERE report_id = ?').bind(id)
    .first<{ token: string; revoked: number }>();
  return { url: row && !row.revoked ? `${origin}/share/${row.token}` : null };
}

export async function manageShare(request: Request, env: AuthEnv): Promise<Response | null> {
  const match = /^\/api\/reports\/([^/]+)\/share$/.exec(new URL(request.url).pathname);
  if (!match) return null;
  const id = match[1];
  if (!await env.DB.prepare('SELECT id FROM reports WHERE id = ?').bind(id).first()) throw new HttpError(404, '报告尚未生成。');
  if (request.method !== 'GET') {
    if (!['POST', 'DELETE'].includes(request.method)) throw new HttpError(405, '不支持此操作。');
    z.object({}).strict().parse(await readBody(request));
    if (request.method === 'DELETE') {
      // Keep a tombstone even if a background sender has not created the link yet.
      await env.DB.prepare(`INSERT INTO report_shares (report_id, token, revoked) VALUES (?, ?, 1)
        ON CONFLICT(report_id) DO UPDATE SET revoked = 1`).bind(id, newToken()).run();
    } else {
      await env.DB.prepare(`INSERT INTO report_shares (report_id, token) VALUES (?, ?)
        ON CONFLICT(report_id) DO UPDATE SET token = excluded.token, revoked = 0 WHERE report_shares.revoked = 1`)
        .bind(id, newToken()).run();
    }
  }
  return Response.json(await shareLink(env.DB, id, new URL(request.url).origin));
}

const css = `*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f3f5ef;color:#263c35;font:16px/1.85 system-ui,sans-serif}main{max-width:800px;margin:32px auto;background:#fff;padding:40px;border:1px solid #dfe6da;border-radius:16px}header{border-bottom:2px solid #355b47;padding-bottom:24px}h1{font-size:30px;line-height:1.4}h2{font-size:21px;line-height:1.5;margin:0 0 20px}section{padding:28px 0;border-bottom:1px solid #e4e9df}p{white-space:pre-wrap;overflow-wrap:anywhere;margin:12px 0}small,footer{color:#637567}a{color:#245c43;overflow-wrap:anywhere}a:focus-visible{outline:3px solid #72997f;outline-offset:3px}li{margin:12px 0}footer{padding-top:24px;font-size:14px}@media(max-width:600px){main{margin:0;padding:24px 20px;border:0;border-radius:0}h1{font-size:26px}h2{font-size:19px}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}`;
function page(title: string, content: string, status = 200) {
  return new Response(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><title>${escape(title)} · MarketPilot</title><link rel="stylesheet" href="/share/style.css"></head><body><main><header><small>MARKETPILOT / 每日投资笔记</small><h1>${escape(title)}</h1></header>${content}</main></body></html>`,
    { status, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}

export async function publicShare(request: Request, env: AuthEnv): Promise<Response | null> {
  const path = new URL(request.url).pathname;
  if (path !== '/share' && !path.startsWith('/share/')) return null;
  if (!['GET', 'HEAD'].includes(request.method)) throw new HttpError(405, '不支持此操作。');
  if (path === '/share/style.css') return new Response(css, { headers: { 'Content-Type': 'text/css; charset=utf-8' } });
  const token = path.slice('/share/'.length);
  const row = tokenPattern.test(token) ? await env.DB.prepare(`SELECT r.result_json FROM reports r
    JOIN report_shares s ON s.report_id = r.id WHERE s.token = ? AND s.revoked = 0`).bind(token)
    .first<{ result_json: string }>() : null;
  if (!row) return page('日报链接不可用', '<p>链接不存在或分享已关闭，请向分享者获取新的链接。</p>', 404);
  // Render only the saved reader-facing text and citations, never the raw evidence or account snapshot.
  const report = JSON.parse(row.result_json) as { title: string; paragraphs: string[]; sources: Source[] };
  const content = report.paragraphs.map(paragraph => {
    const lines = paragraph.split('\n');
    const heading = lines.length > 1 ? `<h2>${escape(lines.shift()!)}</h2>` : '';
    return `<section>${heading}<p>${escape(lines.join('\n'))}</p></section>`;
  }).join('');
  const sources = report.sources.map(source => {
    const label = escape(`[${source.id}] ${source.title}`);
    const url = source.id === 'P1' ? undefined : safeUrl(source.url);
    return `<li>${url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label}<br><small>${escape(source.publishedAt)}</small></li>`;
  }).join('');
  return page(report.title, `${content}<section><h2>来源索引</h2><ul>${sources}</ul></section><footer>这是单篇日报的分享页，持有链接即可阅读。先核实数据，再决定操作；本日报不自动交易。</footer>`);
}
