#!/usr/bin/env python3
"""
TradingByZaaz — Exness MT5 live-feed bridge
===========================================

Runs on YOUR PC (Windows) next to your Exness MetaTrader 5 terminal and streams
live ticks + historical bars into your TradingByZaaz app over its HTTPS ingest
API. This is the real-time path: Exness has no public market-data API, so MT5
itself is the source of truth for live prices.

Setup (once):
    pip install MetaTrader5 requests
    1. Open your Exness MT5 terminal and log in.
    2. In the TradingByZaaz web UI: Settings -> copy the "ingest token".
    3. Run:

    python mt5_bridge.py --url https://<your-app-host> --token <ingest-token>

Options:
    --url        Base URL of your TradingByZaaz app (the preview/deploy URL)
    --token      Ingest token from the web UI settings
    --symbols    Comma list of MT5 symbols (default: auto-detect from the app catalog)
    --suffix     Broker suffix to strip, e.g. "m" for EURUSDm (or set it in the web UI)
    --terminal   Path to terminal64.exe if MT5 is not auto-detected
    --tf         Timeframes to backfill (default: M1,M5,M15,M30,H1,H4,D1)
    --bars       Bars of history per timeframe (default: 3000)

The bridge is fail-safe: it batches ticks (~2/sec) and re-sends history on
reconnect. Ctrl+C to stop.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import deque

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

# MT5 timeframe constants (avoid importing MetaTrader5 at module import time)
TF_MAP = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
}

DEFAULT_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY",
    "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "US30", "NAS100",
]


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def mt5_connect(terminal: str | None):
    try:
        import MetaTrader5 as mt5
    except ImportError:
        sys.exit("Missing dependency: pip install MetaTrader5  (Windows only)")
    ok = mt5.initialize(path=terminal) if terminal else mt5.initialize()
    if not ok:
        sys.exit(f"MT5 initialize failed: {mt5.last_error()}. Is your Exness terminal running and logged in?")
    info = mt5.account_info()
    if info:
        log(f"Connected to MT5 — account {info.login} ({info.server}), balance {info.balance} {info.currency}")
    else:
        log("Connected to MT5 (no account info yet — is the terminal logged in?)")
    return mt5


def resolve(mt5, name: str, suffix: str) -> str | None:
    candidates = [name, name + suffix] if suffix else [name]
    for c in candidates:
        if mt5.symbol_select(c, True):
            return c
    # fuzzy: search all symbols for a prefix match
    for s in mt5.symbols_get() or []:
        if s.name.upper().startswith(name.upper()):
            if mt5.symbol_select(s.name, True):
                return s.name
    return None


def push(session, url, token, path, payload) -> bool:
    try:
        r = session.post(f"{url}{path}", json=payload, headers={"X-Zaaz-Token": token}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log(f"push failed {path}: {e}")
        return False


def main():
    ap = argparse.ArgumentParser(description="Exness MT5 -> TradingByZaaz live feed bridge")
    ap.add_argument("--url", required=True, help="TradingByZaaz base URL, e.g. https://4321-xyz.e2b.app")
    ap.add_argument("--token", required=True, help="Ingest token from the web UI settings")
    ap.add_argument("--symbols", default="", help="Comma list of symbols (default: TradingByZaaz catalog)")
    ap.add_argument("--suffix", default="", help='Broker suffix, e.g. "m" (EURUSDm)')
    ap.add_argument("--terminal", default=None, help="Path to terminal64.exe")
    ap.add_argument("--tf", default="M1,M5,M15,M30,H1,H4,D1")
    ap.add_argument("--bars", type=int, default=3000)
    args = ap.parse_args()

    url = args.url.rstrip("/")
    tfs = [t.strip().upper() for t in args.tf.split(",") if t.strip()]
    wanted = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or DEFAULT_SYMBOLS

    mt5 = mt5_connect(args.terminal)
    session = requests.Session()

    # ---- map catalog symbols to this broker's names --------------------
    mapping: dict[str, str] = {}
    for canon in wanted:
        broker = resolve(mt5, canon, args.suffix)
        if broker:
            mapping[canon] = broker
            log(f"mapped {canon} -> {broker}")
        else:
            log(f"WARNING: {canon} not found on this broker — skipped")
    if not mapping:
        sys.exit("No symbols matched. Try --suffix or --symbols with exact MT5 names.")

    # ---- backfill history once per symbol --------------------------------
    def backfill():
        for canon, broker in mapping.items():
            for tf in tfs:
                mt5tf = TF_MAP.get(tf)
                if mt5tf is None:
                    continue
                rates = mt5.copy_rates_from_pos(broker, mt5tf, 0, args.bars)
                if rates is None or len(rates) == 0:
                    continue
                candles = [
                    {
                        "t": int(r["time"]),
                        "o": float(r["open"]),
                        "h": float(r["high"]),
                        "l": float(r["low"]),
                        "c": float(r["close"]),
                        "v": float(r["tick_volume"]),
                    }
                    for r in rates
                ]
                push(session, url, args.token, "/api/ingest/candles",
                     {"symbol": broker, "tf": tf, "candles": candles})
            log(f"backfilled {canon} ({broker})")

    log("Sending history…")
    backfill()
    log("Streaming live ticks — press Ctrl+C to stop")

    # ---- live tick loop ---------------------------------------------------
    pending: dict[str, deque] = {c: deque() for c in mapping}
    last_backfill = time.time()
    last_tick_seen = {c: 0.0 for c in mapping}
    while True:
        for canon, broker in mapping.items():
            tick = mt5.symbol_info_tick(broker)
            if tick is None:
                continue
            ts = tick.time if tick.time else time.time()
            last_tick_seen[canon] = time.time()
            price = tick.last or tick.bid or 0.0
            if price <= 0:
                continue
            pending[canon].append(
                {"t": int(ts), "bid": float(tick.bid), "ask": float(tick.ask), "last": float(price)}
            )
        for canon, broker in mapping.items():
            if not pending[canon]:
                continue
            ticks = list(pending[canon])
            pending[canon].clear()
            ok = push(session, url, args.token, "/api/ingest/ticks",
                      {"symbol": broker, "ticks": ticks})
            if not ok:
                log("server unreachable — retrying in 5s (history will re-sync)")
                time.sleep(5)
                backfill()
        if time.time() - last_backfill > 1800:
            backfill()
            last_backfill = time.time()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
