"""Read-only public-source check; no portfolio, AI calls, report writes or mail."""
from datetime import datetime, timezone
import json

from jobs.fund_flows import collect_flows
from jobs.sectors import collect_sectors
from jobs.settings import SETTINGS
from jobs.market_context import collect_context


def main():
    cutoff = datetime.now(timezone.utc)
    positions = [dict(symbol=SETTINGS['watchlist']['etfReferenceCode'] + '.SH', name='ETF', assetType='etf'),
                 dict(symbol='159915.SZ', name='深市ETF公开检查', assetType='etf')]
    flows = collect_flows(positions, cutoff)
    sectors = collect_sectors(positions, flows, cutoff)
    summary = {key: dict(status=group['status'], date=group['date'], count=len(group['rows']),
                         expected=group['expectedCount']) for key, group in [('industry', flows['industry']), ('etf', flows['etf'])]}
    summary['history'] = dict(benchmarkDate=sectors['benchmarkDate'],
                              selected=len(sectors['selectedCodes']),
                              complete=sum(len(row['returns']) == 3 and len(row['relative']) == 3 and not row['stale']
                                           for row in sectors['rows'] if row['code'] in sectors['selectedCodes']))
    context = collect_context(cutoff)
    summary['margin'] = dict(count=len(context['margin']), fresh=sum(not row['stale'] for row in context['margin']))
    summary['valuation'] = dict(date=(context.get('valuation') or {}).get('date'),
                                fresh=bool(context.get('valuation')) and not context['valuation']['stale'])
    print(json.dumps(summary))
    healthy = all(group['status'] == 'ok' and group['rows'] and not any(row['stale'] for row in group['rows'])
                  for group in (flows['industry'], flows['etf']))
    healthy = healthy and summary['history']['complete'] == summary['history']['selected'] > 0
    healthy = healthy and summary['margin']['fresh'] == 2 and summary['valuation']['fresh']
    return 0 if healthy else 1


if __name__ == '__main__':
    raise SystemExit(main())
