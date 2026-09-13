import { afterEach, before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { build } from 'esbuild';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import { exportJWK, generateKeyPair, SignJWT } from 'jose';

const origin = 'https://app.example.test';
let script: string, mf: Miniflare, db: D1Database;
let keys: Awaited<ReturnType<typeof generateKeyPair>>;
const claims = { repository: 'xerifg/MarketPilotDaily', repository_id: '1365924685', repository_owner_id: '24712937',
  ref: 'refs/heads/main', workflow_ref: 'xerifg/MarketPilotDaily/.github/workflows/daily.yml@refs/heads/main',
  runner_environment: 'github-hosted', event_name: 'workflow_dispatch', run_id: '1234' };
before(async () => {
  script = (await build({ entryPoints: ['worker/index.ts'], bundle: true, write: false, format: 'esm', platform: 'browser' })).outputFiles[0].text;
  keys = await generateKeyPair('RS256', { extractable: true });
});
beforeEach(async () => {
  const jwk = { ...await exportJWK(keys.publicKey), kid: 'test-key', alg: 'RS256', use: 'sig' };
  mf = new Miniflare(convertV4MiniflareOptions({ modules: true, script, compatibilityDate: '2026-09-11', d1Databases: ['DB'],
    bindings: { APP_ORIGIN: origin, GITHUB_CLIENT_ID: 'test', GITHUB_CLIENT_SECRET: 'test', GITHUB_OWNER_ID: '24712937' },
    serviceBindings: { ASSETS: () => new Response('private') },
    outboundService: request => {
      assert.equal(request.url, 'https://token.actions.githubusercontent.com/.well-known/jwks');
      return Response.json({ keys: [jwk] });
    } }));
  db = await mf.getD1Database('DB') as unknown as D1Database;
  for (const file of (await readdir('migrations')).filter(f => f.endsWith('.sql')).sort()) {
    const sql = await readFile(`migrations/${file}`, 'utf8');
    await db.batch(sql.trim().split(/;\s*(?=(?:CREATE|INSERT|ALTER|DROP)\b)/).map(s => db.prepare(s)));
  }
});
afterEach(async () => { await mf?.dispose(); });
async function token(extra: Record<string, unknown> = {}, audience = origin, expiry = '5m') {
  return new SignJWT({ ...claims, ...extra }).setProtectedHeader({ alg: 'RS256', kid: 'test-key' })
    .setIssuer('https://token.actions.githubusercontent.com').setAudience(audience)
    .setSubject('repo:xerifg@24712937/MarketPilotDaily@1365924685:ref:refs/heads/main')
    .setIssuedAt().setNotBefore('0s').setExpirationTime(expiry).sign(keys.privateKey);
}
async function post(path: string, value: unknown, jwt = '') {
  return mf.dispatchFetch(origin + '/internal/' + path, { method: 'POST', headers: { Authorization: `Bearer ${jwt}`, 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
}
const report = () => ({ title: '测试', paragraphs: ['测试正文'], sources: [], evidence: {}, model: 'test', estimatedCostCny: '0', cutoffAt: new Date().toISOString() });

test('OIDC requires valid signature, audience, expiry, exact repository and trusted workflow', async () => {
  assert.equal((await post('runs/claim', { mode: 'test' })).status, 401);
  for (const extra of [{ repository_id: '999' }, { ref: 'refs/heads/other' }, { event_name: 'pull_request' },
    { workflow_ref: 'xerifg/MarketPilotDaily/.github/workflows/untrusted.yml@refs/heads/main' }, { runner_environment: 'self-hosted' }]) {
    assert.equal((await post('runs/claim', { mode: 'test' }, await token(extra))).status, 401);
  }
  assert.equal((await post('runs/claim', { mode: 'test' }, await token({}, 'https://wrong.example'))).status, 401);
  assert.equal((await post('runs/claim', { mode: 'test' }, await token({}, origin, '-1s'))).status, 401);
  const jwt = await token();
  const parts = jwt.split('.'); parts[2] = (parts[2][0] === 'A' ? 'B' : 'A') + parts[2].slice(1);
  assert.equal((await post('runs/claim', { mode: 'test' }, parts.join('.'))).status, 401);
  assert.equal((await mf.dispatchFetch(origin + '/api/portfolio', { headers: { Authorization: `Bearer ${jwt}` } })).status, 401);
});

test('daily pause, immutable snapshot, budget and once-only delivery persist across runner retries', async () => {
  const jwt = await token();
  assert.deepEqual(await (await post('runs/claim', { mode: 'daily' }, jwt)).json(), { skipped: 'paused' });
  const run = await (await post('runs/claim', { mode: 'test' }, jwt)).json() as { id: string; snapshot: { revision: number } };
  assert.match(run.id, /^test-\d{4}-\d{2}-\d{2}$/);
  await db.prepare('UPDATE portfolio_state SET revision = revision + 1 WHERE id = 1').run();
  const again = await (await post('runs/claim', { mode: 'test' }, jwt)).json() as typeof run;
  assert.equal(again.snapshot.revision, run.snapshot.revision);
  assert.deepEqual(await (await post(`runs/${run.id}/ai-reserve`, {}, jwt)).json(), { allowed: true });
  assert.deepEqual(await (await post(`runs/${run.id}/ai-reserve`, {}, jwt)).json(), { allowed: false });
  assert.equal((await post(`runs/${run.id}/ai-settle`, { chargedMicros: 9000 }, jwt)).status, 200);
  assert.equal((await post(`runs/${run.id}/report`, report(), jwt)).status, 200);
  assert.equal((await post(`runs/${run.id}/report`, report(), jwt)).status, 409);
  const deliveries = await Promise.all([post(`runs/${run.id}/delivery-claim`, {}, jwt), post(`runs/${run.id}/delivery-claim`, {}, jwt)]);
  const results = await Promise.all(deliveries.map(r => r.json() as Promise<{ allowed: boolean }>));
  assert.equal(results.filter(r => r.allowed).length, 1);
  assert.equal((await post(`runs/${run.id}/delivery-finish`, { state: 'delivery_uncertain', errorCode: 'smtp_data_result_unknown' }, jwt)).status, 200);
  assert.deepEqual(await (await post(`runs/${run.id}/delivery-claim`, {}, await token({ run_id: '5678' }))).json(), { allowed: false });
  const row = await db.prepare('SELECT state FROM deliveries WHERE report_id = ?').bind(run.id).first();
  assert.equal(row?.state, 'delivery_uncertain');
});

test('another runner cannot overwrite an in-progress report; pause is checked again before sending', async () => {
  await db.prepare('UPDATE investor_profile SET email_paused = 0 WHERE id = 1').run();
  const jwt = await token();
  const run = await (await post('runs/claim', { mode: 'daily' }, jwt)).json() as { id: string };
  assert.equal((await post(`runs/${run.id}/report`, report(), await token({ run_id: '5678' }))).status, 409);
  assert.equal((await post(`runs/${run.id}/report`, report(), jwt)).status, 200);
  await db.prepare('UPDATE investor_profile SET email_paused = 1 WHERE id = 1').run();
  assert.deepEqual(await (await post(`runs/${run.id}/delivery-claim`, {}, jwt)).json(), { allowed: false });
  assert.equal((await mf.dispatchFetch(origin + '/api/reports')).status, 401);
});
