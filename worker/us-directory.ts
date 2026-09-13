import { HttpError } from './auth';

const base = 'https://www.nasdaqtrader.com/dynamic/SymDir/';

async function searchDirectory(file: string, code: string): Promise<'stock' | 'etf' | null> {
  let text: string;
  try {
    const response = await fetch(`${base}${file}`, { signal: AbortSignal.timeout(10000), cf: { cacheTtl: 3600 } });
    if (!response.ok) throw new Error('Directory unavailable');
    text = await response.text();
  } catch { throw new HttpError(503, '美股官方证券目录暂时不可用，无法核验股票／ETF 类型，请稍后重试。'); }
  const lines = text.trim().split(/\r?\n/);
  const columns = lines[0].trim().split('|');
  const symbolIndex = columns.indexOf(file === 'nasdaqlisted.txt' ? 'Symbol' : 'ACT Symbol');
  const etfIndex = columns.indexOf('ETF');
  const testIndex = columns.indexOf('Test Issue');
  if (symbolIndex !== 0 || etfIndex < 0 || testIndex < 0 || !lines.at(-1)?.startsWith('File Creation Time:')) {
    throw new HttpError(503, '美股官方证券目录格式变化，暂时无法核验类型。');
  }
  const line = lines.find(item => item.startsWith(`${code}|`));
  if (!line) return null;
  const fields = line.trim().split('|');
  if (fields.length !== columns.length || fields[testIndex] !== 'N' || !['Y', 'N'].includes(fields[etfIndex])) {
    throw new HttpError(422, '该美股证券暂不支持或类型无法核验。');
  }
  return fields[etfIndex] === 'Y' ? 'etf' : 'stock';
}

export async function usAssetType(code: string): Promise<'stock' | 'etf'> {
  // The quote source currently labels some US ETFs as stocks. Use the official ETF flag.
  const first = await searchDirectory('nasdaqlisted.txt', code);
  if (first) return first;
  const second = await searchDirectory('otherlisted.txt', code);
  if (second) return second;
  throw new HttpError(422, '官方上市目录未找到该美股代码，暂不能添加。请核对代码，例如 BRK.B。');
}
