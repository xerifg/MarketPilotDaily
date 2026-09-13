export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method, credentials: 'same-origin', redirect: 'error',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch { throw new ApiError(0, '连接失败或登录已过期。表单已保留；请恢复连接或重新登录后核对保存结果。'); }
  if (!response.headers.get('Content-Type')?.includes('application/json')) {
    throw new ApiError(response.status, '未收到数据响应，请重新登录后重试。');
  }
  const data = await response.json() as T & { error?: string };
  if (!response.ok) throw new ApiError(response.status, data.error || '请求失败，请重试。');
  return data as T;
}
