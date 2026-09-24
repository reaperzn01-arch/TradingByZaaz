/* TradingByZaaz signal terminal frontend */
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
};

/* ============================================================= boot */
async function init() {
  const [sym, st, sig] = await Promise.all([
    fetchJSON("/api/symbols"),
    fetchJSON("/api/settings"),
    fetchJSON("/api/signals"),
  ]);
  state.catalog = sym.symbols;
  state.timeframes = sym.timeframes;
  state.symbols = Object.keys(state.catalog);
  state.settings = st.settings || {};

  buildSymbolRow();
  buildTfRow();
  createChart();
  applySettingsToForm();
  renderSignals(sig.open || []);
  renderStats(sig.stats || {});

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
  b.textContent = ok ? "live" : "offline";
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
      <span class="s-src" id="src-${s}">${meta.kind}</span>`;
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
    layout: { background: { color: "#10141d" }, textColor: "#7d879c", fontSize: 11 },
    grid: { vertLines: { color: "#1a2130" }, horzLines: { color: "#1a2130" } },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor: "#232a3a" },
    timeScale: { borderColor: "#232a3a", timeVisible: true, secondsVisible: false },
    autoSize: true,
  });
  state.candleSeries = state.chart.addCandlestickSeries({
    upColor: "#22c07a", downColor: "#f04f5f", wickUpColor: "#22c07a", wickDownColor: "#f04f5f",
    borderVisible: false,
  });
  new ResizeObserver(() => {
    const r = wrap.getBoundingClientRect();
    state.chart.resize(r.width, r.height);
  }).observe(wrap);
}

function renderOverlays() {
  // clear old overlays
  for (const s of state.overlaySeries) state.chart.removeSeries(s);
  state.overlaySeries = [];
  for (const pl of state.priceLines) state.candleSeries.removePriceLine(pl);
  state.priceLines = [];

  const a = (state.analysis && state.analysis.analyses && state.analysis.analyses[state.tf]) || null;
  if (!a || !a.ok) { state.candleSeries.setMarkers([]); return; }

  // trendlines
  for (const tl of (a.trendlines || [])) {
    const color = tl.kind === "resistance" ? "rgba(240,79,95,.85)" : "rgba(34,192,122,.85)";
    const s = state.chart.addLineSeries({ color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, lineStyle: tl.broken ? 3 : 0 });
    s.setData([
      { time: tl.t0, value: tl.p0 },
      { time: tl.t1, value: tl.p1 },
    ]);
    state.overlaySeries.push(s);
  }

  // key levels as price lines
  const interesting = new Set(["PDH", "PDL", "PDC", "PWH", "PWL", "PDPP", "PDR1", "PDS1", "TodayH", "TodayL",
    "AsiaH", "AsiaL", "LondonH", "LondonL", "NewYorkH", "NewYorkL", "PDR2", "PDS2", "PWPP"]);
  const colors = {
    prev_day: "#e8b04b", prev_week: "#9b6fe8", today: "#4b9cf0", session: "#5a6a8a",
    pivot_d: "#7d879c", pivot_w: "#7d879c", swing: "#3a4358",
  };
  for (const lv of (a.levels || [])) {
    if (!interesting.has(lv.name)) continue;
    const pl = state.candleSeries.createPriceLine({
      price: lv.price,
      color: colors[lv.group] || "#5a6a8a",
      lineWidth: 1,
      lineStyle: lv.kind === "pivot" ? 2 : 0,
      axisLabelVisible: true,
      title: lv.name,
    });
    state.priceLines.push(pl);
  }

  // markers: pivots + signals
  const markers = [];
  for (const p of (a.pivot_highs || []).slice(-6)) {
    markers.push({ time: p.t, position: "aboveBar", color: "rgba(240,79,95,.7)", shape: "arrowDown", text: "SwH" });
  }
  for (const p of (a.pivot_lows || []).slice(-6)) {
    markers.push({ time: p.t, position: "belowBar", color: "rgba(34,192,122,.7)", shape: "arrowUp", text: "SwL" });
  }
  const sigs = (state.pulse && state.pulse.signals || []).filter(s => s.symbol === state.symbol);
  for (const s of sigs) {
    const barT = nearestBarTime(s.created);
    if (barT) markers.push({
      time: barT,
      position: s.direction === "BUY" ? "belowBar" : "aboveBar",
      color: s.direction === "BUY" ? "#22c07a" : "#f04f5f",
      shape: s.direction === "BUY" ? "arrowUp" : "arrowDown",
      text: `${s.direction} ${s.tf}`,
    });
  }
  markers.sort((x, y) => x.time - y.time);
  try { state.candleSeries.setMarkers(markers); } catch (e) { /* dup times */ }

  renderStructureStrip(a);
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
  const items = [
    ["Day H/L", `${fmt(ex.day_high, d)} / ${fmt(ex.day_low, d)}`],
    ["Live bar H/L", `${fmt(ex.bar_high, d)} / ${fmt(ex.bar_low, d)}`],
    ["Session H/L", `${fmt(ex.session_high, d)} / ${fmt(ex.session_low, d)}`],
    ["ATR", fmt(a.atr, d)],
    ["RSI", a.rsi],
    ["EMA 20/50/200", `${fmt(a.ema20, d)} · ${fmt(a.ema50, d)} · ${fmt(a.ema200, d)}`],
    ["Bias", a.bias],
    ["Trendlines", (a.trendlines || []).length],
    ["Broker", state.analysis ? (state.analysis.broker_symbol || state.symbol) : state.symbol],
  ];
  if (a.breakout) {
    items.push(["Swing state",
      `${a.breakout.above_last_swing_high ? "ABOVE SwH" : "below SwH"} · ${a.breakout.below_last_swing_low ? "BELOW SwL" : "above SwL"}`]);
  }
  $("structure-strip").innerHTML = items
    .map(([k, v]) => `<div class="struct-item">${k} <b>${v}</b></div>`).join("");

  $("cur-price").textContent = fmt(a.price, d);
  $("cur-price").className = "cur-price " + (a.bias === "BULLISH" ? "up" : a.bias === "BEARISH" ? "down" : "");
  $("cur-bias").textContent = a.bias;
  $("cur-bias").className = "bias " + a.bias;
  $("cur-src").textContent = state.analysis?.source === "demo" ? "simulated feed" : "live feed";
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

  // symbol chip prices
  for (const [s, info] of Object.entries(msg.prices || {})) {
    const px = $(`px-${s}`); const src = $(`src-${s}`);
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

  // toasts for new events
  for (const ev of (msg.events || [])) {
    const sig = ev.signal || {};
    const key = `${ev.type}:${sig.id}:${sig.status}`;
    if (state.seenEvents.has(key)) continue;
    state.seenEvents.add(key);
    if (ev.type === "signal_new") toast(sig, "NEW SIGNAL");
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
  // skip full re-render when nothing meaningful changed (avoids flicker + scroll reset)
  const sig = filtered.map(s => `${s.id}:${s.status}:${s.price_now}`).join("|");
  if (sig === lastSigSignature) return;
  lastSigSignature = sig;
  if (!filtered.length) {
    list.innerHTML = `<div class="muted" style="padding:8px">No active signals — engine is scanning swings, trendlines and previous-market levels…</div>`;
    return;
  }
  list.innerHTML = filtered.map(sigCard).join("");
}

function sigCard(s) {
  const d = s.digits ?? 5;
  const risk = Math.abs(s.entry - s.sl);
  const pips = risk / (s.pip || 0.0001);
  return `
  <div class="sig-card ${s.direction}">
    <div class="sig-head">
      <span><span class="sig-dir ${s.direction}">${s.direction}</span> <span class="sig-sym">${s.symbol}</span>
        <span class="muted">${s.tf}</span></span>
      <span><span class="sig-kind">${(s.kind || "").replace(/_/g, " ")}</span>
        ${s.bar_closed ? "" : '<span class="sig-forming">forming</span>'}</span>
    </div>
    <div class="sig-prices">
      <div><span class="lbl">ENTRY</span>${fmt(s.entry, d)}</div>
      <div><span class="lbl">STOP</span><span class="down">${fmt(s.sl, d)}</span></div>
      <div><span class="lbl">TP1 · ${s.rr1}R</span><span class="up">${fmt(s.tp1, d)}</span></div>
      <div><span class="lbl">TP2 · ${s.rr2}R</span><span class="up">${fmt(s.tp2, d)}</span></div>
    </div>
    <div class="sig-conf-row"><span>confidence ${s.confidence}%</span><span>${pips.toFixed(1)} pips risk · ${lotEstimate(risk / (s.pip || 0.0001))} lots ≈</span></div>
    <div class="sig-conf-bar"><i style="width:${s.confidence}%"></i></div>
    <ul class="sig-reasons">${(s.reasons || []).map(r => `<li>${r}</li>`).join("")}</ul>
    <div class="sig-foot">
      <span class="st ${s.status}">${s.status.replace("_", " ")}</span>
      <span>${s.level_name ? "at " + s.level_name : ""} ${age(s.created)}</span>
    </div>
  </div>`;
}

function lotEstimate(slPips) {
  const st = state.settings || {};
  const bal = st.account_balance || 10000;
  const riskPct = st.risk_percent || 1;
  const riskAmt = bal * riskPct / 100;
  const lots = riskAmt / Math.max(slPips * 10, 1);
  return lots.toFixed(2);
}

function age(created) {
  const s = Math.max(0, (Date.now() / 1000) - created);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function renderStats(st) {
  $("stats-line").textContent = st.closed ? `${st.closed} closed` : "journal empty";
  const rows = [
    ["Win rate", st.closed ? st.win_rate + "%" : "—"],
    ["Wins / Losses / Expired", `${st.wins ?? 0} / ${st.losses ?? 0} / ${st.expired ?? 0}`],
    ["Open now", st.open_now ?? 0],
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
  $("feed-mode-note").textContent = state.settings.force_demo ? "forced demo" : "auto: real > demo";
}

/* ============================================================= toasts + sound */
function toast(sig, title) {
  const el = document.createElement("div");
  el.className = "toast " + sig.direction;
  el.innerHTML = `<b>${title} · ${sig.direction} ${sig.symbol} ${sig.tf}</b>
    entry ${fmt(sig.entry, sig.digits)} · SL ${fmt(sig.sl, sig.digits)} · TP1 ${fmt(sig.tp1, sig.digits)}
    <div class="muted">${(sig.reasons || [])[0] || ""}</div>`;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 8000);
  if ($("chk-sound").checked) beep(sig.direction === "BUY" ? 660 : 440);
}

let audioCtx = null;
function beep(freq) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(); const g = audioCtx.createGain();
    o.frequency.value = freq; o.type = "sine";
    g.gain.setValueAtTime(0.06, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.35);
    o.connect(g); g.connect(audioCtx.destination);
    o.start(); o.stop(audioCtx.currentTime + 0.35);
  } catch { /* no audio */ }
}

/* ============================================================= settings */
function applySettingsToForm() {
  const st = state.settings || {};
  $("inp-token").value = st.ingest_token || "";
  $("inp-suffix").value = st.symbol_suffix || "";
  $("inp-tdkey").value = st.twelvedata_key || "";
  $("inp-force-demo").checked = !!st.force_demo;
  $("inp-speed").value = st.demo_speed ?? 1;
  $("inp-rr").value = st.default_rr ?? 1.8;
  $("inp-sl").value = st.sl_atr ?? 1.2;
  $("inp-risk").value = st.risk_percent ?? 1;
  $("inp-balance").value = st.account_balance ?? 10000;
  $("inp-map").value = JSON.stringify(st.symbol_map || {});
}

async function saveSettings() {
  let map = {};
  try { map = JSON.parse($("inp-map").value || "{}"); } catch { alert("symbol map must be valid JSON"); return; }
  const payload = {
    ingest_token: $("inp-token").value.trim(),
    symbol_suffix: $("inp-suffix").value.trim(),
    twelvedata_key: $("inp-tdkey").value.trim(),
    force_demo: $("inp-force-demo").checked,
    demo_speed: parseFloat($("inp-speed").value) || 1,
    default_rr: parseFloat($("inp-rr").value) || 1.8,
    sl_atr: parseFloat($("inp-sl").value) || 1.2,
    risk_percent: parseFloat($("inp-risk").value) || 1,
    account_balance: parseFloat($("inp-balance").value) || 10000,
    symbol_map: map,
  };
  const res = await fetchJSON("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  state.settings = res.settings;
  renderFeedBox(state.pulse?.feed || {});
}

function wireUI() {
  $("btn-settings").onclick = () => { applySettingsToForm(); $("settings-modal").showModal(); };
  $("btn-close-settings").onclick = () => $("settings-modal").close();
  $("btn-save-settings").onclick = async (e) => { e.preventDefault(); await saveSettings(); $("settings-modal").close(); };
  $("btn-copy-token").onclick = () => {
    const t = $("inp-token");
    navigator.clipboard ? navigator.clipboard.writeText(t.value) : t.select();
  };
  $("btn-new-token").onclick = () => {
    const rnd = Array.from(crypto.getRandomValues(new Uint8Array(18))).map(b => b.toString(16).padStart(2, "0")).join("");
    $("inp-token").value = rnd;
  };
  $("btn-demo-reset").onclick = async () => {
    if (!confirm("Reset all demo history and close signals?")) return;
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
