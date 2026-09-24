# Exness MT5 bridge — live feed for TradingByZaaz

Exness does not publish a public market-data API — **your MT5 terminal is the
data source**. This bridge runs on the Windows PC where your Exness MT5 is
logged in and pushes live ticks + history into TradingByZaaz's ingest API.

```
[Exness MT5 terminal] --MetaTrader5--> [mt5_bridge.py] --HTTPS--> [TradingByZaaz ingest API] --> signals
```

## 1. Prerequisites

- Windows PC with your **Exness MT5 terminal** open and logged in
- Python 3.9+ on that PC (`python --version`)
- Your TradingByZaaz app URL (the live preview or your deployment)
- The **ingest token** from the web UI → Settings → Exness bridge → Copy

## 2. Install

```powershell
pip install MetaTrader5 requests
```

## 3. Run

Download [`mt5_bridge.py`](/bridge/mt5_bridge.py) from the web UI (or copy it
from this repo), then:

```powershell
python mt5_bridge.py --url https://<your-app-host> --token <ingest-token> --suffix m
```

- `--suffix m` strips the Exness mini-account suffix (`EURUSDm` → `EURUSD`).
  Use `""` for standard symbols like `XAUUSD`, or pass `--symbols EURUSDm,XAUUSD`
  with exact MT5 names.
- The web UI *Settings → Exness symbol suffix* does the same mapping server-side.

## 4. What it sends

| Endpoint | Payload | Purpose |
|---|---|---|
| `POST /api/ingest/candles` | `{symbol, tf, candles:[{t,o,h,l,c,v}]}` | history backfill (all TFs) every 30 min |
| `POST /api/ingest/ticks` | `{symbol, ticks:[{t,bid,ask,last}]}` | live ticks, batched ~2×/sec |

Header `X-Zaaz-Token: <token>` is required on both.

## 5. Verify

The app header badge turns **LIVE · EXNESS MT5** (green) and *Feed Health* shows
`exness-mt5` per symbol. As long as live ticks keep arriving the demo generator
stands down for those symbols.

## Notes

- The bridge keeps working if your network blips: it re-pushes history on failure.
- Signals computed from these prices use the exact Exness quotes you trade on,
  including their digits and pip sizes from the app catalog.
- Multiple terminals/accounts: run one bridge per account — the ingest API
  accepts streams from several machines (last write wins per symbol).
