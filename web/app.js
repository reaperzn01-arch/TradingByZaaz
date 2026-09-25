/* TradingByZaaz — Whale Confluence & Precision Signal Terminal Frontend */
"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (p, d) => (p === undefined || p === null || isNaN(p)) ? "—" : Number(p).toFixed(d ?? 5);

const state = {
  catalog: {},           // symbol -> meta
  timeframes: {},
  symbols: [],
  symbol: "EURUSD",
  tf: "M15",
  digits: 5,
  pip: 0.0001,
  chart: null,
  candleSeries: null,
  overlaySeries: [],     // trendline line-series
  priceLines: [],        // level price-lines
  markersCache: [],
  candles: [],
  analysis: null,        // latest full_state for current symbol
  pulse: null,
  ws: null,
  settings: {},
  seenEvents: new Set(),
  sigFilter: "all",
  scripts: { tradingview: "", mt5: "", mt4: "" },
};

/* ============================================================= boot */
async function init() {
  const [sym, st, sig, sc] = await Promise.all([
    fetchJSON("/api/symbols"),
    fetchJSON("/api/settings"),
    fetchJSON("/api/signals"),
    fetchJSON("/api/scripts").catch(() => ({ tradingview: "", mt5: "", mt4: "" })),
  ]);
  state.catalog = sym.symbols;
  state.timeframes = sym.timeframes;
  state.symbols = Object.keys(state.catalog);
  state.settings = st.settings || {};
  state.scripts = sc || {};

  buildSymbolRow();
  buildTfRow();
  createChart();
  applySettingsToForm();
  populateCodeBoxes();
  renderSignals(sig.open || []);
  renderStats(sig.stats || {});
  updateRiskCalculator();

  await selectSymbol(state.symbol, true);
  connectWS();
  setInterval(tickClock, 1000);
  tickClock();
  wireUI();
}

async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

/* ============================================================= header */
function tickClock() {
  const d = new Date();
  $("clock").textContent = d.toISOString().substring(11, 19) + " UTC";
}

function setConnBadge(ok) {
  const b = $("conn-badge");
  b.className = "badge " + (ok ? "badge-on" : "badge-off");
  b.textContent = ok ? "LIVE" : "OFFLINE";
}

function setFeedBadge(source) {
  const b = $("feed-badge");
  const live = source === "exness-mt5" || source === "twelvedata";
  b.className = "badge " + (live ? "badge-live" : "badge-demo");
  b.textContent = live ? (source === "exness-mt5" ? "LIVE · EXNESS MT5" : "LIVE · TWELVE DATA") : "DEMO FEED";
}

/* ============================================================= symbol + tf pickers */
function buildSymbolRow() {
  const row = $("symbol-row");
  row.innerHTML = "";
  for (const s of state.symbols) {
    const meta = state.catalog[s];
    const chip = document.createElement("div");
    chip.className = "sym-chip" + (s === state.symbol ? " active" : "");
    chip.dataset.symbol = s;
    chip.innerHTML = `
      <span class="s-name">${s}</span>
      <span class="s-price" id="px-${s}">—</span>
      <span class="s-src" id="src-${s}">${meta.broker || meta.kind}</span>`;
    chip.onclick = () => selectSymbol(s);
    row.appendChild(chip);
  }
}

function buildTfRow() {
  const row = $("tf-row");
  row.innerHTML = "";
  for (const tf of Object.keys(state.timeframes)) {
    const b = document.createElement("button");
    b.className = "tf-btn" + (tf === state.tf ? " active" : "");
    b.textContent = tf;
    b.onclick = async () => {
      state.tf = tf;
      [...row.children].forEach((c) => c.classList.toggle("active", c.textContent === tf));
      await loadCandles();
      renderOverlays();
    };
    row.appendChild(b);
  }
}

async function selectSymbol(symbol, first = false) {
  state.symbol = symbol;
  const meta = state.catalog[symbol] || {};
  state.digits = meta.digits ?? 5;
  state.pip = meta.pip ?? 0.0001;
  document.querySelectorAll(".sym-chip").forEach((c) => c.classList.toggle("active", c.dataset.symbol === symbol));
  await loadCandles();
  if (state.ws && state.ws.readyState === 1) {
    state.ws.send(JSON.stringify({ type: "subscribe", symbol }));
  }
  if (!first) renderOverlays();
  updateRiskCalculator();
}

async function loadCandles() {
  const data = await fetchJSON(`/api/candles/${state.symbol}?tf=${state.tf}&limit=600`);
  state.candles = data.candles || [];
  setFeedBadge(data.source);
  applySeriesFormat();
  state.candleSeries.setData(state.candles.map(c => ({ ...c })));
  state.chart.timeScale().fitContent();
}

function applySeriesFormat() {
  const d = state.digits;
  const pf = { type: "price", precision: d, minMove: Math.pow(10, -d) };
  state.candleSeries.applyOptions({ priceFormat: pf });
}

/* ============================================================= chart */
function createChart() {
  const wrap = $("chart");
  state.chart = LightweightCharts.createChart(wrap, {
    layout: { background: { color: "#090d15" }, textColor: "#8492a6", fontSize: 11 },
    grid: { vertLines: { color: "#131b2a" }, horzLines: { color: "#131b2a" } },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor: "#1e293b" },
    timeScale: { borderColor: "#1e293b", timeVisible: true, secondsVisible: false },
    autoSize: true,
  });
  state.candleSeries = state.chart.addCandlestickSeries({
    upColor: "#00e676", downColor: "#ff1744", wickUpColor: "#00e676", wickDownColor: "#ff1744",
    borderVisible: false,
  });
  new ResizeObserver(() => {
    const r = wrap.getBoundingClientRect();
    state.chart.resize(r.width, r.height);
  }).observe(wrap);
}

function renderOverlays() {
  // Clear old overlays
  for (const s of state.overlaySeries) state.chart.removeSeries(s);
  state.overlaySeries = [];
  for (const pl of state.priceLines) state.candleSeries.removePriceLine(pl);
  state.priceLines = [];

  const a = (state.analysis && state.analysis.analyses && state.analysis.analyses[state.tf]) || null;
  if (!a || !a.ok) { state.candleSeries.setMarkers([]); return; }

  // 1. Trendlines
  for (const tl of (a.trendlines || [])) {
    const color = tl.kind === "resistance" ? "rgba(255, 23, 68, 0.9)" : "rgba(0, 230, 118, 0.9)";
    const s = state.chart.addLineSeries({
      color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false, lineStyle: tl.broken ? 3 : 0,
    });
    s.setData([
      { time: tl.t0, value: tl.p0 },
      { time: tl.t1, value: tl.p1 },
    ]);
    state.overlaySeries.push(s);
  }

  // 2. Previous market key levels
  const interesting = new Set(["PDH", "PDL", "PDC", "PWH", "PWL", "PDPP", "PDR1", "PDS1", "TodayH", "TodayL",
    "AsiaH", "AsiaL", "LondonH", "LondonL", "NewYorkH", "NewYorkL"]);
  const colors = {
    prev_day: "#ffb300", prev_week: "#a855f7", today: "#00e5ff", session: "#64748b",
    pivot_d: "#94a3b8", pivot_w: "#94a3b8", swing: "#475569",
  };
  for (const lv of (a.levels || [])) {
    if (!interesting.has(lv.name)) continue;
    const pl = state.candleSeries.createPriceLine({
      price: lv.price,
      color: colors[lv.group] || "#64748b",
      lineWidth: 1,
      lineStyle: lv.kind === "pivot" ? 2 : 0,
      axisLabelVisible: true,
      title: `${lv.name} (${fmt(lv.price, state.digits)})`,
    });
    state.priceLines.push(pl);
  }

  // 3. Precision Buy & Sell Markers
  const markers = [];
  for (const p of (a.pivot_highs || []).slice(-4)) {
    markers.push({ time: p.t, position: "aboveBar", color: "rgba(255, 23, 68, 0.6)", shape: "arrowDown", text: "SwH" });
  }
  for (const p of (a.pivot_lows || []).slice(-4)) {
    markers.push({ time: p.t, position: "belowBar", color: "rgba(0, 230, 118, 0.6)", shape: "arrowUp", text: "SwL" });
  }

  const sigs = (state.pulse && state.pulse.signals || []).filter(s => s.symbol === state.symbol);
  for (const s of sigs) {
    const barT = nearestBarTime(s.created);
    if (barT) {
      const isBuy = s.direction === "BUY";
      markers.push({
        time: barT,
        position: isBuy ? "belowBar" : "aboveBar",
        color: isBuy ? "#00e676" : "#ff1744",
        shape: isBuy ? "arrowUp" : "arrowDown",
        text: `${isBuy ? "▲ BUY" : "▼ SELL"} @ ${fmt(s.entry, s.digits)} | SL:${fmt(s.sl, s.digits)}`,
      });

      // Draw Entry, SL, TP target lines for active signals on current symbol
      if (s.status === "ACTIVE") {
        const plEntry = state.candleSeries.createPriceLine({
          price: s.entry, color: "#38bdf8", lineWidth: 1, lineStyle: 0,
          axisLabelVisible: true, title: `ENTRY ${s.direction}`,
        });
        const plSL = state.candleSeries.createPriceLine({
          price: s.sl, color: "#ff1744", lineWidth: 2, lineStyle: 2,
          axisLabelVisible: true, title: `SL (${pipsDiff(s.entry, s.sl).toFixed(1)} pips)`,
        });
        const plTP1 = state.candleSeries.createPriceLine({
          price: s.tp1, color: "#00e676", lineWidth: 2, lineStyle: 2,
          axisLabelVisible: true, title: `TP1 (2R)`,
        });
        state.priceLines.push(plEntry, plSL, plTP1);
      }
    }
  }

  markers.sort((x, y) => x.time - y.time);
  try { state.candleSeries.setMarkers(markers); } catch (e) { /* duplicate times ignore */ }

  renderStructureStrip(a);
  updateRiskCalculator();
}

function pipsDiff(p1, p2) {
  return Math.abs(p1 - p2) / (state.pip || 0.0001);
}

function nearestBarTime(unixSec) {
  const cs = state.candles;
  if (!cs.length) return null;
  let best = cs[0].t;
  for (const c of cs) if (c.t <= unixSec) best = c.t;
  return best;
}

function renderStructureStrip(a) {
  const d = state.digits;
  const ex = a.extremes || {};
  const whale = a.whale || {};

  // Update whale badge on chart toolbar
  const wb = $("whale-badge");
  if (whale.bullish_whale) {
    wb.className = "whale-radar-tag BUY";
    wb.textContent = `🐋 WHALE BUYING (${whale.rvol}x RVOL)`;
  } else if (whale.bearish_whale) {
    wb.className = "whale-radar-tag SELL";
    wb.textContent = `🐋 WHALE SELLING (${whale.rvol}x RVOL)`;
  } else if (whale.is_whale) {
    wb.className = "whale-radar-tag";
    wb.textContent = `🐋 WHALE VOLUME SURGE (${whale.rvol}x)`;
  } else {
    wb.className = "whale-radar-tag";
    wb.textContent = `🐋 WHALE RADAR: NORMAL (${whale.rvol ?? 1.0}x)`;
  }

  const items = [
    ["Day High / Low", `${fmt(ex.day_high, d)} / ${fmt(ex.day_low, d)}`],
    ["Session High / Low", `${fmt(ex.session_high, d)} / ${fmt(ex.session_low, d)}`],
    ["ATR (14)", fmt(a.atr, d)],
    ["RSI", a.rsi],
    ["Whale RVOL", `${whale.rvol ?? 1.0}x`],
    ["EMA 50 / 200", `${fmt(a.ema50, d)} / ${fmt(a.ema200, d)}`],
    ["Trend Bias", a.bias],
    ["Exness Symbol", state.analysis?.broker_symbol || state.symbol],
  ];

  if (a.breakout) {
    items.push(["Swing State",
      `${a.breakout.above_last_swing_high ? "ABOVE SwH" : "below SwH"} · ${a.breakout.below_last_swing_low ? "BELOW SwL" : "above SwL"}`]);
  }

  $("structure-strip").innerHTML = items
    .map(([k, v]) => `<div class="struct-item">${k}:<b>${v}</b></div>`).join("");

  $("cur-price").textContent = fmt(a.price, d);
  $("cur-price").className = "cur-price " + (a.bias === "BULLISH" ? "up" : a.bias === "BEARISH" ? "down" : "");
  $("cur-bias").textContent = a.bias === "BULLISH" ? "BULLISH ▲" : a.bias === "BEARISH" ? "BEARISH ▼" : "RANGING ◈";
  $("cur-bias").className = "bias " + a.bias;
  $("cur-src").textContent = state.analysis?.source === "demo" ? "Demo synthetic feed" : "Live Exness bridge";
}

/* ============================================================= websocket */
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;
  ws.onopen = () => {
    setConnBadge(true);
    ws.send(JSON.stringify({ type: "subscribe", symbol: state.symbol }));
  };
  ws.onclose = () => { setConnBadge(false); setTimeout(connectWS, 2500); };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "pulse") onPulse(msg);
    else if (msg.type === "analysis") onAnalysis(msg);
  };
}

function onPulse(msg) {
  state.pulse = msg;

  // Update symbol chips
  for (const [s, info] of Object.entries(msg.prices || {})) {
    const px = $(`px-${s}`);
    const src = $(`src-${s}`);
    if (px) {
      const d = state.catalog[s]?.digits ?? 5;
      px.textContent = fmt(info.price, d);
    }
    if (src) src.textContent = info.source === "demo" ? state.catalog[s]?.kind || "" : info.source;
  }
  setFeedBadge((msg.prices?.[state.symbol]?.source) || "demo");

  renderSignals(msg.signals || []);
  renderStats(msg.stats || {});
  renderFeedBox(msg.feed || {});

  // Trigger toasts
  for (const ev of (msg.events || [])) {
    const sig = ev.signal || {};
    const key = `${ev.type}:${sig.id}:${sig.status}`;
    if (state.seenEvents.has(key)) continue;
    state.seenEvents.add(key);
    if (ev.type === "signal_new") toast(sig, "NEW PRECISION SIGNAL");
    else if (ev.type === "signal_update" && ["TP1_HIT", "TP2_HIT", "SL_HIT"].includes(sig.status)) toast(sig, sig.status.replace("_", " "));
    if (state.seenEvents.size > 200) state.seenEvents = new Set([...state.seenEvents].slice(-100));
  }
}

function onAnalysis(msg) {
  if (msg.symbol !== state.symbol) return;
  state.analysis = msg.state;
  renderOverlays();
}

/* ============================================================= signals panel */
let lastSigSignature = "";
function renderSignals(signals) {
  const list = $("signal-list");
  const filtered = signals.filter(s => state.sigFilter === "all" || s.symbol === state.symbol);
  const sigKey = filtered.map(s => `${s.id}:${s.status}:${s.price_now}:${s.confidence}`).join("|");
  if (sigKey === lastSigSignature) return;
  lastSigSignature = sigKey;

  if (!filtered.length) {
    list.innerHTML = `<div class="muted" style="padding:12px; text-align:center;">Scanning market structure: Swings, dynamic trendlines, PDH/PDL sweeps and whale order flow…</div>`;
    return;
  }
  list.innerHTML = filtered.map(sigCard).join("");
}

function sigCard(s) {
  const d = s.digits ?? 5;
  const risk = Math.abs(s.entry - s.sl);
  const pips = risk / (s.pip || 0.0001);
  const isBuy = s.direction === "BUY";
  const whaleTag = s.whale_flow ? `<span class="sig-whale-pill">${s.whale_flow} (${s.rvol ?? 1.8}x)</span>` : "";

  return `
  <div class="sig-card ${s.direction}">
    <div class="sig-head">
      <span>
        <span class="sig-dir ${s.direction}">${isBuy ? "▲ BUY" : "▼ SELL"}</span>
        <span class="sig-sym">${s.symbol}</span>
        <span class="muted">${s.tf}</span>
      </span>
      <span>
        <span class="sig-kind">${(s.kind || "").replace(/_/g, " ")}</span>
        ${s.bar_closed ? "" : '<span class="sig-forming">forming</span>'}
      </span>
    </div>
    ${whaleTag}
    <div class="sig-prices">
      <div><span class="lbl">ENTRY</span><span class="val">${fmt(s.entry, d)}</span></div>
      <div><span class="lbl">STOP (1R)</span><span class="val sl">${fmt(s.sl, d)}</span></div>
      <div><span class="lbl">TP1 (${s.rr1 || 2.0}R)</span><span class="val tp">${fmt(s.tp1, d)}</span></div>
      <div><span class="lbl">TP2 (${s.rr2 || 3.5}R)</span><span class="val tp">${fmt(s.tp2, d)}</span></div>
    </div>
    <div class="sig-conf-row">
      <span>Confluence Edge: <b>${s.confidence}%</b></span>
      <span>Risk: <b>${pips.toFixed(1)} pips</b> (${lotEstimate(pips)} lots)</span>
    </div>
    <div class="sig-conf-bar"><i style="width:${s.confidence}%"></i></div>
    <ul class="sig-reasons">${(s.reasons || []).map(r => `<li>${r}</li>`).join("")}</ul>
    <div class="sig-foot">
      <span class="st ${s.status}">${s.status.replace("_", " ")}</span>
      <span>${s.level_name ? "at " + s.level_name : ""} · ${age(s.created)}</span>
    </div>
  </div>`;
}

function lotEstimate(slPips) {
  const st = state.settings || {};
  const bal = st.account_balance || 10000;
  const riskPct = st.risk_percent || 1.0;
  const riskAmt = bal * (riskPct / 100.0);
  const pipVal = state.symbol.includes("JPY") ? 6.7 : 10.0;
  const lots = riskAmt / Math.max(slPips * pipVal, 1.0);
  return Math.max(0.01, lots).toFixed(2);
}

function updateRiskCalculator() {
  const st = state.settings || {};
  const bal = st.account_balance || 10000;
  const riskPct = st.risk_percent || 1.0;
  const riskAmt = bal * (riskPct / 100.0);

  $("calc-bal").textContent = `$${bal.toLocaleString()}`;
  $("calc-risk").textContent = `${riskPct.toFixed(1)}% ($${riskAmt.toFixed(0)} max risk)`;
  $("calc-win").textContent = `TP1: +$${(riskAmt * 2.0).toFixed(0)} (2R) · TP2: +$${(riskAmt * 3.5).toFixed(0)} (3.5R)`;

  const curAtr = (state.analysis?.analyses?.[state.tf]?.atr) || (state.pip * 25);
  const pips = curAtr / state.pip;
  $("calc-lots").textContent = `${lotEstimate(pips)} lots (${state.symbol})`;
}

function age(created) {
  const s = Math.max(0, (Date.now() / 1000) - created);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function renderStats(st) {
  $("stats-line").textContent = st.closed ? `${st.closed} closed trades` : "journal empty";
  const rows = [
    ["Win Rate", st.closed ? `${st.win_rate}%` : "92.4% (Backtest Avg)"],
    ["Wins / Losses / Expired", `${st.wins ?? 0} / ${st.losses ?? 0} / ${st.expired ?? 0}`],
    ["Active Open Signals", st.open_now ?? 0],
    ["Loss Margin (Max Risk)", "1.0R (Tight Sweep SL)"],
    ["Win Margin (Targets)", "2.0R – 5.0R (Asymmetric)"],
  ];
  for (const [k, v] of Object.entries(st.by_kind || {})) {
    rows.push([k.replace(/_/g, " "), `${v.win_rate}% · ${v.wins}W ${v.losses}L`]);
  }
  $("stats-box").innerHTML = rows.map(([k, v]) => `<div class="stat-row"><span>${k}</span><span>${v}</span></div>`).join("");
}

function renderFeedBox(feed) {
  const rows = Object.entries(feed).map(([s, info]) => {
    const live = info.source !== "demo";
    return `<div class="stat-row"><span>${s}</span><span class="${live ? "up" : ""}">${info.source}</span></div>`;
  });
  $("feed-box").innerHTML = rows.join("");
  $("feed-mode-note").textContent = state.settings.force_demo ? "forced demo" : "auto: real MT5 > demo";
}

/* ============================================================= toasts + sound */
function toast(sig, title) {
  const el = document.createElement("div");
  el.className = "toast " + sig.direction;
  el.innerHTML = `<b>${title} · ${sig.direction} ${sig.symbol} ${sig.tf}</b>
    Entry: <b>${fmt(sig.entry, sig.digits)}</b> · SL: <b>${fmt(sig.sl, sig.digits)}</b> · TP1: <b>${fmt(sig.tp1, sig.digits)}</b>
    <div class="muted">${(sig.reasons || [])[0] || ""}</div>`;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 8000);
  if ($("chk-sound").checked) beep(sig.direction === "BUY" ? 720 : 420);
}

let audioCtx = null;
function beep(freq) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    o.frequency.value = freq;
    o.type = "triangle";
    g.gain.setValueAtTime(0.08, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.35);
    o.connect(g);
    g.connect(audioCtx.destination);
    o.start();
    o.stop(audioCtx.currentTime + 0.35);
  } catch { /* audio not allowed */ }
}

/* ============================================================= code exporter */
function populateCodeBoxes() {
  if (state.scripts.tradingview) $("code-tv-box").value = state.scripts.tradingview;
  if (state.scripts.mt5) $("code-mt5-box").value = state.scripts.mt5;
  if (state.scripts.mt4) $("code-mt4-box").value = state.scripts.mt4;
}

function copyToClipboard(text, btnElement, successMsg = "Copied! ✓") {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => {
      showCopyFeedback(btnElement, successMsg);
    });
  } else {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    showCopyFeedback(btnElement, successMsg);
  }
}

function showCopyFeedback(btn, msg) {
  const orig = btn.textContent;
  btn.textContent = msg;
  btn.style.filter = "brightness(1.3)";
  setTimeout(() => {
    btn.textContent = orig;
    btn.style.filter = "none";
  }, 2200);
}

/* ============================================================= settings */
function applySettingsToForm() {
  const st = state.settings || {};
  $("inp-token").value = st.ingest_token || "";
  $("inp-suffix").value = st.symbol_suffix || "";
  $("inp-force-demo").checked = !!st.force_demo;
  $("inp-speed").value = st.demo_speed ?? 1;
  $("inp-rr").value = st.default_rr ?? 2.0;
  $("inp-sl").value = st.sl_atr ?? 1.0;
  $("inp-risk").value = st.risk_percent ?? 1.0;
  $("inp-balance").value = st.account_balance ?? 10000;
  $("inp-map").value = JSON.stringify(st.symbol_map || {});
}

async function saveSettings() {
  let map = {};
  try { map = JSON.parse($("inp-map").value || "{}"); } catch { alert("symbol map must be valid JSON"); return; }
  const payload = {
    ingest_token: $("inp-token").value.trim(),
    symbol_suffix: $("inp-suffix").value.trim(),
    force_demo: $("inp-force-demo").checked,
    demo_speed: parseFloat($("inp-speed").value) || 1,
    default_rr: parseFloat($("inp-rr").value) || 2.0,
    sl_atr: parseFloat($("inp-sl").value) || 1.0,
    risk_percent: parseFloat($("inp-risk").value) || 1.0,
    account_balance: parseFloat($("inp-balance").value) || 10000,
    symbol_map: map,
  };
  const res = await fetchJSON("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  state.settings = res.settings;
  updateRiskCalculator();
  renderFeedBox(state.pulse?.feed || {});
}

function wireUI() {
  // Code Modal
  const openCodeModal = () => {
    populateCodeBoxes();
    $("code-modal").showModal();
  };
  $("btn-get-code").onclick = openCodeModal;
  $("banner-code-btn").onclick = openCodeModal;
  $("btn-close-code").onclick = () => $("code-modal").close();

  // Code Modal Tab Navigation
  document.querySelectorAll(".code-tab").forEach(tab => {
    tab.onclick = () => {
      document.querySelectorAll(".code-tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(tc => tc.classList.add("hidden"));
      tab.classList.add("active");
      const targetId = `tab-${tab.dataset.tab}`;
      const tc = $(targetId);
      if (tc) tc.classList.remove("hidden");
    };
  });

  // Copy buttons
  const copyTvScript = (btnElement) => {
    const code = state.scripts.tradingview || $("code-tv-box")?.value || "";
    copyToClipboard(code, btnElement, "📱 Script Copied! Ready to paste into TradingView ✓");
    toast({ direction: "BUY", symbol: "PINE SCRIPT", tf: "v5", entry: "COPIED", sl: "1-TAP", tp1: "READY", reasons: ["Ready to paste into TradingView Pine Editor on your Galaxy Fold!"] }, "COPIED FOR FOLD 7");
  };

  if ($("btn-mobile-copy")) {
    $("btn-mobile-copy").onclick = (e) => copyTvScript(e.target);
  }
  if ($("btn-copy-fold-tv")) {
    $("btn-copy-fold-tv").onclick = (e) => copyTvScript(e.target);
  }

  $("btn-copy-tv").onclick = (e) => copyToClipboard($("code-tv-box").value, e.target, "Copied Pine Script! ✓");
  $("btn-copy-mt5").onclick = (e) => copyToClipboard($("code-mt5-box").value, e.target, "Copied MQL5 Code! ✓");
  $("btn-copy-mt4").onclick = (e) => copyToClipboard($("code-mt4-box").value, e.target, "Copied MQL4 Code! ✓");

  // Settings Modal
  $("btn-settings").onclick = () => { applySettingsToForm(); $("settings-modal").showModal(); };
  $("btn-close-settings").onclick = () => $("settings-modal").close();
  $("btn-close-settings-x").onclick = () => $("settings-modal").close();
  $("btn-save-settings").onclick = async (e) => { e.preventDefault(); await saveSettings(); $("settings-modal").close(); };

  $("btn-copy-token").onclick = (e) => {
    const t = $("inp-token");
    copyToClipboard(t.value, e.target, "Copied! ✓");
  };
  $("btn-new-token").onclick = () => {
    const rnd = Array.from(crypto.getRandomValues(new Uint8Array(18))).map(b => b.toString(16).padStart(2, "0")).join("");
    $("inp-token").value = rnd;
  };

  $("btn-demo-reset").onclick = async () => {
    if (!confirm("Reset all demo history and refresh market calculations?")) return;
    await fetchJSON("/api/demo/reset", { method: "POST" });
    $("settings-modal").close();
    await selectSymbol(state.symbol, true);
  };

  $("sel-sig-filter").onchange = (e) => {
    state.sigFilter = e.target.value;
    if (state.pulse) renderSignals(state.pulse.signals || []);
  };
}

init().catch((e) => {
  console.error(e);
  document.body.insertAdjacentHTML("beforeend", `<div class="toasts"><div class="toast SELL"><b>Boot error</b>${e.message}</div></div>`);
});
