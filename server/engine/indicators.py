"""Pure-Python technical indicators used by the signal engine."""
from __future__ import annotations

import math
from typing import Sequence


def ema(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def sma(values: Sequence[float], period: int) -> list[float]:
    out: list[float] = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        out.append(s / min(i + 1, period))
    return out


def true_ranges(h: Sequence[float], l: Sequence[float], c: Sequence[float]) -> list[float]:
    trs = [h[0] - l[0]]
    for i in range(1, len(c)):
        pc = c[i - 1]
        trs.append(max(h[i] - l[i], abs(h[i] - pc), abs(l[i] - pc)))
    return trs


def atr(h: Sequence[float], l: Sequence[float], c: Sequence[float], period: int = 14) -> list[float]:
    trs = true_ranges(h, l, c)
    return ema(trs, period)


def rsi(c: Sequence[float], period: int = 14) -> list[float]:
    if not c:
        return []
    out = [50.0] * len(c)
    gain = loss = 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        g = max(d, 0.0)
        lo = max(-d, 0.0)
        if i <= period:
            gain += (g - gain) / i
            loss += (lo - loss) / i
        else:
            gain = (gain * (period - 1) + g) / period
            loss = (loss * (period - 1) + lo) / period
        if loss < 1e-12:
            out[i] = 100.0
        else:
            rs = gain / loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def linreg_slope(ys: Sequence[float], xs: Sequence[float] | None = None) -> float:
    """Least-squares slope of ys (optionally against explicit xs)."""
    n = len(ys)
    if n < 2:
        return 0.0
    if xs is None:
        xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def round_price(price: float, digits: int) -> float:
    return round(price + 0.0, digits)


def format_price(price: float, digits: int) -> str:
    return f"{price:.{digits}f}"


def pips(price_diff: float, pip: float) -> float:
    return price_diff / pip if pip else 0.0


def human_rr(entry: float, sl: float, tp: float) -> float:
    risk = abs(entry - sl)
    if risk < 1e-12:
        return 0.0
    return abs(tp - entry) / risk


def stdev(vals: Sequence[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
