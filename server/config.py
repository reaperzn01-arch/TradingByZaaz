"""TradingByZaaz — global configuration: Exness symbol catalog, timeframes, defaults."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TBAZ_DATA_DIR", ROOT / "data"))
WEB_DIR = ROOT / "web"
BRIDGE_DIR = ROOT / "bridge"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Timeframes (seconds per bar)
# ---------------------------------------------------------------------------
TIMEFRAMES = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}
DEFAULT_TF = "M15"
# Timeframes the signal engine actively scans for every symbol.
ANALYSIS_TFS = ["M5", "M15", "H1"]

# ---------------------------------------------------------------------------
# Exness-oriented symbol catalog.
#   digits   -> price decimals used by Exness quotes
#   pip      -> value of one pip (0.0001 for most FX, 0.01 for JPY/gold ...)
#   seed     -> demo-feed anchor price
#   vol      -> demo-feed annualized-ish vol scalar (per-minute sigma approx.)
#   suffix   -> common Exness/MT5 broker suffix examples ("", "m", "s", ".a" ...)
# ---------------------------------------------------------------------------
SYMBOLS = {
    "EURUSD": {"label": "EUR / USD", "kind": "forex", "digits": 5, "pip": 0.0001, "seed": 1.08500, "vol": 0.00030},
    "GBPUSD": {"label": "GBP / USD", "kind": "forex", "digits": 5, "pip": 0.0001, "seed": 1.27500, "vol": 0.00035},
    "USDJPY": {"label": "USD / JPY", "kind": "forex", "digits": 3, "pip": 0.01, "seed": 148.500, "vol": 0.00032},
    "AUDUSD": {"label": "AUD / USD", "kind": "forex", "digits": 5, "pip": 0.0001, "seed": 0.66500, "vol": 0.00033},
    "USDCAD": {"label": "USD / CAD", "kind": "forex", "digits": 5, "pip": 0.0001, "seed": 1.36000, "vol": 0.00028},
    "USDCHF": {"label": "USD / CHF", "kind": "forex", "digits": 5, "pip": 0.0001, "seed": 0.88000, "vol": 0.00027},
    "NZDUSD": {"label": "NZD / USD", "kind": "forex", "digits": 5, "pip": 0.0001, "seed": 0.61000, "vol": 0.00034},
    "EURJPY": {"label": "EUR / JPY", "kind": "forex", "digits": 3, "pip": 0.01, "seed": 161.200, "vol": 0.00034},
    "XAUUSD": {"label": "Gold / USD", "kind": "metal", "digits": 2, "pip": 0.01, "seed": 2650.00, "vol": 0.00060},
    "XAGUSD": {"label": "Silver / USD", "kind": "metal", "digits": 3, "pip": 0.001, "seed": 31.200, "vol": 0.00090},
    "BTCUSD": {"label": "Bitcoin / USD", "kind": "crypto", "digits": 2, "pip": 0.01, "seed": 96000.0, "vol": 0.00130},
    "ETHUSD": {"label": "Ethereum / USD", "kind": "crypto", "digits": 2, "pip": 0.01, "seed": 3400.0, "vol": 0.00150},
    "US30": {"label": "US Wall St 30", "kind": "index", "digits": 2, "pip": 0.01, "seed": 41500.0, "vol": 0.00055},
    "NAS100": {"label": "US Tech 100", "kind": "index", "digits": 2, "pip": 0.01, "seed": 20100.0, "vol": 0.00075},
}
DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "US30"]

# Sessions (UTC hours) used for session high/low structure
SESSIONS = {
    "Asia": (0, 7),
    "London": (7, 16),
    "NewYork": (12, 21),
}

# ---------------------------------------------------------------------------
# Engine defaults (user-tunable via /api/settings)
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "symbol_suffix": "",            # e.g. "m" if your Exness symbols are EURUSDm
    "symbol_map": {},               # e.g. {"GOLD": "XAUUSD"} for exotic broker names
    "ingest_token": "",             # auto-generated on first run; used by the MT5 bridge
    "twelvedata_key": "",           # optional real-time provider key
    "force_demo": False,            # True -> ignore real feeds, always simulate
    "demo_speed": 1.0,              # demo clock multiplier (1.0 = real time)
    "swing_lookback": 2,            # fractal pivot confirmation bars each side
    "trendline_tolerance_atr": 0.30,
    "trendline_min_touches": 2,
    "confluence_atr": 0.50,         # zone radius for counting confluence structures
    "default_rr": 1.8,              # take-profit distance in R
    "sl_atr": 1.20,                 # stop-loss buffer in ATR beyond structure
    "signal_max_age_bars": 96,      # expire stale signals after N bars on their TF
    "risk_percent": 1.0,            # suggested risk % of account per trade
    "account_balance": 10000.0,     # used for suggested lot-size estimate
    "notifications_sound": True,
}


def broker_symbol(symbol: str, settings: dict) -> str:
    """Map a canonical symbol (EURUSD) to the broker/MT5 name (EURUSDm)."""
    inv = {v: k for k, v in (settings.get("symbol_map") or {}).items()}
    if symbol in inv:
        return inv[symbol]
    return f"{symbol}{settings.get('symbol_suffix', '')}"


def canonical_symbol(broker_name: str, settings: dict) -> str | None:
    """Map an incoming MT5/broker symbol name back to a canonical symbol."""
    smap = settings.get("symbol_map") or {}
    if broker_name in smap:
        return smap[broker_name]
    if broker_name in SYMBOLS:
        return broker_name
    suffix = settings.get("symbol_suffix") or ""
    if suffix and broker_name.endswith(suffix):
        base = broker_name[: -len(suffix)]
        if base in SYMBOLS:
            return base
    # last resort: prefix match (handles EURUSDm, EURUSD.a, XAUUSD_i ...)
    for s in SYMBOLS:
        if broker_name.upper().startswith(s):
            return s
    return None


def ensure_token(settings: dict) -> dict:
    if not settings.get("ingest_token"):
        settings["ingest_token"] = secrets.token_urlsafe(24)
    return settings
