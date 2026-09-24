"""Previous-market-data levels: PDH/PDL/PDC, weekly/monthly, floor pivots, session extremes."""
from __future__ import annotations

from datetime import datetime, timezone

from .candles import Candle


def _day_key(ts: int) -> tuple:
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return (d.year, d.month, d.day)


def _week_key(ts: int) -> tuple:
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    y, w, _ = d.isocalendar()
    return (y, w)


def _month_key(ts: int) -> tuple:
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return (d.year, d.month)


def _bucketize(candles: list[Candle], keyfn) -> list[tuple[tuple, list[Candle]]]:
    buckets: list[tuple[tuple, list[Candle]]] = []
    cur_key = None
    cur: list[Candle] = []
    for c in candles:
        k = keyfn(c.t)
        if k != cur_key:
            if cur_key is not None:
                buckets.append((cur_key, cur))
            cur_key, cur = k, [c]
        else:
            cur.append(c)
    if cur_key is not None:
        buckets.append((cur_key, cur))
    return buckets


def _ohlc(bars: list[Candle]) -> dict:
    return {
        "o": bars[0].o,
        "h": max(b.h for b in bars),
        "l": min(b.l for b in bars),
        "c": bars[-1].c,
        "start": bars[0].t,
        "end": bars[-1].t,
    }


def previous_period_levels(candles: list[Candle], now_ts: int) -> dict:
    """Derive previous-day / week / month OHLC-based levels from history."""
    out: dict[str, list[dict]] = {"levels": []}
    if len(candles) < 20:
        return out

    def levels_from(bar: dict, tag: str, include_pivots: bool = True) -> list[dict]:
        lv = [
            {"name": f"P{tag}H", "price": bar["h"], "group": f"prev_{tag.lower()}", "kind": "extreme"},
            {"name": f"P{tag}L", "price": bar["l"], "group": f"prev_{tag.lower()}", "kind": "extreme"},
            {"name": f"P{tag}C", "price": bar["c"], "group": f"prev_{tag.lower()}", "kind": "close"},
        ]
        if include_pivots:
            pp = (bar["h"] + bar["l"] + bar["c"]) / 3.0
            r1 = 2 * pp - bar["l"]
            s1 = 2 * pp - bar["h"]
            r2 = pp + (bar["h"] - bar["l"])
            s2 = pp - (bar["h"] - bar["l"])
            r3 = bar["h"] + 2 * (pp - bar["l"])
            s3 = bar["l"] - 2 * (bar["h"] - pp)
            lv += [
                {"name": f"P{tag}PP", "price": pp, "group": f"pivot_{tag.lower()}", "kind": "pivot"},
                {"name": f"P{tag}R1", "price": r1, "group": f"pivot_{tag.lower()}", "kind": "pivot"},
                {"name": f"P{tag}R2", "price": r2, "group": f"pivot_{tag.lower()}", "kind": "pivot"},
                {"name": f"P{tag}R3", "price": r3, "group": f"pivot_{tag.lower()}", "kind": "pivot"},
                {"name": f"P{tag}S1", "price": s1, "group": f"pivot_{tag.lower()}", "kind": "pivot"},
                {"name": f"P{tag}S2", "price": s2, "group": f"pivot_{tag.lower()}", "kind": "pivot"},
                {"name": f"P{tag}S3", "price": s3, "group": f"pivot_{tag.lower()}", "kind": "pivot"},
            ]
        return lv

    day_bars = _bucketize(candles, _day_key)
    week_bars = _bucketize(candles, _week_key)
    month_bars = _bucketize(candles, _month_key)

    # previous = last *completed* bucket (the final bucket is the in-progress one)
    def prev_completed(buckets):
        if len(buckets) >= 2:
            return buckets[-2][1]
        return None

    pd = prev_completed(day_bars)
    if pd:
        out["levels"] += levels_from(_ohlc(pd), "D", include_pivots=True)
        out["prev_day"] = _ohlc(pd)
    pw = prev_completed(week_bars)
    if pw:
        out["levels"] += levels_from(_ohlc(pw), "W", include_pivots=True)
        out["prev_week"] = _ohlc(pw)
    pm = prev_completed(month_bars)
    if pm:
        out["levels"] += levels_from(_ohlc(pm), "M", include_pivots=False)
        out["prev_month"] = _ohlc(pm)

    # current day high/low so far (live market high/low of the day)
    today_bars = [b for b in candles if _day_key(b.t) == _day_key(now_ts)]
    if today_bars:
        out["today"] = _ohlc(today_bars)
        out["levels"] += [
            {"name": "TodayH", "price": out["today"]["h"], "group": "today", "kind": "extreme"},
            {"name": "TodayL", "price": out["today"]["l"], "group": "today", "kind": "extreme"},
        ]
    return out


def session_extremes(candles: list[Candle], now_ts: int, sessions: dict, window_hours: int = 24) -> list[dict]:
    """High/low of each trading session (Asia/London/NewYork) within the lookback window."""
    out = []
    start = now_ts - window_hours * 3600
    for name, (h0, h1) in sessions.items():
        bars = []
        for c in candles:
            if c.t < start:
                continue
            hour = datetime.fromtimestamp(c.t, tz=timezone.utc).hour
            if h0 <= hour < h1:
                bars.append(c)
        if bars:
            out.append(
                {
                    "name": f"{name}H",
                    "price": max(b.h for b in bars),
                    "group": f"session_{name.lower()}",
                    "kind": "session",
                }
            )
            out.append(
                {
                    "name": f"{name}L",
                    "price": min(b.l for b in bars),
                    "group": f"session_{name.lower()}",
                    "kind": "session",
                }
            )
    return out


def merge_levels(*level_lists: list[dict]) -> list[dict]:
    """Concatenate and drop near-duplicates (keep first name)."""
    out: list[dict] = []
    for lst in level_lists:
        for lv in lst:
            if any(abs(o["price"] - lv["price"]) < 1e-12 for o in out):
                continue
            out.append(lv)
    return out
