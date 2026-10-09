import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Sectors } from '../src/Sectors';
import type { Sectors as SectorData, SectorRow } from '../src/report-format';

const row: SectorRow = { code: 'BK1', name: '<script>示例行业</script>', kind: 'industry', date: '2026-09-16', stale: false,
  changePct: '2', returns: { '1': '2', '5': '3' }, relative: { '5': '1' }, windows: { '5': { start: '2026-09-09', end: '2026-09-16' } },
  amount: '100000000', net: '-20000000', net5: null, advancers: 10, decliners: 2,
  sourceIds: ['S1', 'F1'], sourceUrl: 'https://example.com/sector', reasons: ['持仓关联'], holdings: ['600001.SH'] };
function fixture(): SectorData {
  return { rows: [row], selectedCodes: ['BK1'], detailCodes: ['BK1'], date: row.date, benchmarkDate: row.date,
    expectedCount: 2, note: '按走势与资金筛选；ETF单列', limitations: ['不复权价格变化'], fetchedAt: '2026-09-17T00:00:00Z',
    holdings: [{ symbol: '600001.SH', name: '示例持仓', code: 'BK1', note: '同源行业已核验' },
      { symbol: 'SPY.US', name: 'SPY', code: null, note: '尚未核验' }] };
}
test('sector view shows dates, windows, breadth, unknown values, mapping and cited analysis', () => {
  const html = renderToStaticMarkup(createElement(Sectors, { sectors: fixture(),
    analysis: [{ code: 'BK1', text: '驱动待核实，下一次收盘后复查。[S1]', email: '观察。[S1]' }],
    sources: [{ id: 'S1', title: '板块', url: 'https://example.com/sector', publishedAt: row.date! }] }));
  for (const value of ['板块分析', '2026-09-16', '2026-09-09', '近20日', '未提供', '百分点', '10 / 2', 'SPY', '未关联', '驱动待核实', '下一次收盘后复查']) assert.ok(html.includes(value), value);
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('role="region"'));
  assert.ok(html.includes('scope="col"'));
});
test('missing analysis and stale ETF flow dates are explicit, and unsafe links are omitted', () => {
  const sectors = fixture();
  sectors.rows = [{ ...row, kind: 'etf', tracking: '示例指数', trackingUrl: 'javascript:alert(1)', sourceUrl: 'javascript:alert(1)',
    flowDate: '2026-09-15', flowStale: true, stale: true }];
  const html = renderToStaticMarkup(createElement(Sectors, { sectors, sources: [] }));
  assert.ok(html.includes('未生成可用的板块解读'));
  assert.ok(html.includes('2026-09-15'));
  assert.ok(html.includes('日期不一致'));
  assert.ok(!html.includes('href="javascript:'));
});
test('empty sector coverage has a clear state without fabricating a ranking', () => {
  const sectors = { ...fixture(), rows: [], selectedCodes: [], detailCodes: [], holdings: [], date: null, benchmarkDate: null };
  const html = renderToStaticMarkup(createElement(Sectors, { sectors, sources: [] }));
  assert.ok(html.includes('无可用的重点板块数据'));
  assert.ok(html.includes('尚无持仓'));
  assert.ok(html.includes('未知'));
});
