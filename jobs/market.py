"""Free daily bars and publisher RSS. Missing/old data never becomes live data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from html import unescape
import json
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

BENCHMARKS = {"000001.SH": "上证指数", "399001.SZ": "深证成指", "SPY.US": "SPY（标普500 ETF）", "QQQ.US": "QQQ（纳斯达克100 ETF）"}
FEEDS = [("中新网财经", "https://www.chinanews.com.cn/rss/finance.xml"),
         ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
         ("美联储", "https://www.federalreserve.gov/feeds/press_all.xml")]


def fetch(url):
    with urlopen(Request(url, headers={"User-Agent": "MarketPilotDaily/0.1 (personal research)"}), timeout=20) as response:
        raw = response.read(2000001)
        if len(raw) > 2000000:
            raise ValueError("source_too_large")
        return raw


def number(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("invalid_price")
    return result


def percent(value):
    return str(value.quantize(Decimal("0.01")))


def parse_bars(symbol, data, cutoff):
    columns = [data[key] for key in ("timestamp", "close", "volume", "amount")]
    if len({len(c) for c in columns}) != 1:
        raise ValueError("inconsistent_bars")
    tz = ZoneInfo("America/New_York" if symbol.endswith(".US") else "Asia/Shanghai")
    rows = []
    for stamp, close, volume, amount in zip(*columns):
        day = datetime.fromtimestamp(stamp / 1000, tz).date()
        # Exclude a current session until after the usual close. No intraday quotes.
        local = cutoff.astimezone(tz)
        if day > local.date() or (day == local.date() and local.hour < (17 if symbol.endswith(".US") else 16)):
            continue
        price = number(close)
        if price > 0:
            rows.append((day, price, number(volume), number(amount)))
    rows.sort(key=lambda row: row[0])
    if len(rows) < 2 or len({r[0] for r in rows}) != len(rows):
        raise ValueError("insufficient_or_duplicate_bars")
    current, previous = rows[-1], rows[-2]
    age = (cutoff.astimezone(tz).date() - current[0]).days
    return {"symbol": symbol, "sessionDate": str(current[0]), "previousSessionDate": str(previous[0]),
            "close": str(current[1]), "changePct": percent((current[1] / previous[1] - 1) * 100),
            "amount": str(current[3]) if current[3] > 0 else None,
            "stale": age > 4, "ageDays": age, "adjustment": "none",
            "currency": "USD" if symbol.endswith(".US") else "CNY"}


def read_quote(symbol, cutoff):
    url = "https://free-api.tickflow.org/v1/klines?" + urlencode({"symbol": symbol, "period": "1d", "count": 8, "adjust": "none"})
    try:
        result = parse_bars(symbol, json.loads(fetch(url), parse_float=Decimal)["data"], cutoff)
        return {**result, "sourceUrl": url}
    except Exception:
        return {"symbol": symbol, "missing": True, "sourceUrl": url}


def parse_feed(raw, cutoff):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("unsafe_xml")
    items = []
    for item in ET.fromstring(raw).findall("./channel/item"):
        try:
            stamp = parsedate_to_datetime(item.findtext("pubDate", ""))
            if stamp.tzinfo is None or not cutoff - timedelta(hours=24) <= stamp <= cutoff:
                continue
            url = item.findtext("link", "").strip()
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or any(c.isspace() for c in url):
                continue
            clean = lambda value: unescape(re.sub(r"<[^>]*>", "", value)).strip()
            items.append({"title": clean(item.findtext("title", ""))[:250], "url": url,
                          "summary": clean(item.findtext("description", ""))[:160], "publishedAt": stamp.isoformat()})
        except (ValueError, TypeError):
            continue
    return sorted(items, key=lambda item: item["publishedAt"], reverse=True)


def collect(snapshot, cutoff):
    positions = snapshot["positions"]
    symbols = list(dict.fromkeys([*BENCHMARKS, *(p["symbol"] for p in positions[:20])]))
    with ThreadPoolExecutor(max_workers=3) as pool:
        quotes = list(pool.map(lambda symbol: read_quote(symbol, cutoff), symbols))
    news, coverage = [], []
    for publisher, url in FEEDS:
        try:
            items = parse_feed(fetch(url), cutoff)
            # Prefer relevant company names, then market and policy topics, then recency.
            keywords = [p["name"] for p in positions] + ["股", "金融", "政策", "经济", "market", "stock", "inflation", "Fed", "AI"]
            items.sort(key=lambda item: -sum(word.lower() in item["title"].lower() for word in keywords))
            limit = 5 if publisher != "美联储" else 2
            news.extend({**item, "publisher": publisher} for item in items[:limit])
            coverage.append(f"{publisher}：过去24小时检出{len(items)}条，选入{min(limit, len(items))}条；仅依据RSS摘要。")
        except Exception:
            coverage.append(f"{publisher}：本次获取失败，不代表没有新闻。")
    missing = ["未接入可靠资金净流入、两融、ETF申赎、估值、财报与完整未来事件日历；不能据此判断不存在利空。",
               "未核验完整交易所节假日历；行情为最近可取得日线，开市与交易限制需另行确认。",
               "涨跌为不复权收盘价变化，除权除息可能影响；不代表含分红总回报。"]
    if not positions:
        missing.append("尚未填写真实持仓，本次只能提供市场观察，不能作个人买卖或仓位建议。")
    if len(positions) > 20:
        missing.append("首版最多采集20只持仓的行情，其余未覆盖；不计算全账户仓位。")
    sources = []
    for quote in quotes:
        quote["id"] = f"Q{len(sources)+1}"
        sources.append({"id": quote["id"], "title": BENCHMARKS.get(quote["symbol"], quote["symbol"]) + " 日线",
                        "url": quote["sourceUrl"], "publishedAt": quote.get("sessionDate", "未知")})
    for article in news:
        article["id"] = f"N{len(sources)+1}"
        sources.append({k: article[k] for k in ("id", "title", "url", "publishedAt")})
    return {"quotes": quotes, "news": news, "coverage": coverage, "missing": missing, "sources": sources}


def portfolio_metrics(snapshot, quotes):
    prices = {q["symbol"]: q for q in quotes if not q.get("missing") and not q.get("stale")}
    output = []
    for p in snapshot["positions"]:
        q = prices.get(p["symbol"])
        value = number(p["quantity"]) * number(q["close"]) if q else None
        cost = number(p["averageCost"]) if p["averageCost"] is not None else None
        output.append({"symbol": p["symbol"], "name": p["name"], "currency": p["currency"], "horizon": p["horizon"],
                       "value": str(value) if value is not None else None,
                       "pnlPct": percent((number(q["close"]) / cost - 1) * 100) if q and cost else None,
                       "weightPct": None, "evidenceId": q["id"] if q else None})
    for currency in ("CNY", "USD"):
        group = [item for item in output if item["currency"] == currency]
        cash = snapshot["cash"][currency]
        if cash is not None and all(item["value"] is not None for item in group):
            total = sum((Decimal(item["value"]) for item in group), Decimal(cash))
            if total:
                for item in group:
                    item["weightPct"] = percent(Decimal(item["value"]) / total * 100)
    # Do not send raw quantities, cost basis, or absolute balances to the model/email.
    return [{k: v for k, v in item.items() if k != "value"} for item in output]
