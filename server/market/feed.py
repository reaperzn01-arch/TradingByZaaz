"""Feed manager: merges DEMO simulation with live Exness MT5 bridge / Twelve Data.

Source priority per symbol: ingested real data (last 60s) > Twelve Data > DEMO.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

from ..config import SYMBOLS, TIMEFRAMES, ANALYSIS_TFS, canonical_symbol
from ..engine.candles import Candle, CandleSeries
from .demo import DemoSymbol, warmup_symbol


class FeedManager:
    def __init__(self, settings: dict, on_candle_close: Callable | None = None):
        self.settings = settings
        self.on_candle_close = on_candle_close
        self.store: dict[str, dict[str, CandleSeries]] = {}
        self.demo_syms: dict[str, DemoSymbol] = {}
        self.last_real: dict[str, float] = {}
        self.source: dict[str, str] = {}
        self.last_ticks: dict[str, dict] = {}
        self.warm: set[str] = set()
        self._tasks: list[asyncio.Task] = []
        self.tfs = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

    # ------------------------------------------------------------------
    def series_for(self, symbol: str) -> dict[str, CandleSeries]:
        if symbol not in self.store:
            caps = {"M1": 20000, "M5": 22000, "M15": 12000, "M30": 8000, "H1": 6000, "H4": 4000, "D1": 1500}
            self.store[symbol] = {tf: CandleSeries(TIMEFRAMES[tf], caps.get(tf, 6000)) for tf in self.tfs}
        return self.store[symbol]

    def current_source(self, symbol: str) -> str:
        if self.settings.get("force_demo"):
            return "demo"
        last = self.last_real.get(symbol, 0)
        if time.time() - last < 60:
            return self.source.get(symbol, "realtime")
        return "demo"

    def status(self) -> dict:
        out = {}
        for s in SYMBOLS:
            out[s] = {
                "source": self.current_source(s),
                "warmed": s in self.warm,
                "last_tick": self.last_ticks.get(s),
                "price": self.last_ticks.get(s, {}).get("price"),
            }
        return out

    # ------------------------------------------------------------------
    def warmup(self, symbol: str, days: float = 75.0) -> None:
        """Build instant history from the demo walk (replaced later by real backfill)."""
        if symbol in self.warm:
            return
        ds, coarse, fine = warmup_symbol(symbol, days)
        self._apply_bars(symbol, "M5", coarse, from_real=False)
        self._apply_m1(symbol, fine, from_real=False)
        self.warm.add(symbol)
        self.demo_syms[symbol] = ds   # continue the SAME walk live (no jump to seed)

    def _apply_bars(self, symbol: str, tf: str, bars: list[Candle], from_real: bool) -> None:
        """Merge a batch of `tf` bars into `tf` and every larger timeframe."""
        if not bars:
            return
        series = self.series_for(symbol)
        t0, t1 = bars[0].t, bars[-1].t + TIMEFRAMES[tf]
        if from_real:
            # replace (not blend with) demo bars inside the ingested window
            for t_, cs in series.items():
                cs.candles = [c for c in cs.candles if not (t0 <= c.t <= t1)]
        else:
            cs = series[tf]
            cs.candles = [c for c in cs.candles if not (t0 <= c.t <= t1)]
        series[tf].merge_many(bars)
        base = TIMEFRAMES[tf]
        for tf2 in self.tfs:
            secs = TIMEFRAMES[tf2]
            if secs <= base:
                continue
            buckets: dict[int, Candle] = {}
            for c in bars:
                bt = (c.t // secs) * secs
                b = buckets.get(bt)
                if b is None:
                    buckets[bt] = Candle(bt, c.o, c.h, c.l, c.c, c.v)
                else:
                    b.h = max(b.h, c.h)
                    b.l = min(b.l, c.l)
                    b.c = c.c
                    b.v += c.v
            series[tf2].merge_many(buckets[k] for k in sorted(buckets))

    def _apply_m1(self, symbol: str, m1_bars: list[Candle], from_real: bool) -> None:
        """Merge an M1 batch into every timeframe *incrementally* (long history is kept)."""
        series = self.series_for(symbol)
        if from_real and m1_bars:
            # replace (not blend with) demo bars inside the ingested window
            t0, t1 = m1_bars[0].t, m1_bars[-1].t + 60
            for tf, cs in series.items():
                cs.candles = [c for c in cs.candles if not (t0 <= c.t <= t1)]
        series["M1"].merge_many(m1_bars)
        for tf in self.tfs[1:]:
            secs = TIMEFRAMES[tf]
            buckets: dict[int, Candle] = {}
            for c in m1_bars:
                bt = (c.t // secs) * secs
                b = buckets.get(bt)
                if b is None:
                    buckets[bt] = Candle(bt, c.o, c.h, c.l, c.c, c.v)
                else:
                    b.h = max(b.h, c.h)
                    b.l = min(b.l, c.l)
                    b.c = c.c
                    b.v += c.v
            series[tf].merge_many(buckets[k] for k in sorted(buckets))

    # ------------------------------------------------------------------
    # Tick path (used by BOTH the demo loop and the Exness bridge ingest)
    # ------------------------------------------------------------------
    def on_tick(self, symbol: str, price: float, ts: float | None = None, volume: float = 0.0, source: str = "ingest") -> None:
        if symbol not in SYMBOLS:
            return
        ts = ts if ts is not None else time.time()
        self.series_for(symbol)
        closed: list[tuple[str, Candle]] = []
        for tf, secs in [("M1", 60), ("M5", 300), ("M15", 900), ("M30", 1800), ("H1", 3600), ("H4", 14400), ("D1", 86400)]:
            cs = self.store[symbol][tf]
            bt = int(ts // secs) * secs
            last = cs.last
            if last is not None and last.t == bt:
                last.tick(price, volume)
                bar = last
            else:
                if last is not None and last.t < bt:
                    closed.append((tf, last))
                bar = Candle(bt, price, price, price, price, volume)
                cs.upsert(bar)
        self.last_ticks[symbol] = {"price": price, "ts": ts, "source": source}
        if source in ("ingest", "twelvedata"):
            self.last_real[symbol] = time.time()   # freshness = wall clock, not tick time
            self.source[symbol] = "exness-mt5" if source == "ingest" else "twelvedata"
        if closed and self.on_candle_close:
            for tf, bar in closed:
                self.on_candle_close(symbol, tf, bar)

    # ------------------------------------------------------------------
    def ingest_ticks(self, symbol: str, ticks: list[dict]) -> int:
        """ticks: [{t: unix_sec|ms, bid, ask, last}] from the Exness MT5 bridge."""
        n = 0
        for tk in ticks:
            t = float(tk.get("t", 0))
            if t > 1e12:
                t = t / 1000.0
            price = tk.get("last") or tk.get("bid") or tk.get("price")
            if price is None:
                continue
            self.on_tick(symbol, float(price), t or None, source="ingest")
            n += 1
        return n

    def ingest_candles(self, symbol: str, tf: str, candles: list[dict]) -> int:
        """Backfill/merge bars from the bridge (MT5 CopyRates). candles: [{t,o,h,l,c,v}]."""
        if tf not in TIMEFRAMES:
            return 0
        cs = self.series_for(symbol)[tf]
        parsed = []
        for c in candles:
            t = int(float(c.get("t", 0)))
            if t > 1e12:
                t //= 1000
            parsed.append(Candle(t, float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c.get("v", 0))))
        if not parsed:
            return 0
        parsed.sort(key=lambda c: c.t)
        if tf == "M1":
            self._apply_m1(symbol, parsed, from_real=True)
        else:
            t0, t1 = parsed[0].t, parsed[-1].t + TIMEFRAMES[tf]
            cs.candles = [x for x in cs.candles if not (t0 <= x.t <= t1)]
            cs.merge_many(parsed)
        self.last_real[symbol] = time.time()
        self.source[symbol] = "exness-mt5"
        return len(parsed)

    def map_symbol(self, broker_name: str) -> str | None:
        return canonical_symbol(broker_name, self.settings)

    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Demo tick loop (paused per-symbol when real data is flowing).

        Uses a virtual clock so `demo_speed` > 1 accelerates simulated time.
        """
        vt = time.time()
        last_wall = vt
        while True:
            try:
                speed = max(0.1, float(self.settings.get("demo_speed", 1.0)))
                wall = time.time()
                vt += (wall - last_wall) * speed
                last_wall = wall
                for symbol, ds in list(self.demo_syms.items()):
                    if self.current_source(symbol) != "demo":
                        continue
                    p, v = ds.step(vt)
                    self.on_tick(symbol, p, vt, v, source="demo")
                await asyncio.sleep(0.4 / min(speed, 8.0))
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1.0)

    async def run_twelvedata(self) -> None:
        """Optional real-time poller when a Twelve Data API key is configured."""
        import httpx

        while True:
            await asyncio.sleep(5.0)
            key = self.settings.get("twelvedata_key") or ""
            if not key or self.settings.get("force_demo"):
                continue
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    for symbol in list(SYMBOLS)[:]:
                        td_symbol = symbol[:3] + "/" + symbol[3:] if len(symbol) == 6 else symbol
                        r = await client.get(
                            "https://api.twelvedata.com/price",
                            params={"symbol": td_symbol, "apikey": key},
                        )
                        if r.status_code == 200:
                            data = r.json()
                            if "price" in data:
                                self.on_tick(symbol, float(data["price"]), source="twelvedata")
            except Exception:
                continue
