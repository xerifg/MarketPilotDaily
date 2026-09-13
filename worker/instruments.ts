import { z } from 'zod';
import type { Instrument } from '../shared/schema';
import { HttpError } from './auth';
import { usAssetType } from './us-directory';

const responseSchema = z.object({ data: z.array(z.object({
  symbol: z.string(), code: z.string(), name: z.string().min(1).max(100),
  exchange: z.string(), region: z.string(), type: z.string(),
})) });

export async function lookupInstrument(symbol: string): Promise<Instrument> {
  let raw: unknown;
  try {
    const response = await fetch(`https://free-api.tickflow.org/v1/instruments?symbols=${encodeURIComponent(symbol)}`, {
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error('Upstream failed');
    raw = await response.json();
  } catch { throw new HttpError(503, '证券资料暂时不可用，请稍后重试；表单尚未保存。'); }
  const parsed = responseSchema.safeParse(raw);
  if (!parsed.success) throw new HttpError(503, '证券资料格式变化，暂时无法核验。');
  const instrument = parsed.data.data.find(item => item.symbol === symbol);
  const isUS = instrument?.exchange === 'US';
  if (!instrument || instrument.region !== (isUS ? 'US' : 'CN')
    || !['stock', 'etf'].includes(instrument.type)
    || !['SH', 'SZ', 'BJ', 'US'].includes(instrument.exchange)
    || `${instrument.code}.${instrument.exchange}` !== symbol) {
    throw new HttpError(422, '未查到支持的 A 股、美股或 ETF，请核对证券代码和市场。');
  }
  return {
    symbol, code: instrument.code, name: instrument.name,
    exchange: instrument.exchange as Instrument['exchange'],
    assetType: isUS ? await usAssetType(instrument.code) : instrument.type as Instrument['assetType'],
    currency: isUS ? 'USD' : 'CNY',
    verifiedAt: new Date().toISOString(),
  };
}
