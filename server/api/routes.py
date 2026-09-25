"""REST API: history, analysis, signals, journal, settings, ingest (Exness bridge)."""
from __future__ import annotations

import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

from ..config import (
    ANALYSIS_TFS,
    DEFAULT_SETTINGS,
    SYMBOLS,
    TIMEFRAMES,
    broker_symbol,
    canonical_symbol,
)

router = APIRouter(prefix="/api")


def _settings(request: Request) -> dict:
    return request.app.state.settings


def _feed(request: Request):
    return request.app.state.feed


def _brains(request: Request) -> dict:
    return request.app.state.brains


def _sigman(request: Request):
    return request.app.state.sigman


def _check_token(request: Request, token: str | None) -> None:
    expected = _settings(request).get("ingest_token") or ""
    if expected and (token or "") != expected:
        raise HTTPException(status_code=401, detail="invalid ingest token")


# ---------------------------------------------------------------------------
@router.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "TradingByZaaz", "ts": time.time()}


@router.get("/symbols")
async def symbols(request: Request) -> dict:
    st = _settings(request)
    out = {}
    for s, meta in SYMBOLS.items():
        out[s] = {**meta, "broker": broker_symbol(s, st)}
    return {"symbols": out, "timeframes": TIMEFRAMES, "analysis_tfs": ANALYSIS_TFS}


@router.get("/feedstatus")
async def feedstatus(request: Request) -> dict:
    feed = _feed(request)
    return {"status": feed.status(), "force_demo": bool(_settings(request).get("force_demo"))}


@router.get("/candles/{symbol}")
async def candles(symbol: str, request: Request, tf: str = "M15", limit: int = 500) -> dict:
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        raise HTTPException(404, f"unknown symbol {symbol}")
    if tf not in TIMEFRAMES:
        raise HTTPException(400, f"unknown timeframe {tf}")
    feed = _feed(request)
    feed.warmup(symbol)  # instant history on first request
    cs = feed.series_for(symbol)[tf]
    limit = max(50, min(limit, 4000))
    data = [c.as_dict() for c in cs.candles[-limit:]]
    return {"symbol": symbol, "tf": tf, "candles": data, "source": feed.current_source(symbol)}


@router.get("/analysis/{symbol}")
async def analysis(symbol: str, request: Request) -> dict:
    symbol = symbol.upper()
    brains = _brains(request)
    brain = brains.get(symbol)
    if not brain:
        raise HTTPException(404, f"unknown symbol {symbol}")
    state = brain.full_state()
    state["source"] = _feed(request).current_source(symbol)
    state["broker_symbol"] = broker_symbol(symbol, _settings(request))
    return state


@router.get("/signals")
async def signals(request: Request) -> dict:
    sm = _sigman(request)
    return {"open": [s.as_dict() for s in sorted(sm.open, key=lambda x: -x.confidence)], "stats": sm.stats()}


@router.get("/journal")
async def journal(request: Request) -> dict:
    sm = _sigman(request)
    return {"journal": list(reversed(sm.journal[-200:]))}


@router.get("/stats")
async def stats(request: Request) -> dict:
    return _sigman(request).stats()


@router.get("/scripts")
async def get_scripts() -> dict:
    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    pine_file = scripts_dir / "TradingByZaaz_WhaleConfluence.pine"
    mq5_file = scripts_dir / "TradingByZaaz_WhaleConfluence.mq5"
    mq4_file = scripts_dir / "TradingByZaaz_WhaleConfluence.mq4"
    return {
        "tradingview": pine_file.read_text(encoding="utf-8") if pine_file.exists() else "",
        "mt5": mq5_file.read_text(encoding="utf-8") if mq5_file.exists() else "",
        "mt4": mq4_file.read_text(encoding="utf-8") if mq4_file.exists() else "",
    }


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    st = _settings(request)
    public = {k: v for k, v in st.items()}
    return {"settings": public, "defaults": DEFAULT_SETTINGS}


@router.post("/settings")
async def update_settings(request: Request, payload: dict) -> dict:
    st = _settings(request)
    for k, v in (payload or {}).items():
        if k in DEFAULT_SETTINGS:
            st[k] = v
    request.app.state.save_settings()
    return {"settings": st}


@router.post("/demo/reset")
async def demo_reset(request: Request) -> dict:
    feed = _feed(request)
    feed.warm.clear()
    feed.demo_syms.clear()
    feed.store.clear()
    feed.last_real.clear()
    feed.source.clear()
    sm = _sigman(request)
    for s in list(SYMBOLS):
        sm.cancel_symbol(s, "demo reset")
    for s in SYMBOLS:
        feed.warmup(s)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Ingest API — used by bridge/mt5_bridge.py on your PC (Exness MT5 terminal)
# ---------------------------------------------------------------------------
@router.post("/ingest/ticks")
async def ingest_ticks(request: Request, payload: dict, x_zaaz_token: str | None = Header(default=None)) -> dict:
    _check_token(request, x_zaaz_token)
    feed = _feed(request)
    raw = payload.get("symbol")
    symbol = feed.map_symbol(raw)
    if not symbol:
        return {"ok": False, "error": f"unmapped symbol {raw}"}
    n = feed.ingest_ticks(symbol, payload.get("ticks") or [])
    return {"ok": True, "symbol": symbol, "accepted": n}


@router.post("/ingest/candles")
async def ingest_candles(request: Request, payload: dict, x_zaaz_token: str | None = Header(default=None)) -> dict:
    _check_token(request, x_zaaz_token)
    feed = _feed(request)
    raw = payload.get("symbol")
    symbol = feed.map_symbol(raw)
    if not symbol:
        return {"ok": False, "error": f"unmapped symbol {raw}"}
    tf = payload.get("tf", "M1")
    n = feed.ingest_candles(symbol, tf, payload.get("candles") or [])
    await request.app.state.hub.broadcast({"type": "feed", "status": feed.status()})
    return {"ok": True, "symbol": symbol, "tf": tf, "accepted": n}
