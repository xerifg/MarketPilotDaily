import { afterEach, before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { build } from 'esbuild';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import type { Portfolio } from '../shared/schema';
import { budgetMonth, getBudget, reserveAiCall, settleAiCall, markAiCallUncertain } from '../worker/ai-budget';

let script: string;
let mf: Miniflare;
let db: D1Database;
let failLookup = false;
let directoryMode: 'ok' | 'failure' | 'malformed' = 'ok';

async function migrate(database: D1Database, file: string) {
  const migration = await readFile(`migrations/${file}`, 'utf8');
  await database.batch(migration.trim().split(/;\s*(?=(?:CREATE|INSERT|ALTER|DROP)\b)/).map(sql => database.prepare(sql.trim())));
}

before(async () => {
  const bundle = await build({ entryPoints: ['worker/index.ts'], bundle: true, write: false, format: 'esm', platform: 'browser', target: 'es2022' });
  script = bundle.outputFiles[0].text;
});

beforeEach(async () => {
  failLookup = false;
  directoryMode = 'ok';
  mf = new Miniflare(convertV4MiniflareOptions({
    modules: true, script, compatibilityDate: '2026-09-11',
    bindings: { LOCAL_DEV_AUTH: 'true', APP_ORIGIN: 'https://app.example.test', GITHUB_CLIENT_ID: 'test-client', GITHUB_CLIENT_SECRET: 'test-secret', GITHUB_OWNER_ID: '24712937' },
    d1Databases: ['DB', 'LEGACY'],
    serviceBindings: { ASSETS: () => new Response('<html>Private app</html>', { headers: { 'Content-Type': 'text/html' } }) },
    outboundService: async request => {
      const url = new URL(request.url);
      if (url.hostname === 'www.nasdaqtrader.com') {
        if (directoryMode === 'failure') return new Response('Unavailable', { status: 503 });
        if (directoryMode === 'malformed') return new Response('<html>Unknown format</html>');
        return new Response(url.pathname.endsWith('/nasdaqlisted.txt')
          ? 'Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\r\nAAPL|Apple Common Stock|Q|N|N|100|N|N\r\nQQQ|Invesco QQQ|G|N|N|100|Y|N\r\nTEST|Test security|Q|Y|N|100|N|N\r\nFile Creation Time: 0911202621:31|||||||\r\n'
          : 'ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\r\nVOO|Vanguard S&P 500 ETF|P|VOO|Y|100|N|VOO\r\nBRK.B|Berkshire Hathaway|N|BRK.B|N|100|N|BRK.B\r\nFile Creation Time: 0911202621:31|||||||\r\n');
      }
      assert.equal(url.hostname, 'free-api.tickflow.org', 'Tests must never call another external service');
      if (failLookup) return new Response('Unavailable', { status: 503 });
      const symbol = url.searchParams.get('symbols')!;
      const instruments: Record<string, { name: string; type: string; region?: string }> = {
        '600000.SH': { name: '测试股票', type: 'stock' },
        '510300.SH': { name: '测试 ETF', type: 'etf' },
        '000001.SH': { name: '测试指数', type: 'index' },
        '000001.SZ': { name: '测试深市股票', type: 'stock' },
        'AAPL.US': { name: '测试美股', type: 'stock' },
        'QQQ.US': { name: '测试美股 ETF', type: 'stock' },
        'VOO.US': { name: '测试美股 ETF 2', type: 'stock' },
        'BRK.B.US': { name: '测试 B 类股票', type: 'stock' },
        'TEST.US': { name: '测试专用证券', type: 'stock' },
        'MISSING.US': { name: '未在官方目录找到', type: 'stock' },
        'BAD.US': { name: '错误的地区', type: 'stock', region: 'CN' },
      };
      const item = instruments[symbol];
      return Response.json({ data: item ? [{ ...item, symbol, code: symbol.slice(0, symbol.lastIndexOf('.')),
        exchange: symbol.slice(symbol.lastIndexOf('.') + 1), region: item.region ?? (symbol.endsWith('.US') ? 'US' : 'CN') }] : [] });
    },
  }));
  db = await mf.getD1Database('DB') as unknown as D1Database;
  for (const file of (await readdir('migrations')).filter(file => file.endsWith('.sql')).sort()) await migrate(db, file);
});
afterEach(async () => { await mf?.dispose(); });

function request(path: string, method = 'GET', body?: unknown, headers: Record<string, string> = {}, origin = 'http://localhost') {
  return mf.dispatchFetch(`${origin}${path}`, {
    method, headers: { ...(body === undefined ? {} : { Origin: origin, 'Content-Type': 'application/json' }), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
async function portfolio() { return await (await request('/api/portfolio')).json() as Portfolio; }
const fields = { quantity: '100', averageCost: null, horizon: null, thesis: '' };
async function add(overrides = {}) {
  return request('/api/positions', 'POST', { symbol: '600000.SH', ...fields, revision: (await portfolio()).revision, ...overrides });
}
test('initial snapshot preserves unknown cash, costs and risk preferences', async () => {
  const value = await portfolio();
  assert.equal(value.revision, 0); assert.deepEqual(value.cash, { CNY: null, USD: null }); assert.equal(value.storage, 'local');
  assert.deepEqual(value.profile, { horizon: null, maxDrawdown: null, maxPosition: null, emailPaused: true });
  const response = await add(); assert.equal(response.status, 201);
  const created = await response.json() as Portfolio;
  assert.equal(created.positions[0].averageCost, null); assert.equal(created.revision, 1);
});

test('CRUD stores decimals exactly, distinguishes zero cost, and supports undo', async () => {
  const response = await add({ quantity: '100.123456', averageCost: '0.000000' });
  let value = await response.json() as Portfolio;
  assert.equal(value.positions[0].quantity, '100.123456'); assert.equal(value.positions[0].averageCost, '0');
  const id = value.positions[0].id;
  value = await (await request(`/api/positions/${id}`, 'PATCH', { ...fields, averageCost: '999999999999.123456', revision: value.revision })).json() as Portfolio;
  assert.equal(value.positions[0].averageCost, '999999999999.123456');
  value = await (await request(`/api/positions/${id}`, 'DELETE', { revision: value.revision })).json() as Portfolio;
  assert.equal(value.positions.length, 0); assert.equal(value.revision, 3);
  value = await (await request(`/api/positions/${id}/restore`, 'POST', { revision: value.revision })).json() as Portfolio;
  assert.equal(value.positions[0].id, id); assert.equal(value.revision, 4);
});

test('parallel edits of one revision have exactly one winner and a coherent snapshot', async () => {
  const value = await (await add()).json() as Portfolio;
  const replies = await Promise.all(['200', '300'].map(quantity => request(`/api/positions/${value.positions[0].id}`, 'PATCH', { ...fields, quantity, revision: 1 })));
  assert.deepEqual(replies.map(reply => reply.status).sort(), [200, 409]);
  const winner = await replies.find(reply => reply.status === 200)!.json() as Portfolio;
  const current = await portfolio();
  assert.equal(current.revision, 2); assert.deepEqual(winner, current);
});

test('cash and profile changes invalidate a stale position edit', async () => {
  const value = await (await add()).json() as Portfolio;
  assert.equal((await request('/api/cash', 'PATCH', { currency: 'CNY', cash: '0', revision: value.revision })).status, 200);
  assert.equal((await request(`/api/positions/${value.positions[0].id}`, 'PATCH', { ...fields, revision: 1 })).status, 409);
  assert.equal((await request('/api/profile', 'PATCH', { horizon: 'long', maxDrawdown: '15', maxPosition: null, emailPaused: true, revision: 2 })).status, 200);
  const current = await portfolio(); assert.equal(current.revision, 3); assert.equal(current.cash.CNY, '0'); assert.equal(current.profile.horizon, 'long');
});

test('duplicate security is rejected without incrementing revision', async () => {
  await add(); assert.equal((await add()).status, 409); assert.equal((await portfolio()).revision, 1);
});

test('unknown symbols, unsupported index and client-forged name cannot enter holdings', async () => {
  assert.equal((await add({ symbol: '999999.SH' })).status, 422);
  assert.equal((await add({ symbol: '000001.SH' })).status, 422);
  assert.equal((await add({ name: '伪造名称' })).status, 400);
  assert.equal((await add({ symbol: '000001.SZ' })).status, 201);
  assert.equal((await portfolio()).positions[0].name, '测试深市股票');
});

test('upstream failure cannot create an unverified holding', async () => {
  failLookup = true; assert.equal((await add()).status, 503); assert.equal((await portfolio()).revision, 0);
});

test('invalid quantities, amounts and unexpected fields are rejected', async () => {
  for (const quantity of ['0', '-1', '1e3', '1.1234567', 'NaN', '1000000000000']) {
    assert.equal((await add({ quantity })).status, 400, quantity);
  }
  assert.equal((await add({ averageCost: -1 })).status, 400);
  assert.equal((await request('/api/profile', 'PATCH', { horizon: null, maxDrawdown: '101', maxPosition: null, emailPaused: true, revision: 0 })).status, 400);
  assert.equal((await portfolio()).revision, 0);
});

test('expired undo cannot restore a removed holding', async () => {
  let value = await (await add()).json() as Portfolio;
  const id = value.positions[0].id;
  await request(`/api/positions/${id}`, 'DELETE', { revision: 1 });
  await db.prepare('UPDATE positions SET deleted_at = ? WHERE id = ?').bind('2000-01-01T00:00:00.000Z', id).run();
  assert.equal((await request(`/api/positions/${id}/restore`, 'POST', { revision: (await portfolio()).revision })).status, 409);
  value = await (await add()).json() as Portfolio;
  assert.equal(value.positions.length, 1); assert.notEqual(value.positions[0].id, id);
});

test('undo cannot duplicate a security that was added again', async () => {
  const original = await (await add()).json() as Portfolio;
  const id = original.positions[0].id;
  await request(`/api/positions/${id}`, 'DELETE', { revision: 1 });
  const replacement = await (await add()).json() as Portfolio;
  assert.equal((await request(`/api/positions/${id}/restore`, 'POST', { revision: replacement.revision })).status, 409);
  assert.deepEqual(await portfolio(), replacement);
});

test('cross-origin, missing Origin, and non-JSON writes fail before mutation', async () => {
  const body = { currency: 'CNY', cash: '20', revision: 0 };
  assert.equal((await request('/api/cash', 'PATCH', body, { Origin: 'https://attacker.test' })).status, 403);
  assert.equal((await request('/api/cash', 'PATCH', body, { Origin: '' })).status, 403);
  assert.equal((await request('/api/cash', 'PATCH', body, { 'Content-Type': 'text/plain' })).status, 415);
  assert.equal((await portfolio()).revision, 0);
});

test('anonymous users see only the login page; forged identity headers cannot open assets or API', async () => {
  const headers = { 'Cf-Access-Authenticated-User-Email': 'owner@example.test', 'Cf-Access-Jwt-Assertion': 'forged' };
  const root = await request('/', 'GET', undefined, headers, 'https://app.example.test');
  assert.equal(root.status, 200); assert.match(await root.text(), /使用 GitHub 登录/);
  for (const path of ['/api/portfolio', '/assets/app.js', '/internal/runs/claim']) {
    assert.equal((await request(path, 'GET', undefined, headers, 'https://app.example.test')).status, 401);
  }
});

test('sensitive responses are not cached and browser identity cannot call task endpoints', async () => {
  assert.equal((await request('/api/portfolio')).headers.get('Cache-Control'), 'no-store');
  assert.equal((await request('/internal/runs/claim', 'POST', {})).status, 403);
});

test('missing production identity configuration fails closed even with local dev flag set', async () => {
  await mf.setOptions(convertV4MiniflareOptions({
    modules: true, script, compatibilityDate: '2026-09-11',
    bindings: { LOCAL_DEV_AUTH: 'true', APP_ORIGIN: 'https://app.example.test', GITHUB_CLIENT_ID: '', GITHUB_CLIENT_SECRET: '', GITHUB_OWNER_ID: '' },
  }));
  for (const path of ['/', '/api/portfolio']) {
    assert.equal((await request(path, 'GET', undefined, {}, 'https://app.example.test')).status, 503);
  }
});

test('US stocks, fractional shares, class tickers and US ETFs coexist with CNY holdings', async () => {
  await add({ symbol: '510300.SH' });
  for (const symbol of ['aapl.us', 'QQQ.US', 'VOO.US', 'BRK.B.US']) {
    assert.equal((await add({ symbol, quantity: '0.123456', averageCost: '234.123456' })).status, 201, symbol);
  }
  const value = await portfolio();
  assert.equal(value.positions.length, 5);
  assert.equal(value.positions.find(item => item.symbol === '510300.SH')?.currency, 'CNY');
  for (const item of value.positions.filter(item => item.exchange === 'US')) {
    assert.equal(item.currency, 'USD'); assert.equal(item.quantity, '0.123456');
    assert.equal(item.averageCost, '234.123456');
    assert.equal(item.assetType, ['QQQ.US', 'VOO.US'].includes(item.symbol) ? 'etf' : 'stock');
  }
  assert.equal(value.positions.find(item => item.symbol === 'BRK.B.US')?.code, 'BRK.B');
  assert.equal((await add({ symbol: 'AAPL.US' })).status, 409);
  assert.equal((await add({ symbol: 'BAD.US' })).status, 422);
  assert.equal((await add({ symbol: 'AAPL.US', currency: 'CNY' })).status, 400);
});

test('missing or malformed US directory cannot silently classify an ETF as a stock', async () => {
  directoryMode = 'failure'; assert.equal((await add({ symbol: 'VOO.US' })).status, 503);
  directoryMode = 'malformed'; assert.equal((await add({ symbol: 'QQQ.US' })).status, 503);
  directoryMode = 'ok';
  assert.equal((await add({ symbol: 'MISSING.US' })).status, 422);
  assert.equal((await add({ symbol: 'TEST.US' })).status, 422);
  assert.equal((await portfolio()).revision, 0);
});

test('cash balances remain separate, preserve unknown and zero, and share conflict protection', async () => {
  assert.equal((await request('/api/cash', 'PATCH', { currency: 'CNY', cash: '12000.123456', revision: 0 })).status, 200);
  let value = await portfolio();
  assert.deepEqual(value.cash, { CNY: '12000.123456', USD: null });
  assert.equal(value.cashAsOf.USD, null);
  const cnyDate = value.cashAsOf.CNY;
  assert.equal((await request('/api/cash', 'PATCH', { currency: 'USD', cash: '0', revision: 0 })).status, 409);
  assert.equal((await request('/api/cash', 'PATCH', { currency: 'USD', cash: '0', revision: 1 })).status, 200);
  value = await portfolio();
  assert.deepEqual(value.cash, { CNY: '12000.123456', USD: '0' }); assert.equal(value.cashAsOf.CNY, cnyDate);
  assert.equal((await request('/api/cash', 'PATCH', { currency: 'USD', cash: null, revision: 2 })).status, 200);
  assert.deepEqual((await portfolio()).cash, { CNY: '12000.123456', USD: null });
  assert.equal((await request('/api/cash', 'PATCH', { cash: '42', revision: 3 })).status, 400);
  assert.equal((await request('/api/cash', 'PATCH', { currency: 'HKD', cash: '42', revision: 3 })).status, 400);
});

test('upgrading the old database preserves holdings, deleted rows, exact costs, cash and revision', async () => {
  const legacy = await mf.getD1Database('LEGACY') as unknown as D1Database;
  await migrate(legacy, '0001_portfolio.sql');
  await legacy.prepare(`INSERT INTO positions (id, symbol, code, name, exchange, asset_type, currency,
    quantity, average_cost, verified_at, updated_at, deleted_at)
    VALUES ('existing', '510300.SH', '510300', '已有 ETF', 'SH', 'etf', 'CNY', '100.123456', '3.123456', '2026-09-11', '2026-09-11', NULL),
    ('deleted', '600000.SH', '600000', '已删除股票', 'SH', 'stock', 'CNY', '100', NULL, '2026-09-11', '2026-09-11', '2026-09-11')`).run();
  await legacy.prepare("UPDATE portfolio_state SET cash = '123.123456', cash_as_of = '2026-09-11' WHERE id = 1").run();
  const before = await legacy.prepare('SELECT * FROM positions ORDER BY id').all();
  const state = await legacy.prepare('SELECT * FROM portfolio_state').first();
  await migrate(legacy, '0002_us_holdings.sql');
  assert.deepEqual((await legacy.prepare('SELECT * FROM positions ORDER BY id').all()).results, before.results);
  assert.deepEqual(await legacy.prepare('SELECT * FROM portfolio_state').first(), { ...state, cash_usd: null, cash_usd_as_of: null });
  await legacy.prepare("UPDATE positions SET quantity = '200' WHERE id = 'existing'").run();
  assert.equal((await legacy.prepare('SELECT revision FROM portfolio_state').first<{ revision: number }>())?.revision, 3);
  await assert.rejects(legacy.prepare("UPDATE positions SET currency = 'USD' WHERE id = 'existing'").run());
});

test('monthly AI budget cannot be exceeded by concurrent reservations or duplicate call IDs', async () => {
  const now = new Date('2026-09-13T00:15:00Z');
  const reservations = await Promise.all(Array.from({ length: 55 }, (_, index) => reserveAiCall(db, `call-${index}`, now)));
  assert.equal(reservations.filter(Boolean).length, 50);
  const first = await db.prepare('SELECT id FROM ai_calls LIMIT 1').first<{ id: string }>();
  assert.equal(await reserveAiCall(db, first!.id, now), false);
  assert.equal(await reserveAiCall(db, 'beyond-budget', now), false);
});

test('settled usage releases unused reservation; unknown calls keep budget occupied', async () => {
  const now = new Date('2026-09-13T00:15:00Z');
  await reserveAiCall(db, 'known', now);
  await reserveAiCall(db, 'unknown', now);
  await settleAiCall(db, 'known', 6000);
  await markAiCallUncertain(db, 'unknown');
  await assert.rejects(settleAiCall(db, 'known', 1));
  const rows = await db.prepare('SELECT state, reserved_micros, charged_micros FROM ai_calls ORDER BY id').all();
  assert.deepEqual(rows.results, [
    { state: 'settled', reserved_micros: 200000, charged_micros: 6000 },
    { state: 'uncertain', reserved_micros: 200000, charged_micros: null },
  ]);
});

test('monthly accounting follows Beijing time and preserves past month reservations', async () => {
  assert.equal(budgetMonth(new Date('2026-09-30T15:59:59Z')), '2026-09');
  assert.equal(budgetMonth(new Date('2026-09-30T16:00:00Z')), '2026-10');
  await reserveAiCall(db, 'september', new Date('2026-09-30T15:59:59Z'));
  await reserveAiCall(db, 'october', new Date('2026-09-30T16:00:00Z'));
  assert.equal((await db.prepare('SELECT DISTINCT month FROM ai_calls').all()).results.length, 2);
});

test('budget view separates settled, pending and uncertain costs and requires owner login', async () => {
  const now = new Date();
  await reserveAiCall(db, 'paid', now);
  await settleAiCall(db, 'paid', 12345);
  await reserveAiCall(db, 'pending', now);
  await reserveAiCall(db, 'unknown', now);
  await markAiCallUncertain(db, 'unknown');
  await reserveAiCall(db, 'older', new Date('2025-01-01T00:00:00Z'));
  const summary = await getBudget(db, now);
  assert.deepEqual(summary, { month: budgetMonth(now), limitCny: 10, settledCny: 0.012345, heldCny: 0.4,
    availableCny: 9.587655, uncertainCalls: 1, canAnalyze: true });
  const response = await request('/api/budget');
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.deepEqual(await response.json(), summary);
  assert.equal((await request('/api/budget', 'GET', undefined, {}, 'https://app.example.test')).status, 401);
  assert.equal((await getBudget(db, new Date('2027-01-01T00:00:00Z'))).settledCny, 0);
});
