import settings from '../config/ai.json';

const monthlyLimit = Math.round(Number(settings.monthlyBudgetCny) * 1_000_000);
const reservation = Math.round(Number(settings.reservePerCallCny) * 1_000_000);

export function budgetMonth(now: Date): string {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit' }).formatToParts(now);
  return `${parts.find(part => part.type === 'year')!.value}-${parts.find(part => part.type === 'month')!.value}`;
}

// Called only by the authenticated task API. No budget endpoint is public.
export async function reserveAiCall(db: D1Database, id: string, now = new Date()): Promise<boolean> {
  if (!/^[a-zA-Z0-9-]{1,80}$/.test(id)) throw new Error('Invalid call ID');
  const month = budgetMonth(now);
  const stamp = now.toISOString();
  const result = await db.prepare(`INSERT INTO ai_calls (id, month, state, reserved_micros, created_at, updated_at)
    SELECT ?, ?, 'reserved', ?, ?, ?
    WHERE NOT EXISTS (SELECT 1 FROM ai_calls WHERE id = ?)
    AND COALESCE((SELECT SUM(CASE WHEN state = 'settled' THEN charged_micros ELSE reserved_micros END)
      FROM ai_calls WHERE month = ?), 0) + ? <= ? RETURNING id`)
    .bind(id, month, reservation, stamp, stamp, id, month, reservation, monthlyLimit).all();
  return result.results.length === 1;
}

export async function settleAiCall(db: D1Database, id: string, chargedMicros: number): Promise<void> {
  if (!Number.isSafeInteger(chargedMicros) || chargedMicros < 0) throw new Error('Invalid usage cost');
  const row = await db.prepare("UPDATE ai_calls SET state = 'settled', charged_micros = ?, updated_at = ? WHERE id = ? AND state = 'reserved' RETURNING id")
    .bind(chargedMicros, new Date().toISOString(), id).first();
  if (!row) throw new Error('Call is missing or already finalized');
}

export async function markAiCallUncertain(db: D1Database, id: string): Promise<void> {
  await db.prepare("UPDATE ai_calls SET state = 'uncertain', updated_at = ? WHERE id = ? AND state = 'reserved'")
    .bind(new Date().toISOString(), id).run();
}
