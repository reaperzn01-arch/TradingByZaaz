"""Swing (fractal) high/low detection + live rolling market extremes."""
from __future__ import annotations

from dataclasses import dataclass

from .candles import Candle


@dataclass
class Pivot:
    t: int          # bar time
    price: float    # high for a pivot-high, low for a pivot-low
    kind: str       # "high" | "low"
    idx: int        # index in the candle list

    def as_dict(self) -> dict:
        return {"t": self.t, "price": self.price, "kind": self.kind, "idx": self.idx}


def find_pivots(candles: list[Candle], lookback: int = 2) -> list[Pivot]:
    """Classic fractal pivots: bar higher/lower than `lookback` bars on each side.

    The most recent `lookback` bars are never confirmed pivots (no right side yet),
    which keeps the live edge honest.
    """
    n = len(candles)
    pivots: list[Pivot] = []
    if n < 2 * lookback + 1:
        return pivots
    for i in range(lookback, n - lookback):
        win = candles[i - lookback : i + lookback + 1]
        hi = candles[i].h
        lo = candles[i].l
        if all(hi >= c.h for c in win) and hi > max(c.h for j, c in enumerate(win) if j != lookback):
            pivots.append(Pivot(candles[i].t, hi, "high", i))
        if all(lo <= c.l for c in win) and lo < min(c.l for j, c in enumerate(win) if j != lookback):
            pivots.append(Pivot(candles[i].t, lo, "low", i))
    pivots.sort(key=lambda p: (p.t, 0 if p.kind == "high" else 1))
    return pivots


def last_pivots(pivots: list[Pivot], kind: str, count: int = 6) -> list[Pivot]:
    return [p for p in pivots if p.kind == kind][-count:]


def live_extremes(candles: list[Candle], now_ts: int, session_start: int) -> dict:
    """Real-time market extremes: forming-bar levels, session high/low, rolling highs."""
    if not candles:
        return {}
    last = candles[-1]
    sess = [c for c in candles if c.t >= session_start]
    day_c = [c for c in candles if c.t >= now_ts - (now_ts % 86400)]
    rolling = candles[-200:]
    hh = max(c.h for c in rolling)
    ll = min(c.l for c in rolling)
    return {
        "last_price": last.c,
        "bar_high": last.h,
        "bar_low": last.l,
        "session_high": max((c.h for c in sess), default=last.h),
        "session_low": min((c.l for c in sess), default=last.l),
        "day_high": max((c.h for c in day_c), default=last.h),
        "day_low": min((c.l for c in day_c), default=last.l),
        "rolling_high_200": hh,
        "rolling_low_200": ll,
    }


def breakout_state(price: float, pivots: list[Pivot], n: int = 3) -> dict:
    """Is live price beyond the most recent swing highs/lows (live market high breaks)?"""
    highs = last_pivots(pivots, "high", n)
    lows = last_pivots(pivots, "low", n)
    res = highs[-1].price if highs else None
    sup = lows[-1].price if lows else None
    state = {"above_last_swing_high": None, "below_last_swing_low": None, "last_swing_high": res, "last_swing_low": sup}
    if res is not None:
        state["above_last_swing_high"] = price > res
    if sup is not None:
        state["below_last_swing_low"] = price < sup
    return state
