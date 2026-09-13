import { test } from 'node:test';
import assert from 'node:assert/strict';
import { beijing, number, readingLines, safeUrl } from '../src/report-format';

test('report formatting distinguishes missing and zero, dates, small prices and large amounts', () => {
  assert.equal(number(null, true), '未提供');
  assert.equal(number('0', true), '0.00');
  assert.equal(number('0.0045'), '0.0045');
  assert.equal(number('958186336970', true), '9,581.86亿');
  assert.equal(number('0.87', false, true), '+0.87');
  assert.equal(beijing('2026-09-11'), '2026-09-11');
  assert.match(beijing('2026-09-12T20:30:00Z'), /2026\/09\/13 04:30/);
});
test('legacy paragraph breaks preserve all content, and unsafe source URLs are not clickable', () => {
  const text = '原始事实[Q1]与失效条件。'.repeat(30);
  const lines = readingLines(text);
  assert.ok(lines.length > 1);
  assert.equal(lines.join(''), text);
  assert.equal(safeUrl('javascript:alert(1)'), undefined);
  assert.equal(safeUrl('https://user:pass@example.com'), undefined);
  assert.equal(safeUrl('https://example.com/?a=1'), 'https://example.com/?a=1');
});

test('missing quote source dates do not crash the report reader', () => {
  for (const value of ['未知', '', 'invalid timestamp']) {
    assert.equal(beijing(value), '时间未知');
  }
});
