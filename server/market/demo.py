"""Realistic synthetic market feed (DEMO mode).

Generates a continuous tick stream with regime switches (trend/range), volatility
 clustering and an intraday session-volatility profile (Asia quiet, London/NY
active) so trendlines, swings and level reactions actually occur.
"""
from __future__ import annotations

import math
import random
import time
import zlib


def zlib_seed(name: str) -> int:
    """Stable across processes (unlike hash()), so demo history is reproducible."""
    return zlib.crc32(name.encode()) & 0xFFFF

from ..config import SYMBOLS
from ..engine.candles import Candle


def session_vol_mult(ts: float) -> float:
    hour = time.gmtime(ts).tm_hour
    if 7 <= hour < 12:      # London morning
        return 1.35
    if 12 <= hour < 16:     # London / NY overlap
        return 1.5
    if 16 <= hour < 21:     # New York
        return 1.15
    if 0 <= hour < 7:       # Asia
        return 0.65
    return 0.45             # off-session


class DemoSymbol:
    def __init__(self, symbol: str, seed: int | None = None):
        meta = SYMBOLS[symbol]
        self.symbol = symbol
        self.digits = meta["digits"]
        self.vol = meta["vol"]            # sigma per 1-minute bar
        self.price = meta["seed"]
        self.rng = random.Random(seed if seed is not None else zlib_seed(symbol))
        self.regime = 0.0                 # drift state in [-1, 1]
        self.regime_until = time.time() + self.rng.uniform(300, 1500)
        self.vol_mult = 1.0
        self._last_t = time.time()

    # ------------------------------------------------------------------
    def _step_drift(self, now: float) -> None:
        if now >= self.regime_until:
            r = self.rng.random()
            if r < 0.45:
                self.regime = 0.0                          # range
            elif r < 0.73:
                self.regime = self.rng.uniform(0.35, 1.0)  # uptrend
            else:
                self.regime = -self.rng.uniform(0.35, 1.0)  # downtrend
            self.regime_until = now + self.rng.uniform(240, 1800)

    def _sigma(self, dt: float, now: float) -> float:
        # mean-reverting vol multiplier with occasional bursts
        self.vol_mult += (1.0 - self.vol_mult) * min(1.0, dt / 900.0)
        if self.rng.random() < 0.002:
            self.vol_mult = self.rng.uniform(1.8, 3.2)
        self.vol_mult = max(0.4, min(3.5, self.vol_mult))
        # vol is RELATIVE per-minute sigma (works for any price scale: gold, crypto ...)
        per_min = self.price * self.vol * session_vol_mult(now) * self.vol_mult
        return per_min * math.sqrt(max(dt, 1e-6) / 60.0)

    def step(self, now: float | None = None) -> tuple[float, float]:
        """Advance the walk to `now`; returns (price, volume)."""
        now = now if now is not None else time.time()
        dt = max(1e-3, now - self._last_t)
        self._last_t = now
        self._step_drift(now)
        sigma = self._sigma(dt, now)
        drift = self.regime * sigma * 0.30            # trend component
        jump = 0.0
        if self.rng.random() < 0.0015:
            jump = self.rng.gauss(0, 3.0) * sigma     # news-ish spike
        self.price += self.rng.gauss(0, sigma) + drift + jump
        # slow anchor reversion keeps demo levels plausible over weeks
        anchor = SYMBOLS[self.symbol]["seed"]
        self.price += (anchor - self.price) * min(0.5, 1e-6 * dt)
        self.price = max(self.price, anchor * 0.90)
        self.price = min(self.price, anchor * 1.10)
        self.price = round(self.price, self.digits)
        return self.price, abs(self.rng.gauss(1.0, 0.4))

    # ------------------------------------------------------------------
    def warmup_minutes(self, minutes: int, bar_secs: int = 60, end_at: int | None = None) -> list[Candle]:
        """Simulate `minutes` of history in `bar_secs` candles ending at `end_at` (default now)."""
        end = int(end_at if end_at is not None else time.time())
        start = end - minutes * 60
        start -= start % bar_secs
        out: list[Candle] = []
        self._last_t = start
        self.price = round(self.price, self.digits)
        t = start
        while t <= end:
            bar = Candle(t, self.price, self.price, self.price, self.price, 0.0)
            for k in range(2):  # 2 sub-steps per bar keeps the shape realistic
                self._last_t = t + (k * bar_secs) // 2
                p, v = self.step(t + ((k + 1) * bar_secs) // 2)
                bar.tick(p, v)
            out.append(bar)
            t += bar_secs
        self._last_t = time.time()
        self.price = out[-1].c
        return out


def warmup_symbol(symbol: str, days: float = 75.0) -> tuple["DemoSymbol", list[Candle], list[Candle]]:
    """Returns (live-walk-instance, coarse M5 history, fine M1 history).

    Coarse history (M5 bars) covers months of structure for previous-week/month
    levels at ~1/5 of the cost; fine M1 covers the last few days for M1 charts.
    """
    ds = DemoSymbol(symbol, zlib_seed(symbol + "w"))
    fine_days = min(days, 5.0)
    now = int(time.time())
    fine_start = now - int(fine_days * 86400)
    coarse = ds.warmup_minutes(int((days - fine_days) * 1440), bar_secs=300, end_at=fine_start)
    fine = ds.warmup_minutes(int(fine_days * 1440), bar_secs=60, end_at=now)
    return ds, coarse, fine
