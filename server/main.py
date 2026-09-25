"""TradingByZaaz server — FastAPI app wiring feeds, engine and WebSocket broadcast."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure vendor dependencies are in path
vendor_dir = str(Path(__file__).resolve().parent.parent / "vendor_py")
if vendor_dir not in sys.path:
    sys.path.insert(0, vendor_dir)

import asyncio
import contextlib
import json
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .api.ws import Hub
from .config import (
    ANALYSIS_TFS,
    DATA_DIR,
    DEFAULT_SYMBOLS,
    DEFAULT_SETTINGS,
    SYMBOLS,
    TIMEFRAMES,
    WEB_DIR,
    ensure_token,
)
from .engine.brain import SymbolBrain
from .engine.signals import SignalManager
from .market.feed import FeedManager

SETTINGS_PATH = DATA_DIR / "settings.json"
JOURNAL_PATH = DATA_DIR / "journal.json"


def load_settings() -> dict:
    st = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            st.update(json.loads(SETTINGS_PATH.read_text()))
        except Exception:
            pass
    ensure_token(st)
    return st


class Engine:
    """Owns feed, brains, signal manager and the broadcast loops."""

    def __init__(self) -> None:
        self.settings = load_settings()
        self.hub = Hub()
        self.sigman = SignalManager(JOURNAL_PATH)
        self.feed = FeedManager(self.settings, on_candle_close=self.on_candle_close)
        self.brains: dict[str, SymbolBrain] = {}
        self.events: list[dict] = []
        self._dirty = False

    # ------------------------------------------------------------------
    def save_settings(self) -> None:
        SETTINGS_PATH.write_text(json.dumps(self.settings, indent=1))

    def brain_for(self, symbol: str) -> SymbolBrain:
        b = self.brains.get(symbol)
        if not b:
            self.feed.warmup(symbol)
            b = SymbolBrain(symbol, self.feed.series_for(symbol), self.sigman, self.settings)
            self.brains[symbol] = b
        return b

    def on_candle_close(self, symbol: str, tf: str, bar) -> None:
        """Bar-closed hook: age signals, run scans and advance lifecycles."""
        self._dirty = True
        if symbol not in SYMBOLS:
            return
        try:
            brain = self.brain_for(symbol)
            self.sigman.on_bar(symbol, tf)
            for t in (tf, *ANALYSIS_TFS):
                if t not in TIMEFRAMES:
                    continue
                created = brain.scan(t)
                for s in created:
                    self.events.append({"type": "signal_new", "signal": s.as_dict(), "ts": time.time()})
                brain.tick_lifecycle(t)
        except Exception:
            pass

    # ------------------------------------------------------------------
    async def analysis_loop(self) -> None:
        """Scan all symbols every second (live highs touch / break events)."""
        await asyncio.sleep(2.0)
        while True:
            try:
                for symbol in SYMBOLS:
                    if symbol not in self.feed.warm and symbol not in DEFAULT_SYMBOLS:
                        continue
                    brain = self.brain_for(symbol)
                    lt = self.feed.last_ticks.get(symbol)
                    if lt:
                        self.sigman.observe(symbol, lt["price"])
                    for tf in ANALYSIS_TFS:
                        created = brain.scan(tf)
                        for s in created:
                            self.events.append({"type": "signal_new", "signal": s.as_dict(), "ts": time.time()})
                            self._dirty = True
                        changed = brain.tick_lifecycle(tf)
                        for s in changed:
                            self.events.append({"type": "signal_update", "signal": s.as_dict(), "ts": time.time()})
                            self._dirty = True
                self.events = self.events[-40:]
            except Exception:
                pass
            await asyncio.sleep(1.0)

    async def broadcast_loop(self) -> None:
        """Push compact state at 1 Hz and full analysis for subscribed symbols at 2s."""
        n = 0
        while True:
            try:
                n += 1
                prices = {}
                for symbol in SYMBOLS:
                    lt = self.feed.last_ticks.get(symbol)
                    if lt:
                        prices[symbol] = {"price": lt["price"], "ts": lt["ts"], "source": self.feed.current_source(symbol)}
                await self.hub.broadcast(
                    {
                        "type": "pulse",
                        "ts": time.time(),
                        "prices": prices,
                        "signals": [s.as_dict() for s in sorted(self.sigman.open, key=lambda x: (-x.confidence, x.symbol))],
                        "events": self.events[-8:],
                        "stats": self.sigman.stats(),
                        "feed": self.feed.status(),
                    }
                )
                if n % 3 == 0:
                    for symbol in SYMBOLS:
                        if self.hub.subscribed(symbol):
                            try:
                                state = self.brain_for(symbol).full_state()
                                state["source"] = self.feed.current_source(symbol)
                                await self.hub.broadcast({"type": "analysis", "symbol": symbol, "state": state}, symbol=symbol)
                            except Exception:
                                pass
            except Exception:
                pass
            await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    async def run(self) -> None:
        # warm the default watchlist first, then everything else
        for s in DEFAULT_SYMBOLS:
            self.feed.warmup(s)
            self.brain_for(s)
        asyncio.create_task(self._warm_rest())
        await asyncio.gather(self.feed.run(), self.feed.run_twelvedata(), self.analysis_loop(), self.broadcast_loop())

    async def _warm_rest(self) -> None:
        for s in SYMBOLS:
            if s not in self.feed.warm:
                self.feed.warmup(s)
                await asyncio.sleep(0.05)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    engine = Engine()
    app.state.engine = engine
    app.state.settings = engine.settings
    app.state.feed = engine.feed
    app.state.brains = engine.brains
    app.state.sigman = engine.sigman
    app.state.hub = engine.hub
    task = asyncio.create_task(engine.run())
    yield
    task.cancel()


app = FastAPI(title="TradingByZaaz", lifespan=lifespan)
app.include_router(router)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    engine = app.state.engine
    await engine.hub.connect(ws)
    await ws.send_text(
        json.dumps(
            {
                "type": "hello",
                "symbols": list(SYMBOLS),
                "analysis_tfs": ANALYSIS_TFS,
                "timeframes": TIMEFRAMES,
                "settings": {k: v for k, v in engine.settings.items() if k != "ingest_token"},
                "has_token": bool(engine.settings.get("ingest_token")),
            }
        )
    )
    engine.hub.register(ws)   # only now receive broadcasts (hello is guaranteed first)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "subscribe" and msg.get("symbol") in SYMBOLS:
                engine.hub.subscribe(ws, msg["symbol"])
                state = engine.brain_for(msg["symbol"]).full_state()
                state["source"] = engine.feed.current_source(msg["symbol"])
                await ws.send_text(json.dumps({"type": "analysis", "symbol": msg["symbol"], "state": state}, default=str))
    except WebSocketDisconnect:
        engine.hub.disconnect(ws)
    except Exception:
        engine.hub.disconnect(ws)


@app.get("/bridge/mt5_bridge.py")
async def bridge_download():
    return FileResponse(Path(__file__).resolve().parent.parent / "bridge" / "mt5_bridge.py", filename="mt5_bridge.py")


@app.get("/scripts/TradingByZaaz_WhaleConfluence.pine")
async def download_pine():
    p = Path(__file__).resolve().parent.parent / "scripts" / "TradingByZaaz_WhaleConfluence.pine"
    return FileResponse(p, filename="TradingByZaaz_WhaleConfluence.pine", media_type="text/plain")


@app.get("/scripts/TradingByZaaz_WhaleConfluence.mq5")
async def download_mq5():
    p = Path(__file__).resolve().parent.parent / "scripts" / "TradingByZaaz_WhaleConfluence.mq5"
    return FileResponse(p, filename="TradingByZaaz_WhaleConfluence.mq5", media_type="text/plain")


@app.get("/scripts/TradingByZaaz_WhaleConfluence.mq4")
async def download_mq4():
    p = Path(__file__).resolve().parent.parent / "scripts" / "TradingByZaaz_WhaleConfluence.mq4"
    return FileResponse(p, filename="TradingByZaaz_WhaleConfluence.mq4", media_type="text/plain")


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
