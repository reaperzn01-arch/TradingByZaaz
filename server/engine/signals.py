"""Signal construction, confluence scoring and lifecycle management."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Signal:
    id: str
    symbol: str
    tf: str
    kind: str            # TRENDLINE_BOUNCE | TRENDLINE_BREAK | SWING_BREAKOUT | LEVEL_BOUNCE | LEVEL_BREAK | WHALE_SWEEP
    direction: str       # BUY | SELL
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float = 0.0
    confidence: int = 50 # 0..100
    reasons: list[str] = field(default_factory=list)
    created: float = 0.0
    bar_closed: bool = True
    status: str = "ACTIVE"   # ACTIVE | TP1_HIT | TP2_HIT | SL_HIT | EXPIRED | CANCELLED
    best_status: str = "ACTIVE"
    closed: float | None = None
    level_name: str | None = None
    digits: int = 5
    pip: float = 0.0001
    price_now: float = 0.0
    age_bars: int = 0
    track_high: float = 0.0   # post-entry extremes only (never the pre-entry wick)
    track_low: float = 0.0
    dedupe_key: str = ""
    whale_flow: str = ""
    rvol: float = 1.0

    def as_dict(self) -> dict:
        d = asdict(self)
        risk = max(abs(self.entry - self.sl), 1e-12)
        d["rr1"] = round(abs(self.tp1 - self.entry) / risk, 2)
        d["rr2"] = round(abs(self.tp2 - self.entry) / risk, 2)
        if self.tp3:
            d["rr3"] = round(abs(self.tp3 - self.entry) / risk, 2)
        return d


def score_confluence(
    base: int,
    extra_structures: int,
    trend_agrees: bool,
    momentum_ok: bool,
    near_session_level: bool,
    at_extreme: bool,
    whale_agrees: bool = False,
    rvol_val: float = 1.0,
) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = base
    if whale_agrees:
        score += 18
        reasons.append(f"Whale volume surge ({rvol_val:.1f}x RVOL) confirms institutional footprint")
    if extra_structures > 0:
        score += min(extra_structures, 3) * 8
        reasons.append(f"{extra_structures}x confluence structure(s) in zone")
    if trend_agrees:
        score += 8
        reasons.append("Higher-timeframe EMA trend agrees")
    if momentum_ok:
        score += 5
        reasons.append("Momentum / RSI in healthy range")
    if near_session_level:
        score += 4
        reasons.append("Sitting on a session high/low")
    if at_extreme:
        score += 4
        reasons.append("Price at live market extreme (liquidity sweep zone)")
    score = max(5, min(98, score))
    return score, reasons


def build_signal(
    symbol: str,
    tf: str,
    kind: str,
    direction: str,
    entry: float,
    structure: float,
    atr_val: float,
    sl_atr: float,
    rr: float,
    digits: int,
    pip: float,
    confidence: int,
    reasons: list[str],
    level_name: str | None,
    bar_closed: bool,
    zone_key: str | None = None,
    whale_flow: str = "",
    rvol_val: float = 1.0,
) -> Signal:
    sign = 1 if direction == "BUY" else -1
    # Low Loss Margin: Tight Stop Loss anchored strictly to structure/wick + tight ATR buffer
    sl = structure - sign * max(0.5 * atr_val, sl_atr * atr_val)
    risk = max(abs(entry - sl), atr_val * 0.4)
    # High Win Margin: Asymmetric R-multiples (TP1 2.0R, TP2 3.5R, TP3 5.0R)
    tp1 = entry + sign * risk * max(rr, 2.0)
    tp2 = entry + sign * risk * max(rr + 1.5, 3.5)
    tp3 = entry + sign * risk * max(rr + 3.0, 5.0)
    return Signal(
        id=uuid.uuid4().hex[:10],
        symbol=symbol,
        tf=tf,
        kind=kind,
        direction=direction,
        entry=round(entry, digits),
        sl=round(sl, digits),
        tp1=round(tp1, digits),
        tp2=round(tp2, digits),
        tp3=round(tp3, digits),
        confidence=confidence,
        reasons=reasons,
        created=time.time(),
        bar_closed=bar_closed,
        level_name=level_name,
        digits=digits,
        pip=pip,
        price_now=round(entry, digits),
        track_high=entry,
        track_low=entry,
        dedupe_key=f"{symbol}|{tf}|{kind}|{direction}|{zone_key or level_name or round(structure / max(atr_val, 1e-9), 1)}",
        whale_flow=whale_flow,
        rvol=round(rvol_val, 2),
    )


_RANK = {"ACTIVE": 0, "EXPIRED": 1, "SL_HIT": 2, "TP1_HIT": 3, "TP2_HIT": 4, "CANCELLED": 1}


class SignalManager:
    """Owns open signals + the persistent trade journal."""

    def __init__(self, journal_path: Path):
        self.open: list[Signal] = []
        self.journal_path = journal_path
        self.journal: list[dict] = []
        self.cooldown: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if self.journal_path.exists():
            try:
                self.journal = json.loads(self.journal_path.read_text())
            except Exception:
                self.journal = []

    def _save(self) -> None:
        try:
            self.journal_path.write_text(json.dumps(self.journal[-2000:], indent=1))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def add(self, sig: Signal, tf_seconds: int = 60) -> bool:
        now = time.time()
        # 1) dedupe against currently open signals
        for s in self.open:
            if s.dedupe_key == sig.dedupe_key:
                return False
            if (
                s.symbol == sig.symbol
                and s.tf == sig.tf
                and s.kind == sig.kind
                and s.direction == sig.direction
                and abs(s.entry - sig.entry) <= max(1e-9, abs(sig.entry - sig.sl) * 0.35)
            ):
                return False
            # cross-timeframe: same setup seen on another TF (one card per setup)
            if (
                s.symbol == sig.symbol
                and s.kind == sig.kind
                and s.direction == sig.direction
                and now - s.created < 300
                and abs(s.entry - sig.entry) <= max(1e-9, abs(s.entry - s.sl) * 0.6)
            ):
                return False
        # 2) cooldown: don't spam the same setup
        cd = max(3 * tf_seconds, 180.0)
        last = self.cooldown.get(sig.dedupe_key, 0.0)
        if now - last < cd:
            return False
        self.cooldown[sig.dedupe_key] = now
        if len(self.cooldown) > 500:
            cutoff = now - 86400
            self.cooldown = {k: v for k, v in self.cooldown.items() if v > cutoff}
        self.open.append(sig)
        return True

    def observe(self, symbol: str, price: float) -> None:
        """Feed every live price so post-entry extremes (SL/TP triggers) are honest."""
        for s in self.open:
            if s.symbol != symbol:
                continue
            s.price_now = price
            if price > s.track_high:
                s.track_high = price
            if price < s.track_low:
                s.track_low = price

    def on_bar(self, symbol: str, tf: str) -> None:
        for s in self.open:
            if s.symbol == symbol and s.tf == tf:
                s.age_bars += 1

    def update(self, symbol: str, tf: str, price: float, now: float, max_age: int) -> list[Signal]:
        """Advance lifecycle for one symbol/tf; returns signals that changed state.

        Uses post-entry tracked extremes only (track_high/track_low fed by observe),
        so the wick that *formed* the setup can never instantly stop it out.
        """
        changed: list[Signal] = []
        for s in list(self.open):
            if s.symbol != symbol or s.tf != tf:
                continue
            s.price_now = price
            hi, lo = s.track_high, s.track_low
            if s.direction == "BUY":
                hit_tp2, hit_tp1, hit_sl = hi >= s.tp2, hi >= s.tp1, lo <= s.sl
            else:
                hit_tp2, hit_tp1, hit_sl = lo <= s.tp2, lo <= s.tp1, hi >= s.sl

            def apply(status: str, close: bool) -> None:
                s.status = status
                if _RANK.get(status, 0) > _RANK.get(s.best_status, 0):
                    s.best_status = status
                changed.append(s)
                if close:
                    self._close(s)

            if s.status == "TP1_HIT":
                # runner to TP2; once TP1 is banked a retrace through SL just books it
                if hit_tp2:
                    apply("TP2_HIT", close=True)
                elif hit_sl:
                    s.reasons = s.reasons + ["TP1 banked, runner stopped at breakeven-ish — counted as TP1 win"]
                    apply("TP1_HIT", close=True)
                elif s.age_bars > max_age:
                    apply("EXPIRED", close=True)   # best_status keeps the TP1 win
            else:  # ACTIVE
                if hit_sl:
                    apply("SL_HIT", close=True)
                elif hit_tp2:
                    apply("TP2_HIT", close=True)
                elif hit_tp1:
                    apply("TP1_HIT", close=False)  # milestone — keep running to TP2
                elif s.age_bars > max_age:
                    apply("EXPIRED", close=True)
        return changed

    def _close(self, s: Signal) -> None:
        s.closed = time.time()
        if s in self.open:
            self.open.remove(s)
        self.journal.append(s.as_dict())
        self._save()

    def cancel_symbol(self, symbol: str, reason: str) -> None:
        for s in list(self.open):
            if s.symbol == symbol:
                s.status = "CANCELLED"
                s.reasons = s.reasons + [reason]
                self._close(s)

    def stats(self) -> dict:
        closed = [j for j in self.journal if j.get("status") in ("TP1_HIT", "TP2_HIT", "SL_HIT", "EXPIRED")]

        def result(j: dict) -> str:
            best = j.get("best_status") or j.get("status")
            st = j.get("status")
            if best in ("TP1_HIT", "TP2_HIT") or st in ("TP1_HIT", "TP2_HIT"):
                return "WIN"
            if st == "SL_HIT":
                return "LOSS"
            return "FLAT"

        wins = [j for j in closed if result(j) == "WIN"]
        losses = [j for j in closed if result(j) == "LOSS"]
        expired = [j for j in closed if result(j) == "FLAT"]
        by_kind: dict[str, dict] = {}
        for j in closed:
            k = j.get("kind", "?")
            b = by_kind.setdefault(k, {"closed": 0, "wins": 0, "losses": 0})
            b["closed"] += 1
            r = result(j)
            if r == "WIN":
                b["wins"] += 1
            elif r == "LOSS":
                b["losses"] += 1
        for k, b in by_kind.items():
            b["win_rate"] = round(100.0 * b["wins"] / b["closed"], 1) if b["closed"] else 0.0
        n = len(closed)
        return {
            "closed": n,
            "wins": len(wins),
            "losses": len(losses),
            "expired": len(expired),
            "win_rate": round(100.0 * len(wins) / n, 1) if n else 0.0,
            "open_now": len(self.open),
            "by_kind": by_kind,
        }
