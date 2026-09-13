export interface AuthEnv {
  LOCAL_DEV_AUTH?: string;
  APP_ORIGIN: string;
  GITHUB_CLIENT_ID: string;
  GITHUB_CLIENT_SECRET: string;
  GITHUB_OWNER_ID: string;
  DB: D1Database;
}

const sessionCookie = '__Host-mp_session';
const stateCookie = '__Host-mp_oauth';
const lifetime = 86400;
const opaque = /^[A-Za-z0-9_-]{43}$/;
const now = () => Math.floor(Date.now() / 1000);
const base64url = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
const random = () => base64url(crypto.getRandomValues(new Uint8Array(32)));
const digest = async (value: string) => base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))));
const cookie = (name: string, value: string, age: number) => `${name}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${age}`;

function readCookie(request: Request, name: string): string {
  const matches = (request.headers.get('Cookie') ?? '').split(';').map(value => value.trim()).filter(value => value.startsWith(`${name}=`));
  return matches.length === 1 ? matches[0].slice(name.length + 1) : '';
}

function configured(request: Request, env: AuthEnv) {
  if (!env.APP_ORIGIN || !env.GITHUB_CLIENT_ID || !env.GITHUB_CLIENT_SECRET || !/^[1-9]\d*$/.test(env.GITHUB_OWNER_ID)) {
    throw new HttpError(503, 'GitHub 登录尚未配置，请先完成部署设置。');
  }
  const url = new URL(request.url);
  if (url.protocol !== 'https:' || url.origin !== env.APP_ORIGIN) throw new HttpError(403, '请使用正式网站地址登录。');
}

const loginCss = `*{box-sizing:border-box}body{margin:0;background:#f5f7f4;color:#233b32;font:16px/1.7 system-ui,sans-serif;min-height:100vh;display:grid;place-items:center;padding:24px}main{max-width:460px;width:100%;background:white;border:1px solid #dfe7e1;border-radius:20px;padding:40px}small{color:#60766a;letter-spacing:2px}h1{font-size:28px;margin:22px 0 12px}p{color:#60766a}a{display:block;background:#245c43;color:white;padding:12px 20px;text-align:center;border-radius:10px;text-decoration:none;margin:28px 0 12px}a:focus-visible{outline:3px solid #adceb6;outline-offset:4px}footer{font-size:13px;color:#60766a}`;

function loginPage(message = '登录后，管理你的持仓与投资计划。', status = 200) {
  // Only fixed messages from this module are rendered; no callback query is echoed.
  return new Response(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>登录 · MarketPilotDaily</title><link rel="stylesheet" href="/auth/style.css"><main><small>MARKETPILOT DAILY</small><h1>你的个人投资空间</h1><p>${message}</p><a href="/auth/github">使用 GitHub 登录</a><footer>仅限已授权的个人账号。持仓数据不会公开。</footer></main></html>`, { status, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}

async function githubJson(url: string, init: RequestInit) {
  let response: Response;
  try { response = await fetch(url, { ...init, redirect: 'manual', signal: AbortSignal.timeout(15000) }); }
  catch { throw new HttpError(502, 'GitHub 暂时无法连接，请重新登录。'); }
  if (!response.ok) throw new HttpError(502, 'GitHub 身份验证失败，请重新登录。');
  try { return await response.json() as Record<string, unknown>; }
  catch { throw new HttpError(502, 'GitHub 返回了无效响应，请重新登录。'); }
}

export async function authRoute(request: Request, env: AuthEnv): Promise<Response | null> {
  const { pathname, searchParams } = new URL(request.url);
  if (!pathname.startsWith('/auth/')) return null;
  configured(request, env);
  if (pathname === '/auth/style.css' && request.method === 'GET') return new Response(loginCss, { headers: { 'Content-Type': 'text/css' } });
  if (pathname === '/auth/login' && request.method === 'GET') return loginPage();
  if (pathname === '/auth/github' && request.method === 'GET') {
    const state = random(), verifier = random(), stamp = now();
    await env.DB.batch([
      env.DB.prepare('DELETE FROM oauth_states WHERE expires_at <= ?').bind(stamp),
      env.DB.prepare('DELETE FROM auth_sessions WHERE expires_at <= ?').bind(stamp),
    ]);
    const inserted = await env.DB.prepare(`INSERT INTO oauth_states (state_hash, verifier, expires_at)
      SELECT ?, ?, ? WHERE (SELECT COUNT(*) FROM oauth_states) < 100 RETURNING state_hash`)
      .bind(await digest(state), verifier, stamp + 600).first();
    if (!inserted) throw new HttpError(429, '登录请求过多，请稍后重试。');
    const target = new URL('https://github.com/login/oauth/authorize');
    target.search = new URLSearchParams({ client_id: env.GITHUB_CLIENT_ID, redirect_uri: `${env.APP_ORIGIN}/auth/callback`,
      scope: '', state, code_challenge: await digest(verifier), code_challenge_method: 'S256', allow_signup: 'false' }).toString();
    return new Response(null, { status: 302, headers: { Location: target.href, 'Set-Cookie': cookie(stateCookie, state, 600) } });
  }
  if (pathname === '/auth/callback' && request.method === 'GET') {
    const state = searchParams.get('state') ?? '';
    const code = searchParams.get('code');
    if (!opaque.test(state) || state !== readCookie(request, stateCookie)) return loginPage('登录校验失败，请重新发起登录。', 403);
    const flow = await env.DB.prepare('DELETE FROM oauth_states WHERE state_hash = ? AND expires_at > ? RETURNING verifier')
      .bind(await digest(state), now()).first<{ verifier: string }>();
    if (!flow || !code || code.length > 512 || searchParams.has('error')) return loginPage('登录已取消或已过期，请重新登录。', 403);
    const headers = { Accept: 'application/json', 'User-Agent': 'MarketPilotDaily' };
    const token = await githubJson('https://github.com/login/oauth/access_token', {
      method: 'POST', headers: { ...headers, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ client_id: env.GITHUB_CLIENT_ID, client_secret: env.GITHUB_CLIENT_SECRET,
        code, redirect_uri: `${env.APP_ORIGIN}/auth/callback`, code_verifier: flow.verifier }).toString(),
    });
    if (typeof token.access_token !== 'string' || !token.access_token || token.token_type !== 'bearer' || token.scope !== '') {
      throw new HttpError(502, 'GitHub 授权响应不符合登录权限要求，请重新登录。');
    }
    const user = await githubJson('https://api.github.com/user', { headers: { ...headers, Authorization: `Bearer ${token.access_token}` } });
    if (!Number.isSafeInteger(user.id) || String(user.id) !== env.GITHUB_OWNER_ID) return loginPage('此 GitHub 账号没有访问权限，请切换到已授权账号。', 403);
    const session = random();
    const previous = readCookie(request, sessionCookie);
    await env.DB.batch([
      env.DB.prepare('DELETE FROM auth_sessions WHERE token_hash = ?').bind(await digest(previous)),
      env.DB.prepare('INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)').bind(await digest(session), String(user.id), now() + lifetime),
    ]);
    const reply = new Response(null, { status: 302, headers: { Location: '/' } });
    reply.headers.append('Set-Cookie', cookie(sessionCookie, session, lifetime));
    reply.headers.append('Set-Cookie', cookie(stateCookie, '', 0));
    return reply;
  }
  if (pathname === '/auth/logout' && request.method === 'POST') {
    if (request.headers.get('Origin') !== env.APP_ORIGIN) throw new HttpError(403, '请求来源不匹配。');
    const session = readCookie(request, sessionCookie);
    if (opaque.test(session)) await env.DB.prepare('DELETE FROM auth_sessions WHERE token_hash = ?').bind(await digest(session)).run();
    return Response.json({ loggedOut: true }, { headers: { 'Set-Cookie': cookie(sessionCookie, '', 0) } });
  }
  throw new HttpError(404, '登录接口不存在。');
}

export function isLocal(request: Request, env: AuthEnv) {
  return env.LOCAL_DEV_AUTH === 'true'
    && ['localhost', '127.0.0.1', '[::1]'].includes(new URL(request.url).hostname);
}

export async function authorize(request: Request, env: AuthEnv): Promise<Response | null> {
  if (isLocal(request, env)) return null;
  configured(request, env);
  const value = readCookie(request, sessionCookie);
  if (opaque.test(value)) {
    const session = await env.DB.prepare('SELECT user_id FROM auth_sessions WHERE token_hash = ? AND expires_at > ?')
      .bind(await digest(value), now()).first<{ user_id: string }>();
    if (session?.user_id === env.GITHUB_OWNER_ID) return null;
  }
  if (request.method === 'GET' && new URL(request.url).pathname === '/') return loginPage();
  throw new HttpError(401, '登录已过期或尚未登录，请使用 GitHub 重新登录。');
}

export class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function readBody(request: Request): Promise<unknown> {
  if (request.headers.get('Origin') !== new URL(request.url).origin) {
    throw new HttpError(403, '请求来源不匹配，请从本站页面保存。');
  }
  if (request.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') {
    throw new HttpError(415, '请使用 JSON 提交。');
  }
  if (Number(request.headers.get('Content-Length')) > 16384) {
    throw new HttpError(413, '提交内容过长。');
  }
  const reader = request.body?.getReader();
  if (!reader) throw new HttpError(400, '提交内容为空。');
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > 16384) {
      await reader.cancel();
      throw new HttpError(413, '提交内容过长。');
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try { return JSON.parse(new TextDecoder().decode(bytes)); }
  catch { throw new HttpError(400, 'JSON 格式无效。'); }
}
