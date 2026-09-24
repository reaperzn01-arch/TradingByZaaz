# ▲ TradingByZaaz

**Trading signal terminal** for Exness traders. Live market highs & lows,
auto-fitted **trendlines**, and **previous market data** (PDH/PDL, weekly/monthly
extremes, floor pivots, session levels) combined into scored BUY/SELL signals
with entry, stop-loss, take-profits, confidence and full reasoning.

Built for **Exness** (MT4/MT5 symbol conventions, digits, pips, broker suffixes)
with a plug-and-play **Exness MT5 live-feed bridge**.

> ⚠ **Risk disclaimer**: TradingByZaaz provides technical analysis tooling for
> study purposes only — it is **not financial advice**. FX/CFD trading on Exness
> carries a substantial risk of loss. Never trade money you cannot afford to lose.

---

## What it does

| Pillar | Implementation |
|---|---|
| **Real-time live market highs** | Fractal swing highs/lows on the live candle stream + session/day/rolling extremes, updated every tick ("Above SwH / Below SwL" state) |
| **Trendlines** | Least-squares trendlines fitted through recent swing pivots (touch-counted, quality-scored, tolerance = ATR-based). Breakout & rejection (bounce) events become signals |
| **Previous market data** | PDH/PDL/PDC · PWH/PWL/PWC · PMH/PML · classic floor pivots (PP, R1–R3, S1–S3) · Asia/London/NY session highs & lows |
| **Confluence engine** | When 2+ structures agree at a zone: scored signals (0–100) with entry / SL (structure ± ATR buffer) / TP1 / TP2 (R-multiples), reasons and a persistent journal (win-rate stats per signal type) |

**Signal types:** `TRENDLINE_BREAK`, `TRENDLINE_BOUNCE`, `SWING_BREAKOUT`,
`LEVEL_BOUNCE`, `LEVEL_BREAK` — scanned on M5/M15/H1, chartable on M1→D1.

## Quick start

```bash
./scripts/setup.sh          # installs deps and serves on :8000
# open http://localhost:8000
```

Out of the box the app runs on a **realistic demo feed** (session volatility,
trend/range regimes, volatility clustering) so everything works immediately —
the header shows `DEMO FEED`. To trade on **real Exness prices**, connect the
bridge below; the badge turns green `LIVE · EXNESS MT5`.

## 🔌 Real-time Exness feed (recommended)

Exness has no public price API — live prices live in your MT5 terminal. Run the
bridge on the PC that has your Exness MT5 logged in:

```powershell
pip install MetaTrader5 requests
python bridge/mt5_bridge.py --url https://<this-app-url> --token <ingest-token> --suffix m
```

- `--suffix m` maps Exness mini symbols (`EURUSDm` → `EURUSD`); set any suffix /
  custom map in the web UI → **Settings · Exness Bridge**
- The ingest token is auto-generated; copy it from the web UI (Settings)
- Full guide: [`bridge/README.md`](bridge/README.md)

An optional **Twelve Data** API key (Settings) adds an alternate live source
when the app is deployed on a host with open internet.

## Architecture

```
web/  (static terminal UI, TradingView lightweight-charts, WebSocket live updates)
server/
  main.py            FastAPI app + engine loops + WS broadcast
  config.py          Exness symbol catalog (digits/pip/suffix), TFs, settings
  market/
    demo.py          realistic synthetic feed (regimes + session vol)
    feed.py          feed manager: ingest > twelvedata > demo
  engine/
    candles.py       candle series + aggregation
    swings.py        fractal swings, live extremes, breakout state
    trendlines.py    pivot trendline fitting + break/bounce detection
    levels.py        previous day/week/month, floor pivots, sessions
    indicators.py    EMA / ATR / RSI
    signals.py       confluence scoring, SL/TP construction, lifecycle, journal
    brain.py         per-symbol analysis -> signals
bridge/
  mt5_bridge.py      Exness MT5 -> ingest API (run on your PC)
```

### HTTP API (summary)

| Endpoint | Description |
|---|---|
| `GET /api/candles/{symbol}?tf=M15` | chart history |
| `GET /api/analysis/{symbol}` | swings, trendlines, levels, bias, extremes |
| `GET /api/signals`, `/api/journal`, `/api/stats` | signals + performance |
| `GET/POST /api/settings` | engine + feed settings |
| `POST /api/ingest/ticks`, `/api/ingest/candles` | Exness bridge ingest (`X-Zaaz-Token`) |
| `WS /ws` | live pulse (prices, signals, events) + per-symbol analysis |

## Signal anatomy

```
BUY EURUSD M15 · TRENDLINE_BOUNCE · confidence 84%
entry 1.08450 · SL 1.08210 · TP1 1.08880 (1.8R) · TP2 1.09310 (2.8R)
• Support trendline bounce (3 touches)
• 2x confluence structure(s) in zone (PDL + SwL)
• Higher-timeframe EMA trend agrees
```

Each signal is journaled (`data/journal.json`) so the Performance panel shows
real win rates per strategy type over time.

## Exness notes

- Symbol catalog covers majors, XAUUSD/XAGUSD, BTCUSD/ETHUSD, US30/NAS100 with
  Exness-style digits (EURUSD 5, USDJPY 3, XAUUSD 2 …) and pip math.
- Suggested **lot estimate** per signal uses your balance + risk % (Settings).
- Signals are analysis to review on your Exness chart — execution is yours.

## Development

```bash
python3 -m uvicorn server.main:app --reload --port 8000
```

Deps: `fastapi`, `uvicorn`, `httpx` (see `requirements.txt`).
