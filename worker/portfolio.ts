import type { Portfolio, Position, Profile } from '../shared/schema';
import { HttpError } from './auth';

const positionSelect = `SELECT id, symbol, code, name, exchange, asset_type AS assetType,
  currency, quantity, average_cost AS averageCost, horizon, thesis,
  verified_at AS verifiedAt, updated_at AS updatedAt FROM positions WHERE deleted_at IS NULL ORDER BY updated_at DESC, id`;

function snapshotStatements(db: D1Database) {
  return [
    db.prepare(`SELECT revision, cash, cash_as_of AS cashAsOf, cash_usd AS cashUsd,
      cash_usd_as_of AS cashUsdAsOf, updated_at AS updatedAt FROM portfolio_state WHERE id = 1`),
    db.prepare(positionSelect),
    db.prepare('SELECT horizon, max_drawdown AS maxDrawdown, max_position AS maxPosition, email_paused AS emailPaused FROM investor_profile WHERE id = 1'),
  ];
}

function decode(results: D1Result[], storage: Portfolio['storage']): Portfolio {
  const state = results[0].results[0] as unknown as {
    revision: number; updatedAt: string; cash: string | null; cashAsOf: string | null;
    cashUsd: string | null; cashUsdAsOf: string | null;
  };
  const rawProfile = results[2].results[0] as unknown as Profile;
  return { revision: state.revision, updatedAt: state.updatedAt,
    cash: { CNY: state.cash, USD: state.cashUsd }, cashAsOf: { CNY: state.cashAsOf, USD: state.cashUsdAsOf },
    positions: results[1].results as unknown as Position[],
    profile: { ...rawProfile, emailPaused: Boolean(rawProfile.emailPaused) }, storage };
}

export async function getPortfolio(db: D1Database, storage: Portfolio['storage']) {
  return decode(await db.batch(snapshotStatements(db)), storage);
}

// The conditional mutation, SQL trigger, and snapshot reads share a D1 transaction.
export async function mutatePortfolio(db: D1Database, statement: D1PreparedStatement, storage: Portfolio['storage']) {
  let results: D1Result[];
  try { results = await db.batch([statement, ...snapshotStatements(db)]); }
  catch (error) {
    if (error instanceof Error && error.message.includes('UNIQUE constraint failed: positions.symbol')) {
      throw new HttpError(409, '这只证券已有持仓，请编辑现有记录。');
    }
    throw error;
  }
  if (!results[0].results.length) {
    throw new HttpError(409, '数据已变化或撤销已过期。本次修改未保存，请重新加载后核对再提交。');
  }
  return decode(results.slice(1), storage);
}
