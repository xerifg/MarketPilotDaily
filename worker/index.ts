import { ZodError } from 'zod';
import { addPosition, editPosition, editProfile, editCash, revisionBody, symbol } from '../shared/schema';
import { authorize, authRoute, HttpError, isLocal, readBody, type AuthEnv } from './auth';
import { lookupInstrument } from './instruments';
import { getPortfolio, mutatePortfolio } from './portfolio';
import { reportRoute, taskRoute } from './daily';
import { getBudget } from './ai-budget';

interface Env extends AuthEnv { DB: D1Database; ASSETS: Fetcher }
const revisionCheck = '(SELECT revision FROM portfolio_state WHERE id = 1) = ?';

async function handle(request: Request, env: Env): Promise<Response> {
  if (new URL(request.url).pathname.startsWith('/internal/')) return taskRoute(request, env);
  const authResponse = await authRoute(request, env) ?? await authorize(request, env);
  if (authResponse) return authResponse;
  const reportResponse = await reportRoute(request, env);
  if (reportResponse) return reportResponse;
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method;
  const storage = isLocal(request, env) ? 'local' : 'cloud';
  if (method === 'GET' && path === '/api/budget') {
    return Response.json(await getBudget(env.DB));
  }
  if (method === 'GET' && path === '/api/portfolio') {
    return Response.json(await getPortfolio(env.DB, storage));
  }
  if (method === 'GET' && path === '/api/instruments') {
    return Response.json(await lookupInstrument(symbol.parse(url.searchParams.get('symbol'))));
  }
  if (method === 'POST' && path === '/api/positions') {
    const input = addPosition.parse(await readBody(request));
    const item = await lookupInstrument(input.symbol);
    const statement = env.DB.prepare(`INSERT INTO positions
      (id, symbol, code, name, exchange, asset_type, currency, quantity, average_cost, horizon, thesis, verified_at, updated_at)
      SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE ${revisionCheck} RETURNING id`)
      .bind(crypto.randomUUID(), item.symbol, item.code, item.name, item.exchange, item.assetType, item.currency,
        input.quantity, input.averageCost, input.horizon, input.thesis, item.verifiedAt, new Date().toISOString(), input.revision);
    return Response.json(await mutatePortfolio(env.DB, statement, storage), { status: 201 });
  }
  const positionMatch = /^\/api\/positions\/([a-zA-Z0-9-]+)(\/restore)?$/.exec(path);
  if (positionMatch && ['PATCH', 'DELETE', 'POST'].includes(method)) {
    const [, id, restore] = positionMatch;
    const now = new Date().toISOString();
    let statement: D1PreparedStatement;
    if (method === 'PATCH' && !restore) {
      const input = editPosition.parse(await readBody(request));
      statement = env.DB.prepare(`UPDATE positions SET quantity = ?, average_cost = ?, horizon = ?, thesis = ?, updated_at = ?
        WHERE id = ? AND deleted_at IS NULL AND ${revisionCheck} RETURNING id`)
        .bind(input.quantity, input.averageCost, input.horizon, input.thesis, now, id, input.revision);
    } else if (method === 'DELETE' && !restore) {
      const input = revisionBody.parse(await readBody(request));
      statement = env.DB.prepare(`UPDATE positions SET deleted_at = ?, updated_at = ?
        WHERE id = ? AND deleted_at IS NULL AND ${revisionCheck} RETURNING id`).bind(now, now, id, input.revision);
    } else if (method === 'POST' && restore) {
      const input = revisionBody.parse(await readBody(request));
      const earliest = new Date(Date.now() - 30000).toISOString();
      statement = env.DB.prepare(`UPDATE positions SET deleted_at = NULL, updated_at = ?
        WHERE id = ? AND deleted_at >= ? AND ${revisionCheck} RETURNING id`).bind(now, id, earliest, input.revision);
    } else throw new HttpError(405, '不支持此操作。');
    return Response.json(await mutatePortfolio(env.DB, statement, storage));
  }
  if (method === 'PATCH' && path === '/api/cash') {
    const input = editCash.parse(await readBody(request));
    const now = new Date().toISOString();
    const [amountColumn, timeColumn] = input.currency === 'USD' ? ['cash_usd', 'cash_usd_as_of'] : ['cash', 'cash_as_of'];
    const statement = env.DB.prepare(`UPDATE portfolio_state SET ${amountColumn} = ?, ${timeColumn} = ?, revision = revision + 1,
      updated_at = ? WHERE id = 1 AND revision = ? RETURNING id`).bind(input.cash, now, now, input.revision);
    return Response.json(await mutatePortfolio(env.DB, statement, storage));
  }
  if (method === 'PATCH' && path === '/api/profile') {
    const input = editProfile.parse(await readBody(request));
    const statement = env.DB.prepare(`UPDATE investor_profile SET horizon = ?, max_drawdown = ?, max_position = ?, email_paused = ?
      WHERE id = 1 AND ${revisionCheck} RETURNING id`)
      .bind(input.horizon, input.maxDrawdown, input.maxPosition, Number(input.emailPaused), input.revision);
    return Response.json(await mutatePortfolio(env.DB, statement, storage));
  }
  if (path.startsWith('/api/') || path.startsWith('/internal/')) throw new HttpError(404, '接口尚未开放。');
  if (!['GET', 'HEAD'].includes(method)) throw new HttpError(405, '不支持此操作。');
  return env.ASSETS.fetch(request);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    let response: Response;
    try { response = await handle(request, env); }
    catch (error) {
      const status = error instanceof HttpError ? error.status : error instanceof ZodError ? 400 : 500;
      const message = error instanceof HttpError ? error.message
        : error instanceof ZodError ? error.issues.map(issue => `${issue.path.join('.')}: ${issue.message}`).join('；')
        : '服务暂时不可用，请稍后重试。';
      response = Response.json({ error: message }, { status });
    }
    const headers = new Headers(response.headers);
    headers.set('Cache-Control', 'no-store');
    headers.set('X-Content-Type-Options', 'nosniff');
    headers.set('Referrer-Policy', 'no-referrer');
    headers.set('X-Frame-Options', 'DENY');
    if (!isLocal(request, env)) {
      headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
    }
    return new Response(response.body, { status: response.status, headers });
  },
};
