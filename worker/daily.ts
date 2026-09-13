import { z } from 'zod';
import { HttpError, type AuthEnv } from './auth';
import { authorizeTask } from './task-auth';
import { getPortfolio } from './portfolio';
import { reserveAiCall, settleAiCall, markAiCallUncertain } from './ai-budget';

const idSchema = z.string().regex(/^(daily|test)-\d{4}-\d{2}-\d{2}(?:-v[23])?$/);
const text = z.string().max(6000);
const resultSchema = z.object({
  title: z.string().min(1).max(200), paragraphs: z.array(text).min(1).max(100),
  sources: z.array(z.object({ id: z.string().max(30), title: z.string().max(300), url: z.url().startsWith('https://'),
    publishedAt: z.string().max(40) })).max(40),
  evidence: z.record(z.string(), z.unknown()), model: z.string().max(100),
  estimatedCostCny: z.string().max(30), cutoffAt: z.iso.datetime({ offset: true }),
}).strict();

async function body(request: Request) {
  if (request.method !== 'POST' || !request.headers.get('Content-Type')?.startsWith('application/json')) throw new HttpError(405, '需要 JSON POST。');
  const reader = request.body?.getReader();
  if (!reader) throw new HttpError(400, '请求为空。');
  let size = 0; const chunks: Uint8Array[] = [];
  for (;;) {
    const { value, done } = await reader.read(); if (done) break;
    size += value.byteLength;
    if (size > 250000) { await reader.cancel(); throw new HttpError(413, '报告过大。'); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try { return JSON.parse(new TextDecoder().decode(bytes)); } catch { throw new HttpError(400, 'JSON 无效。'); }
}

interface Run { id: string; mode: string; github_run_id: string; state: string; snapshot_json: string; created_at: string }
async function getRun(db: D1Database, id: string) {
  const row = await db.prepare('SELECT * FROM daily_runs WHERE id = ?').bind(id).first<Run>();
  if (!row) throw new HttpError(404, '日报任务不存在。');
  return row;
}
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());

export async function taskRoute(request: Request, env: AuthEnv): Promise<Response> {
  const githubRunId = await authorizeTask(request, env);
  const path = new URL(request.url).pathname;
  const input = await body(request);
  if (path === '/internal/runs/claim') {
    const { mode, version } = z.object({ mode: z.enum(['daily', 'test']), version: z.number().int().min(1).max(3).default(1) }).strict().parse(input);
    if (mode === 'daily' && version !== 1) throw new HttpError(400, '每日任务不允许自动重生成。');
    const date = today(); const id = `${mode}-${date}${version > 1 ? `-v${version}` : ''}`;
    const existing = await env.DB.prepare('SELECT id FROM daily_runs WHERE id = ?').bind(id).first();
    if (!existing) {
      const snapshot = await getPortfolio(env.DB, 'cloud');
      if (mode === 'daily' && snapshot.profile.emailPaused) return Response.json({ skipped: 'paused' });
      await env.DB.prepare(`INSERT INTO daily_runs (id, report_date, mode, version, github_run_id, state, snapshot_json, created_at)
        SELECT ?, ?, ?, ?, ?, 'collecting', ?, ? WHERE (SELECT revision FROM portfolio_state WHERE id = 1) = ?
        ON CONFLICT(id) DO NOTHING`).bind(id, date, mode, version, githubRunId, JSON.stringify(snapshot), new Date().toISOString(), snapshot.revision).run();
    }
    const run = await getRun(env.DB, id);
    const report = await env.DB.prepare('SELECT result_json FROM reports WHERE id = ?').bind(id).first<{ result_json: string }>();
    const delivery = await env.DB.prepare('SELECT state FROM deliveries WHERE report_id = ?').bind(id).first();
    return Response.json({ id, state: run.state, owned: run.github_run_id === githubRunId, snapshot: JSON.parse(run.snapshot_json),
      createdAt: run.created_at, report: report ? JSON.parse(report.result_json) : null, delivery });
  }
  const match = /^\/internal\/runs\/([^/]+)\/(ai-reserve|ai-settle|ai-uncertain|report|failed|delivery-claim|delivery-finish)$/.exec(path);
  if (!match) throw new HttpError(404, '任务接口不存在。');
  const id = idSchema.parse(match[1]); const action = match[2]; const run = await getRun(env.DB, id);
  if (!action.startsWith('delivery-') && (run.github_run_id !== githubRunId || run.state !== 'collecting')) throw new HttpError(409, '任务已领取或已完成。');
  const aiId = `${id}-ai`;
  if (action === 'ai-reserve') return Response.json({ allowed: await reserveAiCall(env.DB, aiId) });
  if (action === 'ai-settle') {
    const { chargedMicros } = z.object({ chargedMicros: z.number().int().nonnegative().max(100000000) }).strict().parse(input);
    await settleAiCall(env.DB, aiId, chargedMicros);
  } else if (action === 'ai-uncertain') await markAiCallUncertain(env.DB, aiId);
  else if (action === 'report') {
    const result = resultSchema.parse(input);
    await env.DB.batch([
      env.DB.prepare('INSERT INTO reports (id, result_json, created_at) VALUES (?, ?, ?)').bind(id, JSON.stringify(result), new Date().toISOString()),
      env.DB.prepare("UPDATE daily_runs SET state = 'ready' WHERE id = ?").bind(id),
    ]);
  } else if (action === 'failed') {
    const { code } = z.object({ code: z.string().regex(/^[a-z_]{1,80}$/) }).strict().parse(input);
    await env.DB.prepare("UPDATE daily_runs SET state = 'failed', failure_code = ? WHERE id = ?").bind(code, id).run();
  } else if (action === 'delivery-claim') {
    const result = await env.DB.prepare(`INSERT INTO deliveries (report_id, state, updated_at)
      SELECT ?, 'sending', ? WHERE EXISTS (SELECT 1 FROM reports WHERE id = ?)
      AND (? = 'test' OR (SELECT email_paused FROM investor_profile WHERE id = 1) = 0)
      ON CONFLICT(report_id) DO NOTHING RETURNING report_id`).bind(id, new Date().toISOString(), id, run.mode).all();
    return Response.json({ allowed: result.results.length === 1 });
  } else if (action === 'delivery-finish') {
    const { state, errorCode } = z.object({ state: z.enum(['smtp_accepted', 'smtp_rejected', 'failed_before_data', 'delivery_uncertain']),
      errorCode: z.string().regex(/^[a-z_]{1,80}$/).nullable() }).strict().parse(input);
    const row = await env.DB.prepare("UPDATE deliveries SET state = ?, error_code = ?, updated_at = ? WHERE report_id = ? AND state = 'sending' RETURNING report_id")
      .bind(state, errorCode, new Date().toISOString(), id).first();
    if (!row) throw new HttpError(409, '发送结果已记录或尚未领取。');
  }
  return Response.json({ ok: true });
}

export async function reportRoute(request: Request, env: AuthEnv): Promise<Response | null> {
  const path = new URL(request.url).pathname;
  if (request.method !== 'GET') return null;
  if (path === '/api/reports') {
    const rows = await env.DB.prepare(`SELECT r.id, r.report_date AS reportDate, r.mode, r.version, r.state, r.created_at AS createdAt,
      r.failure_code AS failureCode, d.state AS deliveryState, json_extract(p.result_json, '$.evidence.analysisStatus') AS analysisStatus
      FROM daily_runs r LEFT JOIN deliveries d ON d.report_id = r.id LEFT JOIN reports p ON p.id = r.id
      ORDER BY r.created_at DESC LIMIT 30`).all();
    return Response.json(rows.results);
  }
  const match = /^\/api\/reports\/([^/]+)$/.exec(path);
  if (!match) return null;
  const id = idSchema.parse(match[1]);
  const row = await env.DB.prepare('SELECT result_json FROM reports WHERE id = ?').bind(id).first<{ result_json: string }>();
  if (!row) throw new HttpError(404, '报告尚未生成。');
  return Response.json(JSON.parse(row.result_json));
}
