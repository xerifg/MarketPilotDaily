import { z } from 'zod';

export const horizonLabels = { today: '短线', short: '短期（1–4 周）', long: '长期（6–24 个月）' };
export const horizon = z.enum(['today', 'short', 'long']).nullable();
export const decimal = z.string().trim().regex(/^(0|[1-9]\d{0,11})(\.\d{1,6})?$/, '请输入非负数字，最多 12 位整数、6 位小数')
  .transform(value => value.includes('.') ? value.replace(/0+$/, '').replace(/\.$/, '') : value);
export const revision = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
export const symbol = z.string().trim().toUpperCase().max(32)
  .regex(/^(?:\d{6}\.(?:SH|SZ|BJ)|[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?\.US)$/, '请输入完整代码，例如 510300.SH、AAPL.US 或 BRK.B.US');
export const currency = z.enum(['CNY', 'USD']);
export type Currency = z.infer<typeof currency>;
export const currencyLabels: Record<Currency, string> = { CNY: '人民币', USD: '美元' };
export const positionFields = z.object({
  quantity: decimal.refine(value => /[1-9]/.test(value), '持仓数量必须大于 0'),
  averageCost: decimal.nullable(),
  horizon,
  thesis: z.string().trim().max(1000, '买入理由最多 1000 字'),
});
export const addPosition = positionFields.extend({ symbol, revision }).strict();
export const editPosition = positionFields.extend({ revision }).strict();
export const revisionBody = z.object({ revision }).strict();
const percentage = decimal.refine(value => Number(value) <= 100, '百分比不能超过 100').nullable();
export const profileFields = z.object({
  horizon,
  maxDrawdown: percentage,
  maxPosition: percentage,
  emailPaused: z.boolean(),
});
export const editProfile = profileFields.extend({ revision }).strict();
export const editCash = z.object({ currency, cash: decimal.nullable(), revision }).strict();

export interface Instrument {
  symbol: string;
  code: string;
  name: string;
  exchange: 'SH' | 'SZ' | 'BJ' | 'US';
  assetType: 'stock' | 'etf';
  currency: Currency;
  verifiedAt: string;
}
export type PositionFields = z.infer<typeof positionFields>;
export interface Position extends Instrument, PositionFields { id: string; updatedAt: string }
export type Profile = z.infer<typeof profileFields>;
export interface Portfolio {
  revision: number;
  positions: Position[];
  cash: Record<Currency, string | null>;
  cashAsOf: Record<Currency, string | null>;
  profile: Profile;
  updatedAt: string;
  storage: 'local' | 'cloud';
}
