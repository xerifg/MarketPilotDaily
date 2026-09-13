export interface Source { id: string; title: string; url: string; publishedAt: string }
export interface Quote { id: string; symbol: string; missing?: boolean; close: string; changePct: string; currency: string;
  sessionDate: string; previousSessionDate: string; amount: string | null; stale: boolean }
export interface Analysis { overview: string; today: string; short: string; long: string; watch: string;
  holdings: { symbol: string; advice: string }[] }
export interface Report { title: string; paragraphs: string[]; cutoffAt: string; model: string; estimatedCostCny: string; sources: Source[];
  evidence?: { presentationVersion?: number; analysisStatus?: string; analysisRaw?: Analysis | null;
    quotes?: Quote[]; news?: Source[]; coverage?: string[]; missing?: string[];
    metrics?: { symbol: string; name: string; currency: string; weightPct: string | null; pnlPct: string | null }[] } }
export const benchmarks: Record<string, string> = { '000001.SH': '上证指数', '399001.SZ': '深证成指',
  'SPY.US': 'SPY · 标普 500 ETF', 'QQQ.US': 'QQQ · 纳斯达克 100 ETF' };
export const sections = [['today', '今日', '行动计划'], ['short', '短期', '1–4 周'], ['long', '长期', '6–24 个月']] as const;
export function beijing(value: string) {
  if (value.length === 10) return value;
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value));
}
export function number(value: string | null | undefined, amount = false, signed = false) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '未提供';
  let n = Number(value), unit = '';
  if (amount && Math.abs(n) >= 1e8) { n /= 1e8; unit = '亿'; }
  else if (amount && Math.abs(n) >= 1e4) { n /= 1e4; unit = '万'; }
  return (signed && n > 0 ? '+' : '') + n.toLocaleString('zh-CN', {
    minimumFractionDigits: amount || signed ? 2 : 0, maximumFractionDigits: amount || signed ? 2 : 6 }) + unit;
}
export function readingLines(text: string) {
  const result: string[] = [];
  for (const line of text.split('\n')) {
    let buffer = '';
    for (const sentence of line.split(/(?<=。)/)) {
      buffer += sentence;
      if (buffer.length >= 120) { result.push(buffer.trim()); buffer = ''; }
    }
    if (buffer.trim()) result.push(buffer.trim());
  }
  return result;
}
export function safeUrl(value: string) {
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? value : undefined; }
  catch { return undefined; }
}
