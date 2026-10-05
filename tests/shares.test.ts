import { afterEach, before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { build } from 'esbuild';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

const origin = 'https://app.example.test';
const session = 'a'.repeat(43);
let script: string, mf: Miniflare, db: D1Database;
before(async () => {
  script = (await build({ entryPoints: ['worker/index.ts'], bundle: true, write: false, format: 'esm', platform: 'browser' })).outputFiles[0].text;
});
beforeEach(async () => {
  mf = new Miniflare(convertV4MiniflareOptions({ modules: true, script, compatibilityDate: '2026-09-11', d1Databases: ['DB'],
    bindings: { APP_ORIGIN: origin, GITHUB_CLIENT_ID: 'test', GITHUB_CLIENT_SECRET: 'test', GITHUB_OWNER_ID: '24712937' },
    serviceBindings: { ASSETS: () => new Response('private app') },
    outboundService: () => { throw new Error('No external requests allowed'); } }));
  db = await mf.getD1Database('DB') as unknown as D1Database;
  for (const file of (await readdir('migrations')).filter(f => f.endsWith('.sql')).sort()) {
    const sql = await readFile(`migrations/${file}`, 'utf8');
    await db.batch(sql.trim().split(/;\s*(?=(?:CREATE|INSERT|ALTER|DROP)\b)/).map(s => db.prepare(s)));
  }
  await db.prepare('INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)')
    .bind(createHash('sha256').update(session).digest('base64url'), '24712937', Math.floor(Date.now() / 1000) + 600).run();
  for (const day of ['2026-10-01', '2026-10-02']) {
    await db.prepare(`INSERT INTO daily_runs (id, report_date, mode, github_run_id, state, snapshot_json, created_at)
      VALUES (?, ?, 'daily', '1', 'ready', ?, ?)`)
      .bind(`daily-${day}`, day, '{"cash":"PRIVATE_BALANCE"}', day).run();
    await db.prepare('INSERT INTO reports (id, result_json, created_at) VALUES (?, ?, ?)').bind(`daily-${day}`, JSON.stringify({
      title: `日报 ${day}`, paragraphs: ['今日计划\n完整正文 <script>alert(1)</script>', '长期计划\n继续复查'],
      sources: [{ id: 'P1', title: '个人持仓快照', url: origin, publishedAt: day },
        { id: 'N1', title: '来源 <img>', url: 'https://example.com/news', publishedAt: day },
        { id: 'N2', title: '无效链接', url: 'javascript:alert(1)', publishedAt: day }],
      evidence: { privateField: 'PRIVATE_EVIDENCE', analysisRaw: { invalid: 'UNVALIDATED_AI' } },
    }), day).run();
  }
});
afterEach(async () => { await mf?.dispose(); });
function manage(method = 'GET', requestOrigin = origin) {
  return mf.dispatchFetch(origin + '/api/reports/daily-2026-10-01/share', { method,
    headers: { Cookie: `__Host-mp_session=${session}`, Origin: requestOrigin, 'Content-Type': 'application/json' },
    body: method === 'GET' ? undefined : '{}' });
}

test('anonymous link exposes one escaped report, without account data or application access', async () => {
  assert.deepEqual(await (await manage()).json(), { url: null });
  const { url } = await (await manage('POST')).json() as { url: string };
  assert.match(url, /\/share\/[a-f0-9]{64}$/);
  const response = await mf.dispatchFetch(url);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.match(response.headers.get('X-Robots-Tag')!, /noindex/);
  assert.equal(response.headers.get('Referrer-Policy'), 'no-referrer');
  const html = await response.text();
  for (const text of ['完整正文', '长期计划', '&lt;script&gt;', 'https://example.com/news']) assert.ok(html.includes(text));
  for (const text of ['PRIVATE_BALANCE', 'PRIVATE_EVIDENCE', 'UNVALIDATED_AI', '2026-10-02', '<script>', 'javascript:', `href="${origin}"`]) assert.ok(!html.includes(text), text);
  assert.equal((await mf.dispatchFetch(origin + '/share/style.css')).status, 200);
  for (const path of ['/api/portfolio', '/api/reports', '/api/reports/daily-2026-10-01', '/api/reports/daily-2026-10-01/share', '/assets/private.js']) {
    assert.equal((await mf.dispatchFetch(origin + path)).status, 401);
  }
  for (const path of ['/share', '/share/daily-2026-10-01', '/share/' + 'b'.repeat(64)]) {
    assert.equal((await mf.dispatchFetch(origin + path)).status, 404);
  }
});

test('only owner can manage links; revocation is immediate and reenabling rotates the token', async () => {
  assert.equal((await mf.dispatchFetch(origin + '/api/reports/daily-2026-10-01/share', { method: 'POST' })).status, 401);
  assert.equal((await manage('POST', 'https://other.example')).status, 403);
  const original = await (await manage('POST')).json() as { url: string };
  assert.deepEqual(await (await manage('POST')).json(), original);
  assert.deepEqual(await (await manage('DELETE')).json(), { url: null });
  assert.equal((await mf.dispatchFetch(original.url)).status, 404);
  const next = await (await manage('POST')).json() as { url: string };
  assert.notEqual(next.url, original.url);
  assert.equal((await mf.dispatchFetch(original.url)).status, 404);
  assert.equal((await mf.dispatchFetch(next.url)).status, 200);
});
