"""Candle container + time-series helpers (aggregation, forming bar upkeep)."""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Candle:
    t: int  # bar open time, unix seconds
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0

    def as_dict(self) -> dict:
        return {"t": self.t, "o": self.o, "h": self.h, "l": self.l, "c": self.c, "v": self.v}

    def tick(self, price: float, vol: float = 0.0) -> None:
        self.c = price
        if price > self.h:
            self.h = price
        if price < self.l:
            self.l = price
        self.v += vol


class CandleSeries:
    """Sorted, de-duplicated list of candles for one (symbol, timeframe)."""

    def __init__(self, tf_seconds: int, capacity: int = 6000):
        self.tf = tf_seconds
        self.capacity = capacity
        self.candles: list[Candle] = []

    def __len__(self) -> int:
        return len(self.candles)

    @property
    def last(self) -> Candle | None:
        return self.candles[-1] if self.candles else None

    def arrays(self):
        cs = self.candles
        return self.arrays_of(cs)

    @staticmethod
    def arrays_of(cs):
        return (
            [c.t for c in cs],
            [c.o for c in cs],
            [c.h for c in cs],
            [c.l for c in cs],
            [c.c for c in cs],
        )

    @staticmethod
    def arrays_with_vol_of(cs):
        return (
            [c.t for c in cs],
            [c.o for c in cs],
            [c.h for c in cs],
            [c.l for c in cs],
            [c.c for c in cs],
            [getattr(c, "v", 0.0) for c in cs],
        )

    def upsert(self, candle: Candle) -> Candle:
        cs = self.candles
        if not cs or candle.t > cs[-1].t:
            cs.append(candle)
            return candle
        i = bisect.bisect_left(cs, candle.t, key=lambda c: c.t)
        if i < len(cs) and cs[i].t == candle.t:
            cs[i].h = max(cs[i].h, candle.h)
            cs[i].l = min(cs[i].l, candle.l)
            cs[i].c = candle.c
            cs[i].v = max(cs[i].v, candle.v)
            return cs[i]
        cs.insert(i, candle)
        return candle

    def merge_many(self, candles: Iterable[Candle]) -> None:
        for c in candles:
            self.upsert(c)

    def closed(self) -> list[Candle]:
        """All candles except the still-forming last one (if it is the current bar)."""
        return self.candles[:-1] if self.candles else []


def aggregate(base: list[Candle], tf_seconds: int) -> list[Candle]:
    """Aggregate M1-style base candles into a larger timeframe."""
    out: list[Candle] = []
    bucket: Candle | None = None
    for c in base:
        bt = (c.t // tf_seconds) * tf_seconds
        if bucket is None or bt != bucket.t:
            bucket = Candle(bt, c.o, c.h, c.l, c.c, c.v)
            out.append(bucket)
        else:
            bucket.h = max(bucket.h, c.h)
            bucket.l = min(bucket.l, c.l)
            bucket.c = c.c
            bucket.v += c.v
    return out


def resample(series: list[Candle], base_tf: int, target_tf: int) -> list[Candle]:
    if target_tf == base_tf:
        return list(series)
    return aggregate(series, target_tf)
