"""Verified exchange calendars have an explicit coverage window; unknown years stay unknown."""
from datetime import date, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

CALENDAR = json.loads((Path(__file__).resolve().parents[1] / 'config/calendar.json').read_text(encoding='utf-8'))


def trading_day(day, market='CN'):
    value = day.isoformat()
    if not CALENDAR['start'] <= value <= CALENDAR['end']:
        return None
    if day.weekday() >= 5:
        return False
    if market == 'US':
        return value not in CALENDAR['usClosures']
    return not any(start <= value <= end for start, end in CALENDAR['cnClosures'])


def latest_closed_session(cutoff, market='CN'):
    local = cutoff.astimezone(ZoneInfo('America/New_York' if market == 'US' else 'Asia/Shanghai'))
    day = local.date()
    close_hour = 14 if market == 'US' and day.isoformat() in CALENDAR['usEarlyCloses'] else 17 if market == 'US' else 16
    if local.hour < close_hour:
        day -= timedelta(days=1)
    for _ in range(32):
        status = trading_day(day, market)
        if status is None:
            return None
        if status:
            return day
        day -= timedelta(days=1)
    return None


def session_lag(value, cutoff, market='CN'):
    latest = latest_closed_session(cutoff, market)
    day = date.fromisoformat(value)
    if latest is None or trading_day(day, market) is None:
        return None
    count = 0
    while day < latest:
        day += timedelta(days=1)
        count += bool(trading_day(day, market))
    return count


def calendar_evidence(cutoff):
    today = cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date()
    upcoming = [dict(start=start, end=end, title='美联储FOMC会议', sourceId='D1')
                for start, end in CALENDAR['fedMeetings'] if today.isoformat() <= end][:2]
    return dict(verifiedOn=CALENDAR['verifiedOn'], coverageEnd=CALENDAR['end'],
                chinaSession=str(latest_closed_session(cutoff)) if latest_closed_session(cutoff) else None,
                usSession=str(latest_closed_session(cutoff, 'US')) if latest_closed_session(cutoff, 'US') else None,
                events=upcoming, sourceUrls=[CALENDAR[key] for key in ('cnSource', 'szSource', 'usSource', 'fedSource')],
                limitation='日历覆盖2026年沪深、美股常规休市和美联储计划会议；临时停市、公司财报日期及其他宏观发布未完整覆盖。')
