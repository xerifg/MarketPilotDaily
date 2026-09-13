import { afterEach, before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { build } from 'esbuild';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

const origin = 'https://app.example.test';
const sha = (value: string) => createHash('sha256').update(value).digest('base64url');
let script: string, mf: Miniflare, db: D1Database;
let userId: number, upstreamMode: string, tokenRequests: number, verifier: string;
let expectedChallenge: string;

before(async () => {
  script = (await build({ entryPoints: ['worker/index.ts'], bundle: true, write: false, format: 'esm', platform: 'browser', target: 'es2022' })).outputFiles[0].text;
});
beforeEach(async () => {
  userId = 24712937; upstreamMode = ''; tokenRequests = 0; verifier = ''; expectedChallenge = '';
  mf = new Miniflare(convertV4MiniflareOptions({
    modules: true, script, compatibilityDate: '2026-09-11', d1Databases: ['DB'],
    bindings: { APP_ORIGIN: origin, GITHUB_CLIENT_ID: 'test-client', GITHUB_CLIENT_SECRET: 'test-secret', GITHUB_OWNER_ID: '24712937' },
    serviceBindings: { ASSETS: () => new Response('Private app') },
    outboundService: async request => {
      const url = new URL(request.url);
      if (url.href === 'https://github.com/login/oauth/access_token') {
        tokenRequests++;
        const body = new URLSearchParams(await request.text());
        verifier = body.get('code_verifier')!;
        assert.equal(sha(verifier), expectedChallenge);
        assert.equal(body.get('redirect_uri'), `${origin}/auth/callback`);
        assert.equal(body.get('client_secret'), 'test-secret');
        if (upstreamMode === 'failure') return new Response('private upstream details', { status: 500 });
        if (upstreamMode === 'malformed') return new Response('<html>unexpected</html>');
        return Response.json({ access_token: 'private-test-token', token_type: 'bearer', scope: upstreamMode === 'scope' ? 'repo' : '' });
      }
      assert.equal(url.href, 'https://api.github.com/user', 'No unmocked network access');
      assert.equal(request.headers.get('Authorization'), 'Bearer private-test-token');
      return Response.json({ id: userId, login: 'name-may-change' });
    },
  }));
  db = await mf.getD1Database('DB') as unknown as D1Database;
  const sql = await readFile('migrations/0004_github_auth.sql', 'utf8');
  await db.batch(sql.trim().split(/;\s*(?=CREATE\b)/).map(statement => db.prepare(statement)));
});
afterEach(async () => { await mf?.dispose(); });

function request(path: string, cookie = '', method = 'GET', headers: Record<string, string> = {}) {
  return mf.dispatchFetch(`${origin}${path}`, { method, redirect: 'manual', headers: { Cookie: cookie, ...headers } });
}
async function start() {
  const reply = await request('/auth/github?returnTo=https://attacker.test');
  assert.equal(reply.status, 302);
  const target = new URL(reply.headers.get('Location')!);
  assert.equal(target.origin + target.pathname, 'https://github.com/login/oauth/authorize');
  assert.equal(target.searchParams.get('scope'), '');
  assert.equal(target.searchParams.get('code_challenge_method'), 'S256');
  assert.equal(target.searchParams.get('redirect_uri'), `${origin}/auth/callback`);
  expectedChallenge = target.searchParams.get('code_challenge')!;
  const state = target.searchParams.get('state')!;
  const header = reply.headers.get('Set-Cookie')!;
  assert.match(header, /HttpOnly; Secure; SameSite=Lax; Max-Age=600/);
  return { state, cookie: header.split(';')[0], path: `/auth/callback?state=${state}&code=temporary-code` };
}
async function signIn() {
  const flow = await start();
  const reply = await request(flow.path, flow.cookie);
  assert.equal(reply.status, 302, reply.status === 302 ? '' : await reply.text());
  assert.equal(reply.headers.get('Location'), '/');
  const headers = reply.headers.getSetCookie();
  const session = headers.find(value => value.startsWith('__Host-mp_session='))!;
  assert.match(session, /Path=\/; HttpOnly; Secure; SameSite=Lax; Max-Age=86400/);
  return { ...flow, session: session.split(';')[0] };
}

test('owner signs in with state and PKCE; only a hash of the session is stored', async () => {
  const flow = await signIn();
  assert.equal(await (await request('/', flow.session)).text(), 'Private app');
  const rows = await db.prepare('SELECT * FROM auth_sessions').all();
  assert.equal(rows.results.length, 1);
  assert.equal(rows.results[0].token_hash, sha(flow.session.split('=')[1]));
  assert.equal(rows.results[0].user_id, '24712937');
  assert.equal((await db.prepare('SELECT * FROM oauth_states').all()).results.length, 0);
  assert.equal((await request(flow.path, flow.cookie)).status, 403);
  assert.equal(tokenRequests, 1);
  assert.equal((await request('/internal/runs/claim', flow.session)).status, 404);
});

test('missing, mismatched, expired, duplicated and replayed OAuth state cannot exchange a token', async () => {
  const flow = await start();
  assert.equal((await request(flow.path)).status, 403);
  assert.equal((await request(flow.path, '__Host-mp_oauth=' + 'x'.repeat(43))).status, 403);
  assert.equal((await request(flow.path, `${flow.cookie}; ${flow.cookie}`)).status, 403);
  await db.prepare('UPDATE oauth_states SET expires_at = 0').run();
  assert.equal((await request(flow.path, flow.cookie)).status, 403);
  assert.equal(tokenRequests, 0);
});

test('cancelling authorization consumes state and does not create a session', async () => {
  const flow = await start();
  const reply = await request(`/auth/callback?state=${flow.state}&error=access_denied`, flow.cookie);
  assert.equal(reply.status, 403); assert.equal(tokenRequests, 0);
  assert.equal((await request(flow.path, flow.cookie)).status, 403);
});

test('a different GitHub account cannot create a session', async () => {
  const flow = await start(); userId = 999;
  const reply = await request(flow.path, flow.cookie);
  assert.equal(reply.status, 403); assert.match(await reply.text(), /没有访问权限/);
  assert.equal((await db.prepare('SELECT * FROM auth_sessions').all()).results.length, 0);
});

test('upstream failure and unexpected permission scopes fail without leaking tokens', async () => {
  for (const mode of ['failure', 'malformed', 'scope']) {
    const flow = await start(); upstreamMode = mode;
    const reply = await request(flow.path, flow.cookie);
    assert.equal(reply.status, 502);
    assert.doesNotMatch(await reply.text(), /test-secret|private-test-token|private upstream/);
  }
  assert.equal((await db.prepare('SELECT * FROM auth_sessions').all()).results.length, 0);
});

test('expired, forged, duplicate cookies and changed owner cannot access private assets', async () => {
  const flow = await signIn();
  assert.equal((await request('/assets/app.js', '__Host-mp_session=' + 'x'.repeat(43))).status, 401);
  assert.equal((await request('/assets/app.js', `${flow.session}; ${flow.session}`)).status, 401);
  await db.prepare("UPDATE auth_sessions SET user_id = '999'").run();
  assert.equal((await request('/assets/app.js', flow.session)).status, 401);
  await db.prepare("UPDATE auth_sessions SET user_id = '24712937', expires_at = 0").run();
  assert.equal((await request('/assets/app.js', flow.session)).status, 401);
});

test('logout requires same origin and revokes the server session immediately', async () => {
  const flow = await signIn();
  assert.equal((await request('/auth/logout', flow.session)).status, 404);
  assert.equal((await request('/auth/logout', flow.session, 'POST', { Origin: 'https://attacker.test' })).status, 403);
  assert.equal(await (await request('/', flow.session)).text(), 'Private app');
  const reply = await request('/auth/logout', flow.session, 'POST', { Origin: origin });
  assert.equal(reply.status, 200); assert.deepEqual(await reply.json(), { loggedOut: true });
  assert.match(reply.headers.get('Set-Cookie')!, /Max-Age=0/);
  assert.equal((await request('/assets/app.js', flow.session)).status, 401);
});

test('alternate hosts fail closed, login responses are not cached and old flows are cleaned up', async () => {
  assert.equal((await mf.dispatchFetch('https://preview.example.test/auth/github', { redirect: 'manual' })).status, 403);
  await db.prepare("INSERT INTO oauth_states VALUES ('old', 'old', 0)").run();
  await db.prepare("INSERT INTO auth_sessions VALUES ('old', '24712937', 0)").run();
  await start();
  assert.equal(await db.prepare("SELECT * FROM oauth_states WHERE state_hash = 'old'").first(), null);
  assert.equal(await db.prepare("SELECT * FROM auth_sessions WHERE token_hash = 'old'").first(), null);
  const reply = await request('/');
  assert.equal(reply.headers.get('Cache-Control'), 'no-store');
  assert.equal(reply.headers.get('Referrer-Policy'), 'no-referrer');
  assert.match(reply.headers.get('Content-Security-Policy')!, /frame-ancestors 'none'/);
});
