"""Per-symbol analysis brain: structure -> confluence -> scored signals."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..config import SESSIONS, SYMBOLS, TIMEFRAMES
from . import indicators as ind
from . import levels as lev
from . import swings as sw
from . import trendlines as tl
from .candles import Candle, CandleSeries
from .signals import Signal, SignalManager, build_signal, score_confluence


def _hour(ts: int) -> int:
    return datetime.fromtimestamp(ts, tz=timezone.utc).hour


def session_start_ts(now_ts: int) -> int:
    """Start of the most recent active session window (for session high/low)."""
    hour = datetime.fromtimestamp(ts := now_ts, tz=timezone.utc).hour
    starts = []
    for name, (h0, h1) in SESSIONS.items():
        if h0 <= hour < h1:
            starts.append(h0)
    if not starts:
        # between sessions — use the last session that opened today or yesterday
        opens = [h0 for (h0, _h1) in SESSIONS.values()]
        past = [h for h in opens if h <= hour]
        start_hour = max(past) if past else min(opens)
    else:
        start_hour = min(starts)
    day_start = now_ts - (now_ts % 86400)
    return day_start + start_hour * 3600


class SymbolBrain:
    """Analyzes one symbol across the standard analysis timeframes."""

    def __init__(self, symbol: str, series: dict[str, CandleSeries], sigman: SignalManager, settings: dict):
        self.symbol = symbol
        self.series = series          # tf -> CandleSeries (must contain ANALYSIS_TFS + D1)
        self.sigman = sigman
        self.settings = settings
        self.meta = SYMBOLS[symbol]
        self.last_events: list[dict] = []

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        out = {"symbol": self.symbol, "timeframes": {}, "signals": []}
        for tf, cs in self.series.items():
            last = cs.last
            out["timeframes"][tf] = {
                "bars": len(cs),
                "last": last.as_dict() if last else None,
            }
        out["signals"] = [s.as_dict() for s in self.sigman.open if s.symbol == self.symbol]
        return out

    def analysis(self, tf: str) -> dict:
        """Full structure analysis for one timeframe."""
        cs = self.series.get(tf)
        if not cs or len(cs) < 40:
            return {"tf": tf, "ok": False}
        candles = cs.candles
        # structure window: recent bars only (keeps per-second scans cheap)
        work = candles[-1200:]
        ts, _o, h, l, c = cs.arrays()
        w_ts, _wo, w_h, w_l, w_c = CandleSeries.arrays_of(work)
        now_ts = int(time.time())
        digits = self.meta["digits"]
        pip = self.meta["pip"]
        price = c[-1]

        # long context for previous-period levels: D1 history + this TF's recent bars
        d1 = self.series.get("D1")
        hist = list(d1.candles) if d1 and d1.candles else []
        if hist and candles and hist[-1].t >= candles[0].t:
            hist = [x for x in hist if x.t < candles[0].t]
        ctx = hist + list(work)

        atr = ind.atr(w_h, w_l, w_c, 14)[-1] or price * 0.0005
        rsi = ind.rsi(w_c, 14)[-1]
        e20 = ind.ema(w_c, 20)[-1]
        e50 = ind.ema(w_c, 50)[-1]
        e200 = ind.ema(w_c, 200)[-1]
        trend_up = e50 > e200 and price > e50
        trend_dn = e50 < e200 and price < e50
        bias = "BULLISH" if trend_up else "BEARISH" if trend_dn else "NEUTRAL"

        lookback = int(self.settings.get("swing_lookback", 2))
        pivots = sw.find_pivots(work, lookback)
        highs = sw.last_pivots(pivots, "high", 6)
        lows = sw.last_pivots(pivots, "low", 6)

        tol = float(self.settings.get("trendline_tolerance_atr", 0.30)) * atr
        min_touches = int(self.settings.get("trendline_min_touches", 2))
        tlines = tl.fit_trendlines(pivots, "high", now_ts, tol, min_touches) + tl.fit_trendlines(
            pivots, "low", now_ts, tol, min_touches
        )

        # previous-period levels change slowly — cache per wall-clock minute
        cache_key = f"{self.symbol}:{tf}:{now_ts // 60}"
        if getattr(self, "_lv_cache_key", None) != cache_key:
            self._lv_cache = lev.previous_period_levels(ctx, now_ts)
            self._lv_cache_key = cache_key
        prev_levels = self._lv_cache
        sess_levels = lev.session_extremes(work, now_ts, SESSIONS)
        swing_levels = [
            {"name": f"SwH{i}", "price": p.price, "group": "swing", "kind": "swing"}
            for i, p in enumerate(highs[-3:])
        ] + [
            {"name": f"SwL{i}", "price": p.price, "group": "swing", "kind": "swing"}
            for i, p in enumerate(lows[-3:])
        ]
        all_levels = lev.merge_levels(prev_levels.get("levels", []), sess_levels, swing_levels)

        extremes = sw.live_extremes(work, now_ts, session_start_ts(now_ts))
        bo_state = sw.breakout_state(price, pivots)

        return {
            "tf": tf,
            "ok": True,
            "price": round(price, digits),
            "digits": digits,
            "pip": pip,
            "atr": round(atr, digits),
            "rsi": round(rsi, 1),
            "ema20": round(e20, digits),
            "ema50": round(e50, digits),
            "ema200": round(e200, digits),
            "bias": bias,
            "pivots": [p.as_dict() for p in pivots[-12:]],
            "pivot_highs": [p.as_dict() for p in highs],
            "pivot_lows": [p.as_dict() for p in lows],
            "trendlines": [x.as_dict(ts[-1]) for x in tlines],
            "trendline_objs": tlines,
            "levels": [{**lv, "price": round(lv["price"], digits)} for lv in all_levels],
            "prev": {k: v for k, v in prev_levels.items() if k != "levels"},
            "extremes": {k: round(v, digits) for k, v in extremes.items()},
            "breakout": bo_state,
            "bars": len(cs),
            "last_bar": ts[-1],
        }

    # ------------------------------------------------------------------
    def scan(self, tf: str) -> list[Signal]:
        """Evaluate signal rules for one timeframe; returns newly created signals."""
        a = self.analysis(tf)
        if not a.get("ok"):
            return []
        cs = self.series[tf]
        candles = cs.candles
        if len(candles) < 30:
            return []
        tf_secs = TIMEFRAMES.get(tf, 60)
        last = candles[-1]
        prev = candles[-2] if len(candles) > 1 else last
        price = last.c
        atr = a["atr"] or 1e-9
        digits = a["digits"]
        pip = a["pip"]
        rsi = a["rsi"]
        now_ts = int(time.time())
        bar_closed = (now_ts - last.t) >= TIMEFRAMES.get(tf, 60) - 2

        direction_hint = a["bias"]
        trend_up, trend_dn = direction_hint == "BULLISH", direction_hint == "BEARISH"
        momentum_ok = 30.0 <= rsi <= 70.0

        settings = self.settings
        sl_atr = float(settings.get("sl_atr", 1.2))
        rr = float(settings.get("default_rr", 1.8))
        conf_atr = float(settings.get("confluence_atr", 0.5))
        created: list[Signal] = []

        def near_confluence(price_zone: float) -> tuple[int, str | None, bool]:
            """Count distinct structure types within the confluence radius."""
            count = 0
            closest_name = None
            closest_d = 1e18
            at_extreme = False
            ex = a.get("extremes") or {}
            for key in ("day_high", "day_low", "session_high", "session_low", "rolling_high_200", "rolling_low_200"):
                v = ex.get(key)
                if v and abs(v - price_zone) <= conf_atr * atr:
                    count += 1
                    at_extreme = True
            for lv in a["levels"]:
                d = abs(lv["price"] - price_zone)
                if d <= conf_atr * atr:
                    count += 1
                    if d < closest_d:
                        closest_d, closest_name = d, lv["name"]
            for line in a["trendline_objs"]:
                v = line.value_at(now_ts)
                if abs(v - price_zone) <= conf_atr * atr:
                    count += 1
                    if abs(v - price_zone) < closest_d:
                        closest_d, closest_name = abs(v - price_zone), f"{line.kind} trendline"
            return min(count, 4), closest_name, at_extreme

        # ---- 1) Trendline events -------------------------------------
        prev_price = prev.c
        for line in a["trendline_objs"]:
            event = tl.check_trendline_event(price, last.l, last.h, line, now_ts, atr, prev_price)
            if not event:
                continue
            if event in ("resistance_break", "support_break"):
                direction = "BUY" if event == "resistance_break" else "SELL"
                kind = "TRENDLINE_BREAK"
                structure = line.value_at(now_ts)
            else:
                direction = "SELL" if event == "resistance_reject" else "BUY"
                kind = "TRENDLINE_BOUNCE"
                structure = line.value_at(now_ts)
            conf_base = 48 if kind == "TRENDLINE_BOUNCE" else 52
            extras, lvl_name, at_ext = near_confluence(structure)
            trend_agrees = (direction == "BUY" and trend_up) or (direction == "SELL" and trend_dn)
            if kind == "TRENDLINE_BOUNCE":
                trend_agrees = (direction == "BUY" and not trend_dn) or (direction == "SELL" and not trend_up)
            conf, reasons = score_confluence(conf_base, extras, trend_agrees, momentum_ok, False, at_ext)
            reasons.insert(0, f"{line.kind.capitalize()} trendline {event.replace('_', ' ')} ({line.touches} touches)")
            sig = build_signal(
                self.symbol, tf, kind, direction, price, structure, atr, sl_atr, rr,
                digits, pip, conf, reasons, lvl_name or f"{line.kind} trendline", bar_closed,
                zone_key=f"tl:{line.t0}:{line.kind}",
            )
            if self.sigman.add(sig, tf_secs):
                created.append(sig)

        # ---- 2) Swing-high / swing-low breakout ----------------------
        bo = a["breakout"]
        for name, cond, direction in (
            ("above_last_swing_high", bo.get("above_last_swing_high"), "BUY"),
            ("below_last_swing_low", bo.get("below_last_swing_low"), "SELL"),
        ):
            if not cond:
                continue
            structure = bo["last_swing_high"] if direction == "BUY" else bo["last_swing_low"]
            if structure is None:
                continue
            # only fire when price moved meaningfully beyond the swing (fresh breakout)
            if abs(price - structure) > 0.8 * atr or abs(price - structure) < 0.05 * atr:
                continue
            extras, lvl_name, at_ext = near_confluence(structure)
            trend_agrees = (direction == "BUY" and trend_up) or (direction == "SELL" and trend_dn)
            conf, reasons = score_confluence(50, extras, trend_agrees, momentum_ok, False, at_ext)
            reasons.insert(0, f"Break of last swing {'high' if direction == 'BUY' else 'low'} at {round(structure, digits)}")
            sig = build_signal(
                self.symbol, tf, "SWING_BREAKOUT", direction, price, structure, atr, sl_atr, rr,
                digits, pip, conf, reasons, lvl_name, bar_closed,
                zone_key=f"sw:{direction}",
            )
            if self.sigman.add(sig, tf_secs):
                created.append(sig)

        # ---- 3) Previous-market level reactions ----------------------
        key_levels = [lv for lv in a["levels"] if lv["group"].startswith(("prev_", "pivot_", "today", "session_"))]
        for lv in key_levels:
            lp = lv["price"]
            dist = price - lp
            # BOUNCE: wick pierced the level but close is back on our side
            if direction_hint != "BEARISH" and last.l <= lp + 0.15 * atr and price > lp + 0.35 * atr and dist < 1.2 * atr:
                direction, kind = "BUY", "LEVEL_BOUNCE"
            elif direction_hint != "BULLISH" and last.h >= lp - 0.15 * atr and price < lp - 0.35 * atr and -dist < 1.2 * atr:
                direction, kind = "SELL", "LEVEL_BOUNCE"
            # BREAK: clean push through a major previous-market level
            elif prev.c <= lp and price > lp + 0.25 * atr and price - lp < 1.0 * atr and lv["group"] in ("prev_day", "prev_week", "today"):
                direction, kind = "BUY", "LEVEL_BREAK"
            elif prev.c >= lp and price < lp - 0.25 * atr and lp - price < 1.0 * atr and lv["group"] in ("prev_day", "prev_week", "today"):
                direction, kind = "SELL", "LEVEL_BREAK"
            else:
                continue
            extras, _n, at_ext = near_confluence(lp)
            extras = max(0, extras - 1)  # don't double-count the level itself
            trend_agrees = (direction == "BUY" and trend_up) or (direction == "SELL" and trend_dn)
            near_sess = lv["group"].startswith("session_")
            conf_base = 45 if kind == "LEVEL_BOUNCE" else 47
            conf, reasons = score_confluence(conf_base, extras, trend_agrees, momentum_ok, near_sess, at_ext)
            reasons.insert(0, f"{lv['name']} {kind.split('_')[1].lower()} at {round(lp, digits)} (previous market data)")
            sig = build_signal(
                self.symbol, tf, kind, direction, price, lp, atr, sl_atr, rr,
                digits, pip, conf, reasons, lv["name"], bar_closed,
                zone_key=f"lv:{lv['name']}",
            )
            if self.sigman.add(sig, tf_secs):
                created.append(sig)

        return created

    # ------------------------------------------------------------------
    def tick_lifecycle(self, tf: str) -> list[Signal]:
        cs = self.series.get(tf)
        if not cs or not cs.last:
            return []
        last = cs.last
        now = time.time()
        max_age = int(self.settings.get("signal_max_age_bars", 96))
        self.sigman.observe(self.symbol, last.c)
        return self.sigman.update(self.symbol, tf, last.c, now, max_age)

    def full_state(self) -> dict:
        """Everything the UI needs for one symbol."""
        out = {"symbol": self.symbol, "meta": self.meta, "analyses": {}}
        for tf in self.series:
            a = self.analysis(tf)
            a.pop("trendline_objs", None)
            out["analyses"][tf] = a
        out["signals"] = [s.as_dict() for s in self.sigman.open if s.symbol == self.symbol]
        return out
