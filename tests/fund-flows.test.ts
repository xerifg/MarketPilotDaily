import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { FundFlows } from '../src/FundFlows';
import type { FundFlows as FlowData, FlowRow } from '../src/report-format';

const row: FlowRow = { code: 'BK1', name: '<script>行业</script>', date: '2026-09-16', net: '100000000',
  net5: null, stale: false, sourceIds: ['F1'], sourceUrl: 'https://example.com/flow' };
function fixture(): FlowData {
  return { industry: { status: 'ok', date: row.date, rows: [row], expectedCount: 1, note: '主力为大单统计' },
    etf: { status: 'missing', date: null, rows: [], expectedCount: 9, note: 'ETF净申赎估算' },
    holdings: [{ symbol: 'SPY.US', name: 'SPY', kind: 'etf', flow: null, industryCode: null, note: '美股未覆盖' }],
    fetchedAt: '2026-09-16T22:00:00Z', limitations: ['不混合统计口径'] };
}
test('flow view shows coverage, signed amounts, dates, missing values and safe text', () => {
  const html = renderToStaticMarkup(createElement(FundFlows, { flows: fixture() }));
  for (const value of ['行业资金', 'ETF净申赎', '持仓关联', '2026-09-16', '+1.00亿元', '未提供', '美股未覆盖', '近5日']) assert.ok(html.includes(value));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('本次未取得可用数据，不代表零流入'));
});
test('stale flows remain in history but cannot lead the current rankings', () => {
  const flows = fixture();
  flows.industry.rows = [{ ...row, stale: true, sourceUrl: 'javascript:alert(1)' }];
  const html = renderToStaticMarkup(createElement(FundFlows, { flows }));
  assert.ok(html.includes('数据偏旧'));
  assert.ok(html.includes('没有符合条件的有效数据'));
  assert.ok(!html.includes('href="javascript:'));
});
