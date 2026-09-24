"""Trendline fitting through swing pivots + breakout / bounce detection."""
from __future__ import annotations

from dataclasses import dataclass, field

from .swings import Pivot


@dataclass
class Trendline:
    kind: str            # "support" (drawn on pivot lows) | "resistance" (pivot highs)
    t0: int              # anchor time
    p0: float            # anchor price
    slope: float         # price per second
    touches: int
    quality: float       # 0..1 score
    pivots: list[dict] = field(default_factory=list)
    broken: bool = False
    break_time: int | None = None

    def value_at(self, t: int) -> float:
        return self.p0 + self.slope * (t - self.t0)

    def end(self, t_end: int) -> int:
        return max(t_end, self.t0)

    def as_dict(self, t_end: int) -> dict:
        t1 = self.end(t_end)
        return {
            "kind": self.kind,
            "t0": self.t0,
            "p0": self.p0,
            "t1": t1,
            "p1": self.value_at(t1),
            "slope": self.slope,
            "touches": self.touches,
            "quality": round(self.quality, 3),
            "pivots": self.pivots,
            "broken": self.broken,
            "break_time": self.break_time,
        }


def _fit_line(p1: Pivot, p2: Pivot) -> tuple[float, float]:
    dt = p2.t - p1.t
    if dt == 0:
        return 0.0, p1.price
    slope = (p2.price - p1.price) / dt
    return slope, p1.price


def fit_trendlines(
    pivots: list[Pivot],
    kind: str,
    now_ts: int,
    tol: float,
    min_touches: int = 2,
    max_lines: int = 3,
) -> list[Trendline]:
    """Fit candidate trendlines through recent same-type pivots.

    Every pair of pivots defines a line; lines are scored by how many other pivots
    sit within `tol` (touches). Recency-weighted quality picks the winners.
    """
    cand = [p for p in pivots if p.kind == kind][-8:]
    if len(cand) < 2:
        return []
    lines: list[Trendline] = []
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            p1, p2 = cand[i], cand[j]
            slope, p0 = _fit_line(p1, p2)
            # reject wild slopes (more than tol per bar-time would leave the frame)
            t_min, t_max = min(p1.t, p2.t), max(p1.t, p2.t)
            if t_max - t_min <= 0:
                continue
            span = abs(slope) * (now_ts - t_min)
            if span > tol * 40:
                continue
            touches = 0
            touched = []
            for p in cand:
                val = p0 + slope * (p.t - p1.t)
                if abs(p.price - val) <= tol:
                    touches += 1
                    touched.append({"t": p.t, "price": p.price})
            if touches < min_touches:
                continue
            # recency weight: prefer lines anchored on recent pivots
            recency = (p2.t - cand[0].t + 1) / (cand[-1].t - cand[0].t + 1)
            quality = (touches / len(cand)) * 0.7 + recency * 0.3
            tl = Trendline(
                kind=kind,
                t0=p1.t,
                p0=p1.price,
                slope=slope,
                touches=touches,
                quality=quality,
                pivots=touched,
            )
            lines.append(tl)

    # de-duplicate near-identical lines (same value window), keep best quality
    lines.sort(key=lambda x: (-x.quality, -x.touches, -abs(x.slope)))
    picked: list[Trendline] = []
    for tl in lines:
        ok = True
        for pk in picked:
            v1 = tl.value_at(now_ts)
            v2 = pk.value_at(now_ts)
            if abs(v1 - v2) < tol * 0.8:
                ok = False
                break
        if ok:
            picked.append(tl)
        if len(picked) >= max_lines:
            break
    return picked


def check_trendline_event(
    price: float,
    bar_low: float,
    bar_high: float,
    tl: Trendline,
    now_ts: int,
    atr_val: float,
    prev_price: float,
) -> str | None:
    """Classify the current interaction with a trendline.

    Returns one of: 'resistance_break', 'support_break', 'resistance_reject',
    'support_reject' (bounce), or None.
    """
    line = tl.value_at(now_ts)
    buf = 0.15 * atr_val
    if tl.kind == "resistance":
        if prev_price <= line and price > line + buf:
            return "resistance_break"
        if bar_high >= line - 0.25 * atr_val and price < line - 0.30 * atr_val:
            return "resistance_reject"
    else:
        if prev_price >= line and price < line - buf:
            return "support_break"
        if bar_low <= line + 0.25 * atr_val and price > line + 0.30 * atr_val:
            return "support_reject"
    return None
