from datetime import datetime, timezone
from unittest.mock import patch
import unittest

from jobs.calendar import latest_closed_session, trading_day, session_lag, calendar_evidence
from jobs.market_context import read_margin

CUTOFF = datetime(2026, 10, 12, 0, tzinfo=timezone.utc)


def context_fixture():
    return dict(calendar=calendar_evidence(CUTOFF), margin=[], valuation=None, limitations=[])


class MarketContextTests(unittest.TestCase):
    def test_holidays_weekends_and_expired_calendar(self):
        from datetime import date
        self.assertFalse(trading_day(date(2026, 10, 6)))
        self.assertFalse(trading_day(date(2026, 10, 10)))
        self.assertEqual(str(latest_closed_session(datetime(2026, 10, 7, tzinfo=timezone.utc))), '2026-09-30')
        self.assertEqual(str(latest_closed_session(CUTOFF)), '2026-10-09')
        self.assertEqual(session_lag('2026-09-30', datetime(2026, 10, 7, tzinfo=timezone.utc)), 0)
        self.assertIsNone(trading_day(date(2027, 1, 5)))
        self.assertIsNone(latest_closed_session(datetime(2027, 1, 5, tzinfo=timezone.utc)))

    def test_us_early_close_and_timezone(self):
        self.assertEqual(str(latest_closed_session(datetime(2026, 11, 27, 19, tzinfo=timezone.utc), 'US')), '2026-11-27')
        self.assertEqual(str(latest_closed_session(datetime(2026, 11, 27, 17, tzinfo=timezone.utc), 'US')), '2026-11-25')

    @patch('jobs.market_context.fetch_json')
    def test_shenzhen_margin_uses_published_units_and_verifies_date(self, fetch):
        metadata = dict(catalogid='1837_xxpl', subname='2026-10-09',
                        cols={key: '(亿元)' for key in ('jrrzye', 'jrrjye', 'jrrzrjye')})
        fetch.return_value = [dict(metadata=metadata, data=[dict(jrrzye='1,000.00', jrrjye='10.00', jrrzrjye='1,010.00')])]
        row = read_margin('SZ', CUTOFF)
        self.assertEqual(row['total'], '101000000000.00')
        metadata['subname'] = '2026-10-08'
        with self.assertRaises(ValueError):
            read_margin('SZ', CUTOFF)
        metadata['subname'] = '2026-10-09'
        metadata['cols']['jrrzye'] = '(元)'
        with self.assertRaises(ValueError):
            read_margin('SZ', CUTOFF)


if __name__ == '__main__':
    unittest.main()
