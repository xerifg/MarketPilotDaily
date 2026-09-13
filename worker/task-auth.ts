import { createRemoteJWKSet, jwtVerify, type JWTPayload } from 'jose';
import { HttpError, type AuthEnv } from './auth';

const issuer = 'https://token.actions.githubusercontent.com';
const keys = createRemoteJWKSet(new URL(`${issuer}/.well-known/jwks`));
const repo = 'xerifg/MarketPilotDaily';
const repoId = '1365924685';
const ownerId = '24712937';

export function checkTaskClaims(p: JWTPayload): string {
  const subjects = [`repo:${repo}:ref:refs/heads/main`, `repo:xerifg@${ownerId}/MarketPilotDaily@${repoId}:ref:refs/heads/main`];
  if (!subjects.includes(p.sub ?? '') || p.repository !== repo || p.repository_id !== repoId
      || p.repository_owner_id !== ownerId || p.ref !== 'refs/heads/main'
      || p.workflow_ref !== `${repo}/.github/workflows/daily.yml@refs/heads/main`
      || p.runner_environment !== 'github-hosted' || !['schedule', 'workflow_dispatch'].includes(String(p.event_name))
      || !/^\d+$/.test(String(p.run_id)) || typeof p.exp !== 'number') {
    throw new HttpError(403, '任务身份不允许访问。');
  }
  return String(p.run_id);
}

export async function authorizeTask(request: Request, env: AuthEnv): Promise<string> {
  const url = new URL(request.url);
  if (url.protocol !== 'https:' || url.origin !== env.APP_ORIGIN) throw new HttpError(403, '请使用正式任务入口。');
  const header = request.headers.get('Authorization') ?? '';
  if (!/^Bearer [A-Za-z0-9_.-]+$/.test(header) || header.length > 16000) throw new HttpError(401, '任务身份无效。');
  try {
    const { payload } = await jwtVerify(header.slice(7), keys, { issuer, audience: env.APP_ORIGIN,
      algorithms: ['RS256'], maxTokenAge: '10m', requiredClaims: ['exp', 'iat', 'nbf', 'sub'] });
    return checkTaskClaims(payload);
  } catch { throw new HttpError(401, '任务身份无效或已过期。'); }
}
