"""Finance-Tools: echte Kurse, niemals erfundene Zahlen."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from nexus.omega import ratelimit
from nexus.omega.tools import _http


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _yahoo(symbol: str) -> dict | None:
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + _http.quote(symbol) + "?range=1d&interval=5m")
    resp = _http.request("GET", url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    data = resp.get("json") or {}
    try:
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta") or {}
        price = _num(meta.get("regularMarketPrice"))
        if price is None:
            closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            vals = [v for v in (_num(x) for x in closes) if v is not None]
            price = vals[-1] if vals else None
        if price is None:
            return None
        return {"symbol": meta.get("symbol") or symbol, "price": price,
                "previous_close": _num(meta.get("previousClose") or meta.get("chartPreviousClose")),
                "currency": meta.get("currency"), "exchange": meta.get("exchangeName"),
                "market_time": meta.get("regularMarketTime"), "source": "Yahoo Finance chart"}
    except (AttributeError, IndexError, TypeError):
        return None


def _stooq(symbol: str) -> dict | None:
    sym = str(symbol or "").strip()
    if "." not in sym:
        sym = sym.lower() + ".us"
    url = "https://stooq.com/q/l/?s=" + _http.quote(sym) + "&f=sd2t2ohlcv&h&e=csv"
    resp = _http.request("GET", url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    if not resp.get("success"):
        return None
    rows = list(csv.DictReader(io.StringIO(resp.get("body") or "")))
    if not rows:
        return None
    row = {str(k).strip().lower(): v for k, v in rows[0].items()}
    close = _num(row.get("close"))
    if close is None:
        return None
    return {"symbol": row.get("symbol") or sym, "price": close,
            "open": _num(row.get("open")), "high": _num(row.get("high")),
            "low": _num(row.get("low")), "volume": _num(row.get("volume")),
            "date": row.get("date"), "time": row.get("time"),
            "currency": None, "source": "Stooq"}


def stock_quote(params: dict, ctx: dict) -> dict:
    symbol = str(params.get("symbol") or "").strip().upper()
    if not symbol:
        return {"success": False, "status": "INVALID_PARAMS", "error": "symbol_required"}
    key = f"omega:quote:{symbol}"
    cached = ratelimit.cache_get(key)
    if cached is not None:
        return {**cached, "cached": True}
    tried = []
    quote = None
    try:
        import yfinance as yf  # type: ignore

        tried.append("yfinance")
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        price = _num(getattr(info, "last_price", None))
        if price is not None:
            quote = {"symbol": symbol, "price": price,
                     "previous_close": _num(getattr(info, "previous_close", None)),
                     "currency": getattr(info, "currency", None),
                     "exchange": getattr(info, "exchange", None),
                     "source": "yfinance"}
    except Exception:
        pass
    if quote is None:
        tried.append("Yahoo chart")
        quote = _yahoo(symbol)
    if quote is None:
        tried.append("Stooq")
        quote = _stooq(symbol)
    if quote is None:
        return {"success": False, "status": "NO_DATA",
                "error": f"Keine Live-Kursdaten fuer {symbol} (versucht: {', '.join(tried)})",
                "tried": tried}
    quote["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    quote["tried"] = tried
    result = {"success": True, "status": "OK", **quote}
    ratelimit.cache_set(key, result, 60)
    return result


HANDLERS = {"stock_quote": stock_quote}
