/* BharatInvest frontend — vanilla JS, no build step */
const state = { scan: null, market: null, sort: { key: "total", dir: -1 }, charts: [] };

/* ---------------- helpers ---------------- */
const $ = (id) => document.getElementById(id);
const fmtN = (x, d = 1) => (x === null || x === undefined || Number.isNaN(x) ? "–" :
  Number(x).toLocaleString("en-IN", { maximumFractionDigits: d }));
const fmtPct = (x, d = 1) => (x === null || x === undefined ? "–" : (x > 0 ? "+" : "") + Number(x).toFixed(d) + "%");
const fmtPrice = (x) => (x === null || x === undefined ? "–" : "₹" + fmtN(x, 1));
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const okChip = (v) => (v === null || v === undefined) ? "–" : fmtN(v, 0);
const fmtDateStr = (d) => { try { return new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" }); } catch (e) { return String(d || ""); } };
const fmtIST = (iso) => { try { return new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true }); } catch (e) { return String(iso || ""); } };

function ratingClass(r) {
  if (!r) return "b-hold";
  if (r.startsWith("STRONG")) return "b-strong";
  if (r.startsWith("BUY")) return "b-buy";
  if (r.startsWith("HOLD")) return "b-hold";
  return "b-avoid";
}
function ringColor(score) {
  if (score >= 78) return "#0c7a43";
  if (score >= 68) return "#0f766e";
  if (score >= 55) return "#b7791f";
  return "#b3372c";
}
function scoreRing(score) {
  const c = ringColor(score);
  const deg = Math.max(0, Math.min(100, score)) * 3.6;
  return `<div class="score-ring" style="background: conic-gradient(${c} ${deg}deg, #edf0f3 ${deg}deg)"><span style="background:#fff;width:38px;height:38px;border-radius:50%;display:flex;align-items:center;justify-content:center">${Math.round(score)}</span></div>`;
}
function loadingHTML(msg) { return `<div><span class="loading"></span>${esc(msg || "Loading…")}</div>`; }
function errorHTML(msg) { return `<div class="banner">${esc(msg)}</div>`; }

/* ---------------- tabs ---------------- */
let screenOpened = false;
let portfolioOpened = false;
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab-page").forEach(p => p.classList.toggle("active", p.id === "tab-" + btn.dataset.tab));
    window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
    if (btn.dataset.tab === "screen") {
      screenOpened = true;
      if (state.scan && state.scan.rows) {
        applyFilters();                 // pehli baar khulne par full list dikha do (filters khali = sab)
      } else if (!$("screen-table").innerHTML.trim()) {
        $("screen-table").innerHTML = loadingHTML("Scan data load ho raha hai (Dashboard start me hi fetch ho raha hai)…");
      }
    }
    if (btn.dataset.tab === "portfolio" && !portfolioOpened) { portfolioOpened = true; loadPortfolio(); }
  });
});
function openStockTab(symbol) {
  document.querySelector('.tab[data-tab="stock"]').click();
  $("stock-input").value = symbol;
  analyzeSymbol(symbol);
}

/* ---------------- universe / datalist ---------------- */
fetch("/api/universe").then(r => r.json()).then(list => {
  const dl = $("uni-list");
  state.universe = list;
  dl.innerHTML = list.map(u => `<option value="${esc(u.symbol)}">${esc(u.name)} · ${esc(u.sector)}</option>`).join("");
}).catch(() => {});

/* ---------------- dashboard ---------------- */
async function loadDashboard() {
  ["market-strip", "fii-card", "breadth-card", "top-cards", "rank-table"].forEach(id => { $(id).innerHTML = ""; $(id).innerHTML = loadingHTML(); });
  try {
    state.market = await (await fetch("/api/market")).json();
    renderMarket(state.market);
    renderFII(state.market);
  } catch (e) { $("market-strip").innerHTML = errorHTML("Market data failed: " + esc(e.message)); }

  try {
    state.scan = await (await fetch("/api/scan")).json();
    renderScan(state.scan);
  } catch (e) {
    $("top-cards").innerHTML = errorHTML("Scan failed: " + esc(e.message));
    $("rank-table").innerHTML = "";
  }
}

function renderMarket(m) {
  const strip = $("market-strip");
  let html = "";
  for (const [k, v] of Object.entries(m.indices || {})) {
    const chg = v.chg_pct;
    html += `<div class="market">
      <div class="name">${esc(k)}</div>
      <div class="val">${fmtN(v.value, 2)}</div>
      <div class="${chg >= 0 ? 'chg-up' : 'chg-down'}">${chg === null ? "" : fmtPct(chg, 2)}</div>
      <div class="muted" style="font-size:10px">as of ${fmtDateStr(v.date)} EOD</div>
    </div>`;
  }
  /* Nifty P/E gauge — mehenga / sasta market */
  const nv = m.nifty_valuation || {};
  if (nv && nv.available) {
    const cls = nv.verdict === "CHEAP" ? "val-cheap" : nv.verdict === "EXPENSIVE" ? "val-dear" : "val-fair";
    html += `<div class="market">
      <div class="name">NIFTY P/E · market gauge ${aiBtn("nifty_valuation", "", "Explain")}</div>
      <div class="val">${fmtN(nv.pe, 2)}</div>
      <div class="${cls}" style="font-weight:700">${esc(nv.verdict || "")}</div>
      <div class="muted" style="font-size:10px">NSE as of ${fmtDateStr(nv.as_of)} · ${esc(nv.basis || "")}${nv.stale ? " (stale)" : ""}</div>
    </div>`;
  }
  strip.innerHTML = html || errorHTML("No index data");
}

function renderFII(m) {
  const fd = m.fii_dii;
  let html = "";
  if (fd && fd.available && (fd.rows || []).length) {
    for (const r of fd.rows.slice(0, 4)) {
      const ncls = r.net_cr >= 0 ? "chg-up" : "chg-down";
      html += `<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dashed #eef1f4">
        <span><b>${esc(r.who)}</b> <span class="muted small">${esc(String(r.date).slice(0, 10))}</span></span>
        <span class="${ncls}" style="font-weight:700">₹${fmtN(r.net_cr, 0)} Cr</span></div>`;
    }
    html += `<div class="muted small" style="margin-top:6px">Net cash flows (₹ crore). NSE public data.</div>`;
  } else {
    html = `<div class="muted small">Not available right now — NSE blocks scripts sometimes. Cross-check on NSE → FII/DII page.</div>`;
  }
  $("fii-card").innerHTML = `<h3>FII / DII flows ${aiBtn("fii_dii", "", "Explain")}</h3>` + html;
}

function renderScan(s) {
  if (!s || !s.rows) return;
  const b = s.breadth || {};
  $("breadth-card").innerHTML = `<h3>Market breadth (universe)</h3>
    <div style="font-size:28px;font-weight:800">${b.available ? b.above_200dma_pct + "%" : "–"}</div>
    <div class="muted small">of ${okChip(b.n)} scanned stocks are above their 200-DMA. Above 50% = healthy market.</div>`;
  $("scan-meta").innerHTML = `Scan: ${s.count} stocks · generated ${esc(fmtIST(s.generated_at))} IST` +
    (s.stale ? ` <span class="scan-refresh-note">fresh scan chal raha hai — auto-refresh…</span>` : "");

  const top = s.rows.slice(0, 6);
  $("top-cards").innerHTML = top.map(r => topCard(r)).join("");
  document.querySelectorAll(".top-card").forEach(el => el.addEventListener("click", () => openStockTab(el.dataset.sym)));

  renderTable(s.rows);
  const sect = new Set(s.rows.map(r => r.sector).filter(Boolean));
  const fs = $("f-sector");
  const cur = fs.value;
  fs.innerHTML = `<option value="">All</option>` + [...sect].sort().map(x => `<option${x === cur ? " selected" : ""}>${esc(x)}</option>`).join("");

  // breadth uses scan too — update if we got late
  const b2 = s.breadth || {}; const bc = $("breadth-card");
  if (bc && b2.available && bc.innerHTML.includes("Awaiting")) {
    bc.innerHTML = `<h3>Market breadth (universe)</h3><div style="font-size:28px;font-weight:800">${b2.above_200dma_pct}%</div>
      <div class="muted small">of ${b2.n} scanned stocks above 200-DMA</div>`;
  }
  // agar user pehle Screener tab khol chuka hai aur scan ab aaya hai to results auto-refresh
  if (screenOpened) applyFilters();

  // purana scan mila + background refresh chal raha hai -> poll karke fresh auto-load
  if (s.stale) pollFreshScan();
}

/* Stale scan ke baad /api/scan poll karta hai — fresh hone par khud re-render */
let _stalePoll = null;
function pollFreshScan() {
  if (_stalePoll) return;
  let tries = 0;
  _stalePoll = setInterval(async () => {
    tries++;
    try {
      const s = await (await fetch("/api/scan")).json();
      if (!s.stale) {
        clearInterval(_stalePoll); _stalePoll = null;
        state.scan = s; renderScan(s);
        return;
      }
      if (tries >= 20) {   // ~4 min baad haar maan lo
        clearInterval(_stalePoll); _stalePoll = null;
        const meta = $("scan-meta");
        if (meta) meta.innerHTML += ` <span class="scan-refresh-note">abhi bhi purana — 'Refresh scan' dabao.</span>`;
      }
    } catch (e) { /* network error — dobara try */ }
  }, 12000);
}

function topCard(r) {
  return `<div class="card top-card" data-sym="${esc(r.symbol)}">
    <div class="rank">#${r.rank}</div>
    <div style="display:flex;gap:12px;align-items:center">
      ${scoreRing(r.total)}
      <div>
        <div class="nm">${esc(r.name)}</div>
        <div class="muted small">${esc(r.symbol)} · ${esc(r.sector || "")}</div>
        <div style="margin-top:4px"><span class="badge ${ratingClass(r.rating)}">${esc(r.rating)}</span></div>
      </div>
    </div>
    <div class="muted small" style="margin-top:8px">${esc((r.why_buy && r.why_buy[0]) || "")}</div>
    <div class="muted small" style="margin-top:3px">${r.risks && r.risks[0] ? "<b style='color:var(--red)'>Risk:</b> " + esc(r.risks[0]) : ""}</div>
  </div>`;
}

/* ---------------- price action chips & card ---------------- */
function paTrendChip(t) {
  if (!t) return `<span class="muted">–</span>`;
  const broke = String(t).endsWith("?");
  const base = broke ? t.slice(0, -1) : t;
  const cls = base === "UP" ? "t-up" : base === "DOWN" ? "t-down" : "t-side";
  return `<span class="trend-chip ${cls}" title="PA structure (swing HH/HL/LH/LL)${broke ? " · structure break hai" : ""}">${esc(base)}${broke ? "↯" : ""}</span>`;
}
function paBoChip(b) {
  if (!b) return `<span class="muted">–</span>`;
  const map = { "fresh+": ["bo-fresh", "fresh +vol"], "fresh": ["bo-plain", "fresh"],
    "retest": ["bo-plain", "retest"], "false": ["bo-false", "false"],
    "breakdown": ["bo-down", "breakdown"] };
  const m = map[b] || ["bo-plain", esc(String(b))];
  return `<span class="bo-chip ${m[0]}" title="PA breakout status (volume-confirmed = fresh+)">${m[1]}</span>`;
}
function priceActionHTML(pa, sym) {
  if (!pa || !pa.available) {
    return `<div class="card"><h3>Price action — 10-point checklist</h3>
      <div class="muted small">Price action abhi available nahi (kam bars ya data-feed issue). Baaki report theek hai.</div></div>`;
  }
  const t = pa.timing || {}, st = pa.structure || {}, sr = pa.sr || {}, em = pa.emas || {},
    vol = pa.volume || {}, mtf = pa.mtf || {}, strg = pa.strength || {},
    cd = pa.candles || {}, bo = pa.breakout || {}, gp = pa.gaps || {}, pt = pa.patterns || {};
  const sigCls = { BUY_ZONE: "sig-buy", BREAKOUT: "sig-bo", TURN: "sig-turn",
    WAIT: "sig-wait", CAUTION: "sig-caution", NEUTRAL: "sig-wait" }[t.signal] || "sig-wait";
  const reasons = (t.reasons || []).map(x => `<li>${esc(x)}</li>`).join("");
  const fmtZ = z => z ? `<b>₹${fmtN(z.price, 1)}</b> <span class="muted small">(${z.dist_pct >= 0 ? "+" : ""}${fmtN(z.dist_pct, 1)}% · ${z.touches}x touch${z.major ? " · major" : ""})</span>` : "–";
  const mtfChip = v => `<span class="trend-chip ${v === "UP" ? "t-up" : v === "DOWN" ? "t-down" : "t-side"}">${esc(v || "NA")}</span>`;
  const candles = (cd.patterns || []).map(p => `<li><span class="dot dot-${p.bias === "bullish" ? "p" : p.bias === "bearish" ? "n" : "m"}"></span> ${esc(p.pattern)} <span class="muted small">${esc(String(p.date).slice(5))}</span></li>`).join("")
    || `<li class="muted">last 6 sessions me koi named pattern nahi</li>`;
  const gaps = (gp.list || []).map(g => `<li>${esc(String(g.date).slice(5))} <b class="${g.direction === "up" ? "chg-up" : "chg-down"}">${g.direction === "up" ? "gap-up" : "gap-down"} ${fmtN(g.pct, 1)}%</b>${g.filled ? ' <span class="muted small">filled</span>' : ' <span class="muted small">OPEN</span>'} <span class="muted small">${esc(g.type || "")}</span></li>`).join("")
    || `<li class="muted">40 sessions me gap nahi</li>`;
  const pats = (pt.list || []).map(p => `<li><b>${esc(p.pattern)}</b> <span class="pa-status">${esc(p.status)}</span>${p.neckline != null ? ` · neckline ₹${fmtN(p.neckline, 0)}` : ""}${p.trigger != null ? ` · trigger ₹${fmtN(p.trigger, 0)}` : ""}${p.target != null ? ` · target ₹${fmtN(p.target, 0)}` : ""}<div class="muted small">${esc(p.note || "")}</div></li>`).join("")
    || `<li class="muted">koi pattern detect nahi hua (limited set — DB/DT, H&S, flags)</li>`;
  const zones = (sr.zones || []).slice(0, 5).map(z => `<span class="chip">${z.kind === "support" ? "S" : "R"} ₹${fmtN(z.price, 0)} <span class="muted">${z.touches}x</span></span>`).join(" ");
  const boKey = { fresh_breakout: bo.volume_confirmed ? "fresh+" : "fresh", breakout_retest: "retest",
    false_breakout: "false", false_breakdown: "false", fresh_breakdown: "breakdown",
    breakdown: "breakdown" }[bo.status] || null;
  return `<div class="card"><h3>Price action — 10-point checklist ${aiBtn("price_action", sym)}</h3>
    <div class="pa-sig ${sigCls}"><b>${esc(t.signal || "NEUTRAL")}</b><span class="pa-sig-note">${esc(pa.summary || "")}</span></div>
    <ul class="pct-list" style="margin:2px 0 10px">${reasons}</ul>
    <div class="grid cols-2" style="align-items:stretch">
      <div class="pa-box"><b>1 · Market structure (HH/HL)</b>
        <div class="pa-kv"><span>Trend</span><span>${paTrendChip(st.verdict)}${st.broke ? ` <span class="muted small">${esc(st.broke_note || "")}</span>` : ""}</span></div>
        <div class="pa-kv"><span>Swings</span><span class="muted small">${esc(st.note || "")}</span></div>
        <div class="pa-kv"><span>Last swing hi/lo</span><span class="muted">₹${fmtN(st.last_swing_high, 1)} / ₹${fmtN(st.last_swing_low, 1)}</span></div>
      </div>
      <div class="pa-box"><b>8 · Multi-timeframe</b>
        <div class="pa-kv"><span>D / W / M</span><span>${mtfChip(mtf.daily)} ${mtfChip(mtf.weekly)} ${mtfChip(mtf.monthly)}</span></div>
        <div class="pa-kv"><span>Alignment</span><span class="muted">${esc(mtf.alignment || "")}</span></div>
      </div>
      <div class="pa-box"><b>2 · Support / resistance</b>
        <div class="pa-kv"><span>Support</span><span>${fmtZ(sr.support)}</span></div>
        <div class="pa-kv"><span>Resistance</span><span>${fmtZ(sr.resistance)}</span></div>
        <div class="pa-kv"><span>Zones</span><span>${zones || "–"}</span></div>
        ${(sr.flips || []).length ? `<div class="muted small">Flip: ${esc(sr.flips.join(" · "))}</div>` : ""}
      </div>
      <div class="pa-box"><b>4 · Breakout / breakdown</b>
        <div class="pa-kv"><span>Status</span><span>${boKey ? paBoChip(boKey) : '<span class="muted">none</span>'}</span></div>
        ${bo.status && bo.status !== "none" ? `<div class="pa-kv"><span>Level / bars ago</span><span class="muted">₹${fmtN(bo.level, 1)} · ${bo.bars_ago}d</span></div>` : ""}
        ${bo.note ? `<div class="muted small">${esc(bo.note)}</div>` : ""}
      </div>
      <div class="pa-box"><b>5 · Volume</b>
        <div class="pa-kv"><span>avg20 / avg60</span><span class="muted">${fmtN(vol.avg20, 0)} / ${fmtN(vol.avg60, 0)}</span></div>
        <div class="pa-kv"><span>Last vs avg20</span><span>${vol.last_ratio != null ? "×" + fmtN(vol.last_ratio, 1) : "–"}${vol.spike ? " <b>spike</b>" : ""}</span></div>
        <div class="pa-kv"><span>Buying pressure (20d)</span><span>${vol.buying_pressure_pct != null ? fmtN(vol.buying_pressure_pct, 0) + "%" : "–"}</span></div>
        <div class="muted small">${esc(vol.read || "")}</div>
      </div>
      <div class="pa-box"><b>6 · EMAs (20/50/100/200)</b>
        <div class="pa-kv"><span>Values</span><span class="muted small">${[["20", em.ema20], ["50", em.ema50], ["100", em.ema100], ["200", em.ema200]].map(x => x[1] == null ? x[0] + ":–" : x[0] + ":₹" + fmtN(x[1], 0)).join(" · ")}</span></div>
        <div class="pa-kv"><span>Stack</span><span>${em.stack === "bullish" ? '<b class="chg-up">bullish</b>' : em.stack === "bearish" ? '<b class="chg-down">bearish</b>' : "mixed"}</span></div>
        <div class="pa-kv"><span>Golden / death cross</span><span class="muted">${em.golden_cross_days != null ? em.golden_cross_days + "d ago" : "–"} / ${em.death_cross_days != null ? em.death_cross_days + "d ago" : "–"}</span></div>
      </div>
      <div class="pa-box"><b>3 · Candlesticks (last 6 sessions)</b><ul class="pct-list">${candles}</ul></div>
      <div class="pa-box"><b>7 · Gaps (40 sessions)</b><ul class="pct-list">${gaps}</ul></div>
      <div class="pa-box"><b>9 · Chart patterns</b><ul class="pct-list">${pats}</ul></div>
      <div class="pa-box"><b>10 · Strength / weakness</b>
        <div class="pa-kv"><span>Verdict</span><span><b>${esc(strg.verdict || "neutral")}</b></span></div>
        <div class="pa-kv"><span>Last bar body / close-pos</span><span class="muted">${strg.body_pct != null ? fmtN(strg.body_pct, 0) + "%" : "–"} / ${strg.close_pos_pct != null ? fmtN(strg.close_pos_pct, 0) + "%" : "–"}</span></div>
        <div class="pa-kv"><span>Consolidation</span><span>${strg.consolidation ? "yes (10-bar range &lt;5%)" : "no"}</span></div>
        ${strg.rejection ? `<div class="muted small">${esc(strg.rejection)}</div>` : ""}
      </div>
    </div>
    <div class="muted small" style="margin-top:8px">${esc((pa.warnings || []).join(" · "))}</div>
    <div class="muted small"><b>Price action = timing layer.</b> "Kya khareedna hai" wo 5-pillar score decide karta hai — PA us verdict ko override nahi karta.</div>
  </div>`;
}

const RANK_COLS = [
  { k: "rank", l: "#", n: true }, { k: "name", l: "Stock" }, { k: "sector", l: "Sector" },
  { k: "rating", l: "Rating" }, { k: "total", l: "Score", n: true },
  { k: "price", l: "Price", n: true }, { k: "pe", l: "P/E", n: true }, { k: "pb", l: "P/B", n: true },
  { k: "roe_pct", l: "ROE%", n: true }, { k: "cagr_profit_pct", l: "Prft CAGR%", n: true },
  { k: "yoy_profit_pct", l: "YoY%", n: true }, { k: "de", l: "D/E", n: true },
  { k: "div_yield_pct", l: "Div%", n: true }, { k: "rsi14", l: "RSI", n: true },
  { k: "rs_12m", l: "RS-12M", n: true },
  { k: "trend", l: "Trend" }, { k: "breakout", l: "Breakout" },
  { k: "why", l: "Why (engine)" },
];

function renderTable(rows) {
  const dir = state.sort.dir;
  const key = state.sort.key;
  const sorted = [...rows].sort((a, b) => {
    let x = a[key], y = b[key];
    if (typeof x === "string") { return dir * x.localeCompare(y); }
    x = x === null || x === undefined ? -Infinity : x;
    y = y === null || y === undefined ? -Infinity : y;
    return dir * (x - y);
  });
  const th = RANK_COLS.map(c =>
    `<th data-key="${c.k}" class="${c.n ? 'num' : ''}">${c.l}${key === c.k ? (dir === -1 ? " ▾" : " ▴") : ""}</th>`).join("");
  const trs = sorted.map(r => {
    const why = (r.why_buy && r.why_buy[0]) || "";
    return `<tr class="rowlink" data-sym="${esc(r.symbol)}">
     <td class="num">${r.rank}</td>
     <td><b>${esc(r.name)}</b><div class="muted" style="font-size:11px">${esc(r.symbol)}</div></td>
     <td class="muted">${esc(r.sector || "")}</td>
     <td><span class="badge ${ratingClass(r.rating)}">${esc(r.rating)}</span></td>
     <td class="num"><b>${fmtN(r.total, 1)}</b></td>
     <td class="num">${fmtPrice(r.price)}<div class="${r.live ? 'live-chip' : (r.chg_1d >= 0 ? 'chg-up' : 'chg-down')}" style="font-size:11px">${r.live ? `● live ${esc(r.price_time || "")}` : (r.chg_1d == null ? "" : fmtPct(r.chg_1d))}</div>${r.live ? `<div class="${r.chg_1d >= 0 ? 'chg-up' : 'chg-down'}" style="font-size:11px">${r.chg_1d == null ? "" : fmtPct(r.chg_1d)}</div>` : ""}</td>
     <td class="num">${okChip(r.pe)}</td>
     <td class="num">${okChip(r.pb)}</td>
     <td class="num">${okChip(r.roe_pct)}</td>
     <td class="num">${okChip(r.cagr_profit_pct)}</td>
     <td class="num">${okChip(r.yoy_profit_pct)}</td>
     <td class="num">${okChip(r.de)}</td>
     <td class="num">${okChip(r.div_yield_pct)}</td>
     <td class="num">${okChip(r.rsi14)}</td>
     <td class="num">${r.rs_12m == null ? "–" : fmtPct(r.rs_12m, 0)}</td>
     <td>${paTrendChip(r.trend)}</td>
     <td>${paBoChip(r.breakout)}</td>
     <td class="muted" style="white-space:normal;min-width:220px;font-size:12px">${esc(why)}</td>
   </tr>`;
  }).join("");
  $("rank-table").innerHTML = `<table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
  $("rank-table").style.maxHeight = "640px";
  $("rank-table").querySelectorAll("th").forEach(el => el.addEventListener("click", () => {
    const k = el.dataset.key;
    if (state.sort.key === k) state.sort.dir *= -1; else state.sort = { key: k, dir: k === "name" || k === "sector" ? 1 : -1 };
    renderTable(state.scan.rows);
  }));
  $("rank-table").querySelectorAll("tr.rowlink").forEach(el => el.addEventListener("click", () => openStockTab(el.dataset.sym)));
}

/* CSV export */
function exportCSV() {
  if (!state.scan || !state.scan.rows) return;
  const cols = ["symbol", "name", "sector", "rating", "total", "price", "pe", "pb", "roe_pct",
    "roce_pct", "de", "cagr_profit_pct", "yoy_profit_pct", "div_yield_pct", "rsi14", "rs_12m",
    "trend", "breakout", "fscore", "market_cap_cr", "above_200dma", "data_quality"];
  const head = cols.join(",");
  const lines = state.scan.rows.map(r => cols.map(c => JSON.stringify(r[c] ?? "")).join(","));
  const blob = new Blob([head + "\n" + lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "bharatinvest_ranking.csv";
  a.click();
}

/* ---------------- screener ---------------- */
function applyFilters() {
  if (!state.scan || !state.scan.rows) { $("screen-table").innerHTML = errorHTML("No scan data yet — open Dashboard tab first."); return; }
  const get = (id) => $(id).value.trim();
  const num = (id) => { const v = get(id); return v === "" ? null : parseFloat(v); };
  const fpe = num("f-pe"), froe = num("f-roe"), fde = num("f-de"), fcagr = num("f-cagr"),
    fdy = num("f-dy"), fscore = num("f-score"), r1 = num("f-rsi1"), r2 = num("f-rsi2"),
    fsec = get("f-sector") || "", frt = get("f-rating") || "", above = $("f-above200").checked,
    ftr = get("f-trend") || "", fbo = get("f-breakout") || "";
  const out = state.scan.rows.filter(r => {
    if (fsec && r.sector !== fsec) return false;
    if (frt && !(r.rating || "").startsWith(frt)) return false;
    if (fpe !== null && !(r.pe !== null && r.pe <= fpe)) return false;
    if (froe !== null && !(r.roe_pct !== null && r.roe_pct >= froe)) return false;
    if (fde !== null && !(r.de !== null && r.de <= fde)) return false;
    if (fcagr !== null && !(r.cagr_profit_pct !== null && r.cagr_profit_pct >= fcagr)) return false;
    if (fdy !== null && !(r.div_yield_pct !== null && r.div_yield_pct >= fdy)) return false;
    if (fscore !== null && !((r.total ?? 0) >= fscore)) return false;
    if (r1 !== null && r2 !== null && !(r.rsi14 !== null && r.rsi14 >= r1 && r.rsi14 <= r2)) return false;
    if (above && !r.above_200dma) return false;
    if (ftr && (r.trend || "") !== ftr) return false;
    if (fbo && (r.breakout || "") !== fbo) return false;
    return true;
  });
  if (!out.length) { $("screen-table").innerHTML = `<div class="muted" style="padding:16px">No stocks match these filters. Loosen them (especially max P/E + min ROE together).</div>`; return; }
  state.sort = { key: "total", dir: -1 };
  renderTableScreen(out);
}
function renderTableScreen(rows) {
  // simple approach: temporarily point renderTable at screen-table by cloning function logic
  const sorted = [...rows].sort((a, b) => (b.total ?? 0) - (a.total ?? 0));
  const cols = ["rank", "name", "sector", "rating", "total", "price", "pe", "roe_pct", "cagr_profit_pct", "yoy_profit_pct", "de", "div_yield_pct", "rsi14", "rs_12m", "trend"];
  const labels = { rank: "#", name: "Stock", sector: "Sector", rating: "Rating", total: "Score", price: "Price", pe: "P/E", roe_pct: "ROE%", cagr_profit_pct: "CAGR%", yoy_profit_pct: "YoY%", de: "D/E", div_yield_pct: "Div%", rsi14: "RSI", rs_12m: "RS-12M", trend: "Trend" };
  const th = cols.map(c => `<th class="${c !== 'name' && c !== 'sector' ? 'num' : ''}">${labels[c]}</th>`).join("");
  const trs = sorted.map(r => `<tr class="rowlink" data-sym="${esc(r.symbol)}">
    <td class="num">${r.rank}</td>
    <td><b>${esc(r.name)}</b><div class="muted" style="font-size:11px">${esc(r.symbol)}</div></td>
    <td class="muted">${esc(r.sector || "")}</td>
    <td><span class="badge ${ratingClass(r.rating)}">${esc(r.rating)}</span></td>
    <td class="num"><b>${fmtN(r.total, 1)}</b></td>
    <td class="num">${fmtPrice(r.price)}</td>
    <td class="num">${okChip(r.pe)}</td><td class="num">${okChip(r.roe_pct)}</td>
    <td class="num">${okChip(r.cagr_profit_pct)}</td><td class="num">${okChip(r.yoy_profit_pct)}</td>
    <td class="num">${okChip(r.de)}</td><td class="num">${okChip(r.div_yield_pct)}</td>
    <td class="num">${okChip(r.rsi14)}</td>
    <td class="num">${r.rs_12m == null ? "–" : fmtPct(r.rs_12m, 0)}</td>
    <td>${paTrendChip(r.trend)}</td>
  </tr>`).join("");
  $("screen-table").innerHTML = `<div class="muted small" style="padding:8px 10px">${sorted.length} stocks match — click a row to open the deep-dive.</div>
    <table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
  $("screen-table").querySelectorAll("tr.rowlink").forEach(el => el.addEventListener("click", () => openStockTab(el.dataset.sym)));
}

/* ---------------- stock deep dive ---------------- */
async function analyzeSymbol(symbol) {
  const box = $("stock-detail");
  box.innerHTML = loadingHTML(`Analyzing ${symbol} — fetching price data, financials, valuation & news (first time ~5-10s)…`);
  let d;
  try {
    d = await (await fetch(`/api/analyze/${encodeURIComponent(symbol)}`)).json();
  } catch (e) {
    box.innerHTML = errorHTML("Analysis failed: " + esc(e.message)); return;
  }
  if (d.detail) { box.innerHTML = errorHTML(d.detail); return; }
  renderDetail(d);
}

function kvRow(k, v) { return `<div><b>${fmtN(v)}</b> <span>${esc(k)}</span></div>`; }
function pctChip(v, good, bad) {
  if (v === null || v === undefined) return `<span class="pill">n/a</span>`;
  const cls = v >= good ? "good" : v <= bad ? "bad" : "info";
  return `<span class="pill ${cls}">${esc(String(v))}</span>`;
}

function pillarBars(pillars, weights) {
  return pillars.map(p => `
    <div class="pillar-row">
      <div style="display:flex;justify-content:space-between;font-size:13px">
        <b>${esc(p.name)} <span class="muted small">(w ${(weights[p.name.toLowerCase()] * 100).toFixed(0)}%)</span></b>
        <b>${fmtN(p.score, 0)}/100</b>
      </div>
      <div class="pillar-bar"><div style="width:${Math.max(2, p.score)}%;background:${ringColor(p.score)}"></div></div>
    </div>`).join("");
}

function sensTable(dcf) {
  if (!dcf.sensitivity) return "";
  const head = `<tr><th>disk_rate ↓ / growth →</th>${dcf.sensitivity.map(r => `<th>${r.discount}%</th>`).join("")}</tr>`;
  const rows = [0, 1, 2].map(i => `<tr><td>g${i - 1 < 0 ? "" : "+"}${i === 0 ? " base" : ""}</td>${dcf.sensitivity.map(r => `<td>₹${fmtN(r.values[i], 0)}</td>`).join("")}</tr>`).join("");
  return `<table class="sens">${head}${rows}</table>`;
}

function finTableHTML(st) {
  if (!st || !st.years || !st.years.length || !st.rows) return "";
  const th = `<tr><th></th>${st.years.map(y => `<th>${esc(y)}</th>`).join("")}</tr>`;
  const rows = Object.entries(st.rows).filter(([, v]) => v.some(x => x !== null))
    .map(([label, vals]) => `<tr><td>${esc(label)}</td>${vals.map(v =>
      `<td>${v === null || v === undefined ? "–" : fmtN(v, 0)}</td>`).join("")}</tr>`).join("");
  return `<table class="fin-table">${th}${rows}</table>`;
}

function screenerFinHTML(s, sym) {
  const st = s.statements || {};
  const secs = [
    ["profit_loss", "Profit &amp; Loss"], ["balance_sheet", "Balance Sheet"],
    ["cash_flow", "Cash Flow"], ["quarterly", "Quarterly Results"], ["ratios", "Ratios"],
  ].filter(([k]) => st[k]);
  if (!secs.length) return "";
  const blocks = secs.map(([k, label]) =>
    `<details class="finsec"${k === "profit_loss" ? " open" : ""}><summary>${label} (${st[k].years.length} years)</summary>${finTableHTML(st[k])}</details>`).join("");
  return `<div class="card" style="margin-bottom:14px">
    <h3 style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap"><span>10-year financial statements ${aiBtn("screener", sym)}</span>
      <span class="muted small">${s.consolidated === false ? "Standalone" : "Consolidated"} · Screener.in · figures ₹ Crore · scraped ${esc(fmtIST(s.scraped_at))} IST</span></h3>
    ${blocks}</div>`;
}

function renderDetail(d) {
  const q = d.quote || {}, f = d.fundamental || {}, t = d.technical || {},
    comp = d.scoring || {}, dcf = d.dcf || {}, sc = d.scenarios || {}, dq = d.data_quality || {},
    pl = d.pledge || {};
  const fs = f.fscore || {};
  const price = q.price ?? t.price;
  state.curSym = d.symbol;

  let html = `
  <div class="card" style="margin-bottom:14px">
    <div class="detail-head">
      <div>
        <h2>${esc(d.name)} <span class="muted" style="font-size:13px">· ${esc(d.symbol)} · ${esc(d.sector || "")}</span></h2>
        <div style="margin-top:4px"><span class="badge ${ratingClass(comp.rating)}">${esc(comp.rating || "")}</span>
          <span class="muted small">${esc(comp.label || "")}</span>
          <span class="dq dq-${(dq.grade || "LOW").toLowerCase()}">DATA ${esc(dq.grade)}</span>
          ${d.bse_resolve ? `<span class="pill info">BSE ${esc(d.bse_resolve.bse_code)} → ${esc(d.bse_resolve.yahoo_symbol)}</span>` : ""}
          ${d.cached ? '<span class="pill info">cached</span>' : ""}
        </div>
      </div>
      ${scoreRing(comp.total ?? 0)}
      <div style="margin-left:auto;text-align:right">
        <div style="font-size:24px;font-weight:800">${fmtPrice(price)}</div>
        <div class="${(q.chg_1d_pct ?? 0) >= 0 ? 'chg-up' : 'chg-down'}">${q.chg_1d_pct === null || q.chg_1d_pct === undefined ? "" : fmtPct(q.chg_1d_pct, 2)} today</div>
        <div class="muted small">as of ${q.live ? `<span class="live-chip">● live ${esc(q.as_of || "")} IST</span>` : (fmtDateStr(q.date || t.price_date || "") + " (EOD)")}</div>
        <div class="muted small">Report: ${esc(fmtIST(d.generated_at))} IST</div>
        <div class="muted small">Mcap ₹${fmtN((f.market_cap ?? 0) / 1e7, 0)} Cr</div>
        ${aiBtn("report", d.symbol, "✨ Explain page", "ai-report-out")}
      </div>
    </div>
    ${aiEnabled() ? `<div class="ai-ask-row">
        <input id="ai-ask-input" placeholder="AI se kuch bhi poochho — e.g. is stock me abhi invest karna theek rahega?"/>
        <button class="btn btn-sm btn-primary" id="ai-ask-go">Ask AI</button>
      </div>
      <div class="ai-out" id="ai-ask-out"></div>
      <div class="ai-out" id="ai-report-out"></div>`
      : `<div class="ai-off-note">✨ AI explain off hai — topbar ke <b>✨ AI</b> se free Google key daalo, phir har card beginner bhasha me samajh aayega (terms + glossary ke saath).</div>`}
  </div>

  <div class="grid cols-3" style="margin-bottom:14px">
    <div class="card"><h3>Five pillars (weighted)</h3>${pillarBars(comp.pillars || [], comp.weights || {})}</div>
    <div class="card"><h3>Why buy?</h3>${(comp.why_buy || []).map(x => `<span class="pill good">${esc(x)}</span>`).join("") || '<span class="muted small">Nothing notable</span>'}</div>
    <div class="card"><h3>Risks &amp; red flags</h3>${(comp.risks || []).map(x => `<span class="pill bad">${esc(x)}</span>`).join("")}</div>
  </div>

  <div class="grid cols-2" style="margin-bottom:14px">`;

  /* DCF + scenarios side by side */
  html += `<div class="card"><h3>DCF / EPV intrinsic value ${aiBtn("dcf", d.symbol)}</h3>`;
  if (dcf.available) {
    html += `<div class="kv" style="margin-bottom:8px">
      <div><b>₹${fmtN(dcf.intrinsic_per_share, 1)}</b> <span>intrinsic/share (${esc(dcf.source || "")})</span></div>
      <div><b>${fmtN(dcf.mos_pct, 0)}%</b> <span>margin of safety vs ₹${fmtN(price, 1)}</span></div>
      <div><b>${fmtN(dcf.growth_used_pct, 1)}%</b> <span>growth assumed (capped)</span></div>
      <div><b>11%</b> <span>discount rate, 4% terminal</span></div>
    </div>${sensTable(dcf)}<div class="muted small" style="margin-top:6px">${esc(dcf.docs || "")}</div>`;
  } else {
    html += `<div class="muted small">Insufficient FCF/profit history for a DCF on this stock — rely on PE vs peers and qualitative checks.</div>`;
  }
  html += `</div>`;

  html += `<div class="card"><h3>1-year scenarios (estimates only)</h3>`;
  if (sc.available) {
    html += `<div class="scenario">
      <div class="sc-bull"><div class="lbl">BULL</div><div class="val">₹${fmtN(sc.bull, 0)}</div></div>
      <div class="sc-base"><div class="lbl">BASE</div><div class="val">₹${fmtN(sc.base, 0)}</div></div>
      <div class="sc-bear"><div class="lbl">BEAR</div><div class="val">₹${fmtN(sc.bear, 0)}</div></div>
    </div><div class="muted small" style="margin-top:8px">${esc(sc.note || "")}</div>`;
  } else { html += `<div class="muted small">Not enough data.</div>`; }
  html += `</div></div>`;

  /* fundamentals + technicals */
  html += `<div class="grid cols-2" style="margin-bottom:14px">
    <div class="card"><h3>Fundamentals ${aiBtn("fundamentals", d.symbol)}</h3><div class="kv">
      <div><b>${okChip(f.pe)}</b><span>P/E</span></div>
      <div><b>${okChip(f.forward_pe)}</b><span>Forward P/E</span></div>
      <div><b>${okChip(f.pb)}</b><span>P/B</span></div>
      <div><b>${okChip(f.roe_pct)}%</b><span>ROE</span></div>
      <div><b>${okChip(f.roce_pct)}%</b><span>ROCE</span></div>
      <div><b>${okChip(f.debt_to_equity)}</b><span>Debt / Equity</span></div>
      <div><b>${okChip(f.cfo_pat)}</b><span>CFO / PAT (cash quality)</span></div>
      <div><b>${f.fcf_cr === null || f.fcf_cr === undefined ? "–" : "₹" + fmtN(f.fcf_cr, 0) + " Cr"}</b><span>Free cash flow</span></div>
      <div><b>${okChip(f.cagr_profit_pct)}%</b><span>Profit CAGR</span></div>
      <div><b>${okChip(f.cagr_rev_pct)}%</b><span>Revenue CAGR</span></div>
      <div><b>${okChip(f.yoy_rev_pct)}%</b><span>Latest YoY revenue</span></div>
      <div><b>${okChip(f.yoy_profit_pct)}%</b><span>Latest YoY profit</span></div>
      <div><b>${okChip(f.opm_pct)}%</b><span>Operating margin</span></div>
      <div><b>${okChip(f.npm_pct)}%</b><span>Net margin</span></div>
      <div><b>${fs.score === null || fs.score === undefined ? "–" : fs.score + "/" + fs.max}</b><span>Piotroski F-Score${fs.complete === false ? " (partial)" : ""}</span></div>
      <div><b>${okChip(f.div_yield_pct)}%</b><span>Dividend yield</span></div>
      <div><b>${f.analyst_rating ? esc(f.analyst_rating) : "–"}</b><span>Street rating</span></div>
      <div><b>${f.target_mean_price ? fmtPrice(f.target_mean_price) : "–"}</b><span>Street target</span></div>
    </div></div>
    <div class="card"><h3>Technicals &amp; levels ${aiBtn("technicals", d.symbol)}</h3><div class="kv">
      <div><b>${okChip(t.rsi14)}</b><span>RSI(14) <span class="muted">${esc(t.rsi_status || "")}</span></span></div>
      <div><b>${t.macd === null || t.macd === undefined ? "–" : fmtN(t.macd, 1) + " / " + fmtN(t.macd_signal, 1)}</b><span>MACD / signal</span></div>
      <div><b>${t.sma50 === null || t.sma50 === undefined ? "–" : (price > t.sma50 ? "Above" : "Below")}</b><span>vs 50-DMA (${okChip(t.sma50)})</span></div>
      <div><b>${t.sma200 === null || t.sma200 === undefined ? "–" : (price > t.sma200 ? "Above" : "Below")}</b><span>vs 200-DMA (${okChip(t.sma200)})</span></div>
      <div><b>${t.golden_cross !== null && t.golden_cross !== undefined ? t.golden_cross + "d ago" : "–"}</b><span>Golden cross</span></div>
      <div><b>${t.death_cross !== null && t.death_cross !== undefined ? t.death_cross + "d ago" : "–"}</b><span>Death cross</span></div>
      <div><b>${fmtPct(t.chg_3m_pct, 0)}</b><span>3-month move</span></div>
      <div><b>${fmtPct(t.chg_12m_pct, 0)}</b><span>12-month move</span></div>
      <div><b>${t.rs_12m == null ? "–" : fmtPct(t.rs_12m, 0)}</b><span>Relative strength vs NIFTY (12M)</span><div></div></div>
      <div><b>${t.pos_52w === null || t.pos_52w === undefined ? "–" : fmtN(t.pos_52w, 0) + "%"}</b><span>52-week position</span></div>
      <div><b>${fmtPct(t.drawdown_52w, 0)}</b><span>Drawdown from 52w high (${fmtPrice(t.high_52w)} ↔ ${fmtPrice(t.low_52w)})</span></div>
      <div><b>${okChip(t.annual_vol_pct)}%</b><span>Annualised volatility</span></div>
      <div><b>${t.supports && t.supports.length ? t.supports.map(x => "₹" + fmtN(x, 0)).join(", ") : "–"}</b><span>Supports</span></div>
      <div><b>${t.resistances && t.resistances.length ? t.resistances.map(x => "₹" + fmtN(x, 0)).join(", ") : "–"}</b><span>Resistances</span></div>
      <div><b>${pl.available ? fmtN(pl.pledged_pct, 1) + "%" : "–"}</b><span>Promoter pledge${pl.available ? "" : " (check on screener.in)"}</span></div>
    </div></div>
  </div>`;

  /* price action — 10-category timing layer (score ke saath, override nahi) */
  html += priceActionHTML(d.price_action, d.symbol);

  /* 10-year financial statements (Screener.in) — full text tables */
  if (d.screener && d.screener.available) html += screenerFinHTML(d.screener, d.symbol);

  /* chart */
  if (t.chart && t.chart.dates && t.chart.dates.length > 30) {
    html += `<div class="card" style="margin-bottom:14px">
      <h3>2-year price &amp; moving averages</h3>
      <div style="height:260px"><canvas id="px-chart"></canvas></div>
      <div style="height:110px;margin-top:8px"><canvas id="rsi-chart"></canvas></div>
    </div>`;
  }

  /* news */
  const news = d.news || [];
  html += `<div class="grid cols-2" style="margin-bottom:14px">
    <div class="card"><h3>Recent news (Google News)</h3>
      ${news.length ? news.map(n => `<div class="news-item">
        <span class="dot dot-${n.sentiment === "positive" ? "p" : n.sentiment === "negative" ? "n" : "m"}"></span>
        <a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a>
        <div class="muted small">${esc(n.source || "")} · ${esc(n.date ? String(n.date).slice(0, 16) : "")}</div>
      </div>`).join("") : `<div class="muted small">No news found.</div>`}
    </div>
    <div class="card"><h3>Engine checklist (Quality pillar) ${aiBtn("checklist", d.symbol)}</h3>
      <div class="chk-grid">
      ${((comp.pillars || []).find(p => p.name === "Quality")?.checks || []).map(c => `
        <div class="chk-item"><b>${esc(c.metric)}</b>
          <span class="muted">${esc(c.note || "")}</span>
          <div style="color:var(--navy);font-weight:700;font-size:12px">+${fmtN(c.pts, 1)} pts</div></div>`).join("")}
      </div>
    </div>
  </div>`;

  /* pros & cons (Screener.in) + official NSE announcements */
  const pc = (d.screener || {}).proscons || {};
  const hasPC = ((pc.pros || []).length + (pc.cons || []).length) > 0;
  html += `<div class="grid cols-2" style="margin-bottom:14px">
    <div class="card"><h3>Pros &amp; cons (Screener.in · x-ray)</h3>
      ${hasPC ? `<div class="small">
        ${(pc.pros || []).length ? `<b style="color:var(--green)">PROS</b><ul class="pct-list">${pc.pros.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        ${(pc.cons || []).length ? `<b style="color:var(--red)">CONS</b><ul class="pct-list">${pc.cons.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      </div>` : `<div class="muted small">Screener.in ne is stock ke liye pros/cons abhi list nahi kiya (kuch companies khaali rakhte hain).</div>`}
    </div>
    <div class="card"><h3>Official announcements (NSE)</h3><div id="ann-list" class="muted small">Loading…</div></div>
  </div>`;

  /* verdict */
  html += `<div class="card" style="margin-bottom:14px">
    <h3>Investment verdict — ${esc(comp.rating || "")} ${aiBtn("verdict", d.symbol)}</h3>
    <p style="margin:6px 0"><b>${esc(comp.label || "")}.</b> Composite score ${fmtN(comp.total, 1)}/100 across the five pillars (Quality 30% · Growth 20% · Valuation 20% · Technical 15% · Risk 15%).</p>
    <p style="margin:6px 0"><b>The case:</b> ${(comp.why_buy || []).join(" ")} ${(comp.why_buy || []).length ? "" : "No strong positives detected."}</p>
    <p style="margin:6px 0"><b>The cautions:</b> ${(comp.risks || []).join(" ")}</p>
    <p class="muted small" style="margin:10px 0 0">Data confidence: <b>${esc(dq.grade)}</b>. Engine output is research, not advice — cross-check on NSE, screener.in and the latest annual report, especially promoter pledge, auditor notes and management commentary, before investing.</p>
  </div>`;

  $("stock-detail").innerHTML = html;
  drawCharts(t);
  loadAnnouncements(d.symbol);
}

async function loadAnnouncements(symbol) {
  const box = $("ann-list");
  if (!box) return;
  box.innerHTML = loadingHTML("NSE announcements aa rahe hain…");
  try {
    const a = await (await fetch(`/api/announcements/${encodeURIComponent(symbol)}`)).json();
    if (a && a.available && (a.items || []).length) {
      box.innerHTML = a.items.map(n => `<div class="ann-item">
        <div class="muted small">${esc(String(n.date || "").slice(0, 20))}${n.category ? ` · <span class="cat">${esc(n.category)}</span>` : ""}</div>
        ${n.pdf ? `<a href="${esc(n.pdf)}" target="_blank" rel="noopener">${esc(String(n.title || "").slice(0, 220))}</a>`
                : esc(String(n.title || "").slice(0, 220))}
      </div>`).join("")
        + `<div class="muted small" style="margin-top:6px">Source: NSE official announcements feed (latest ${a.items.length}). Verdict hi dal ke chalo — official filings sachai ki asli taakat hain.</div>`;
    } else {
      box.innerHTML = `<div class="muted small">No recent announcements NSE ne is symbol ke liye diya (ya data feed busy tha).</div>`;
    }
  } catch (e) {
    box.innerHTML = `<div class="muted small">NSE feed is waqt blocked/late — thodi der baad page refresh karo.</div>`;
  }
}

function drawCharts(t) {
  if (typeof Chart === "undefined") return;
  const px = $("px-chart"), rr = $("rsi-chart");
  if (!px || !rr) return;
  state.charts.forEach(c => { try { c.destroy(); } catch (e) { } });
  state.charts = [];
  const labels = t.chart.dates;
  const skip = Math.max(1, Math.round(labels.length / 120));
  const pack = (arr) => arr.map((v, i) => v);
  Chart.defaults.font.size = 10;
  state.charts.push(new Chart(px, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Close", data: pack(t.chart.close), borderColor: "#153a5b", borderWidth: 2, pointRadius: 0, tension: 0 },
        { label: "SMA20", data: pack(t.chart.sma20), borderColor: "#e07b24", borderWidth: 1, pointRadius: 0, tension: 0 },
        { label: "SMA50", data: pack(t.chart.sma50), borderColor: "#b7791f", borderWidth: 1, pointRadius: 0, tension: 0 },
        { label: "SMA200", data: pack(t.chart.sma200), borderColor: "#0c7a43", borderWidth: 1.4, borderDash: [6, 3], pointRadius: 0, tension: 0 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      scales: {
        x: { ticks: { maxTicksLimit: 10 }, grid: { display: false } },
        y: { position: "right", grid: { color: "#eef1f4" } }
      },
      plugins: { legend: { labels: { boxWidth: 10 } } }
    }
  }));
  state.charts.push(new Chart(rr, {
    type: "line",
    data: {
      labels,
      datasets: [{ label: "RSI(14)", data: pack(t.chart.rsi), borderColor: "#6b7280", borderWidth: 1.4, pointRadius: 0, tension: 0.2 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 10 }, grid: { display: false } },
        y: { min: 0, max: 100, ticks: { stepSize: 50 }, grid: { color: "#eef1f4" } }
      }
    }
  }));
}

/* ---------------- events ---------------- */
$("btn-analyze").addEventListener("click", () => {
  const s = ($("stock-input").value || "").trim().toUpperCase();
  if (s) analyzeSymbol(s);
});
$("stock-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btn-analyze").click(); });
$("btn-rescan").addEventListener("click", async () => {
  $("top-cards").innerHTML = loadingHTML("Re-scanning all stocks (takes 30–90s first time; cached after warp-up)…");
  try { state.scan = await (await fetch("/api/scan?refresh=1")).json(); renderScan(state.scan); }
  catch (e) { $("top-cards").innerHTML = errorHTML("Scan failed: " + esc(e.message)); }
});
$("btn-csv").addEventListener("click", exportCSV);
$("btn-apply").addEventListener("click", applyFilters);
$("btn-clear").addEventListener("click", () => {
  ["f-pe", "f-roe", "f-de", "f-cagr", "f-dy", "f-score", "f-rsi1", "f-rsi2"].forEach(id => $(id).value = "");
  $("f-sector").value = ""; $("f-rating").value = ""; $("f-trend").value = ""; $("f-breakout").value = ""; $("f-above200").checked = false;
  applyFilters();
});

/* portfolio form events */
$("btn-pf-add") && $("btn-pf-add").addEventListener("click", () => {
  const sym = ($("pf-sym").value || "").trim().toUpperCase();
  const qty = parseFloat($("pf-qty").value), avg = parseFloat($("pf-avg").value);
  const dt = $("pf-date").value || "";
  if (!sym || !(qty > 0) || !(avg > 0)) { $("pf-msg").textContent = "Symbol + Qty + Avg teeno zaroori hai."; return; }
  const cur = ((state.port && state.port.holdings) || []).filter(h => h.symbol !== sym);
  cur.push({ symbol: sym, qty, avg, bought: dt });
  ["pf-sym", "pf-qty", "pf-avg"].forEach(id => $(id).value = ""); $("pf-date").value = "";
  postPortfolio(cur, "Add/Update ho gaya " + sym);
});
$("pf-file") && $("pf-file").addEventListener("change", () => { /* file selected — btn dabane par read hoga */ });
$("btn-pf-import") && $("btn-pf-import").addEventListener("click", () => {
  const f = $("pf-file").files && $("pf-file").files[0];
  if (!f) { $("pf-msg").textContent = "Pehle CSV file choose karo."; return; }
  const rd = new FileReader();
  rd.onload = async () => {
    try {
      const r = await fetch("/api/portfolio/import", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csv: String(rd.result || "") }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || ("HTTP " + r.status));
      const res = await r.json(); state.port = res; renderPortfolio();
      const ir = res.import_result || {};
      $("pf-msg").textContent = `Import done — ${ir.added || 0} holdings set, khaali/invalid rows skip.`;
    } catch (e) { $("pf-msg").textContent = "Import fail: " + e.message; }
  };
  rd.readAsText(f);
});
$("btn-pf-clear") && $("btn-pf-clear").addEventListener("click", () => {
  if (!confirm("Poora portfolio clear kar dein? (portfolio.json khaali ho jaayega)")) return;
  fetch("/api/portfolio/clear", { method: "POST" }).then(r => r.json()).then(p => {
    state.port = p; renderPortfolio(); $("pf-msg").textContent = "Clear ho gaya.";
  }).catch(e => { $("pf-msg").textContent = "Clear fail: " + e.message; });
});

/* ---------------- portfolio tab ---------------- */
function loadPortfolio() {
  const t = $("pf-totals");
  if (t) t.innerHTML = `<div class="card muted small">${loadingHTML("Portfolio load ho raha he…")}</div>`;
  if ($("pf-table")) $("pf-table").innerHTML = "";
  if ($("pf-meta")) $("pf-meta").textContent = "";
  if ($("pf-msg")) $("pf-msg").textContent = "";
  fetch("/api/portfolio").then(r => r.json()).then(p => { state.port = p; renderPortfolio(); })
    .catch(e => { if (t) t.innerHTML = `<div class="card">${errorHTML("Portfolio failed: " + esc(e.message))}</div>`; });
}

function renderPortfolio() {
  const p = state.port; if (!p) return;
  const tt = p.totals || {};
  const n12 = p.nifty_12m_pct;
  const liveRow = (p.rows || []).find(r => r.live);
  $("pf-totals").innerHTML = `
    <div class="card"><div style="font-size:24px;font-weight:800">₹${fmtN(tt.invested, 0)}</div>
      <div class="muted small">Invested total · ${tt.n || 0} holdings ${aiBtn("portfolio", "", "Explain")}</div></div>
    <div class="card"><div style="font-size:24px;font-weight:800">₹${fmtN(tt.current, 0)}</div>
      <div class="muted small">Current value${liveRow ? ` · <span class="live-chip">● live ${esc(liveRow.price_time || "")} IST</span>` : (tt.price_date ? " · as of " + fmtDateStr(tt.price_date) + " EOD" : " (price nahi mila)")}</div></div>
    <div class="card"><div style="font-size:24px;font-weight:800" class="${(tt.pnl || 0) >= 0 ? "chg-up" : "chg-down"}">
      ${(tt.pnl > 0 ? "+" : "")}₹${fmtN(tt.pnl, 0)} (${fmtPct(tt.pnl_pct, 1)})</div>
      <div class="muted small">P&amp;L (tumhari entry ke base par) · Nifty 12M ${n12 == null ? "–" : fmtPct(n12)} sirf context</div></div>`;
  renderPfTable();
}

function renderPfTable() {
  const rows = state.port.rows || [];
  if (!rows.length) {
    $("pf-table").innerHTML = `<div class="muted" style="padding:14px">Portfolio khaali — upar form se holding add karo ya broker CSV import karo.</div>`;
    $("pf-meta").textContent = "";
    return;
  }
  const sectors = state.port.sectors || [];
  const trs = rows.map(r => `
    <tr>
      <td><b>${esc(r.name)}</b><div class="muted" style="font-size:11px">${esc(r.symbol)}</div></td>
      <td class="muted">${esc(r.sector)}</td>
      <td class="num">${fmtN(r.qty, 0)}</td>
      <td class="num">${fmtPrice(r.avg)}</td>
      <td class="num">${r.price == null ? "–" : fmtPrice(r.price)}<div class="muted" style="font-size:10.5px">${r.live ? `<span class="live-chip">● live ${esc(r.price_time || "")}</span>` : (r.price_date ? "as of " + fmtDateStr(r.price_date) + " EOD" : "price nahi mila")}</div></td>
      <td class="num">₹${fmtN(r.invested, 0)}</td>
      <td class="num">${r.value == null ? "–" : "₹" + fmtN(r.value, 0)}</td>
      <td class="num ${(r.pnl || 0) >= 0 ? 'chg-up' : 'chg-down'}" style="font-weight:700">${r.pnl == null ? "–" : (r.pnl > 0 ? "+" : "") + "₹" + fmtN(r.pnl, 0) + " (" + fmtPct(r.pnl_pct, 1) + ")"}</td>
      <td class="num">${r.cagr_pct == null ? "–" : fmtPct(r.cagr_pct)}</td>
      <td class="num">${r.alloc_pct == null ? "–" : fmtN(r.alloc_pct, 1) + "%"}</td>
      <td>${r.pa ? `${paTrendChip(r.pa.trend)}<div class="muted" style="font-size:10px">S ₹${r.pa.support != null ? fmtN(r.pa.support, 0) : "–"} (${r.pa.support_dist_pct == null ? "–" : fmtN(r.pa.support_dist_pct, 1) + "%"}) · R ₹${r.pa.resistance != null ? fmtN(r.pa.resistance, 0) : "–"} (${r.pa.resistance_dist_pct == null ? "–" : fmtN(r.pa.resistance_dist_pct, 1) + "%"})</div>` : '<span class="muted">–</span>'}</td>
      <td>${esc(r.bought || "")}</td>
      <td><button class="dellink" data-sym="${esc(r.symbol)}" title="Is row ko portfolio se hatao">✕</button></td>
    </tr>`).join("");
  $("pf-table").innerHTML = `
    <table><thead><tr>
      <th>Stock</th><th>Sector</th><th class="num">Qty</th><th class="num">Avg ₹</th>
      <th class="num">Price</th><th class="num">Invested ₹</th><th class="num">Value ₹</th>
      <th class="num">P&amp;L</th><th class="num">CAGR</th><th class="num">Alloc %</th><th>PA</th><th>Bought</th><th></th>
    </tr></thead><tbody>${trs}</tbody></table>
    <div style="padding:8px 10px"><b style="font-size:12px">Sector mix:</b>
      ${sectors.map(s => `<span class="chip"><b>${esc(s.sector)}</b> ${fmtN(s.pct, 1)}%</span>`).join("")}</div>`;
  $("pf-table").querySelectorAll(".dellink").forEach(b => b.addEventListener("click", async () => {
    const rest = ((state.port && state.port.holdings) || []).filter(h => h.symbol !== b.dataset.sym);
    await postPortfolio(rest, "Hata diya: " + b.dataset.sym);
  }));
  $("pf-meta").textContent = `${rows.length} holdings · sab kuch portfolio.json me local`;
}

async function postPortfolio(holdings, okmsg) {
  try {
    const r = await fetch("/api/portfolio", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ holdings: holdings || [] }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || ("HTTP " + r.status));
    state.port = await r.json(); renderPortfolio();
    if ($("pf-msg")) $("pf-msg").textContent = okmsg || "Saved.";
  } catch (e) {
    if ($("pf-msg")) $("pf-msg").textContent = "Fail: " + e.message;
  }
}

/* ---------------- allocation guru ---------------- */
$("btn-alloc") && $("btn-alloc").addEventListener("click", async () => {
  const b = parseFloat($("alloc-budget").value || 0);
  const box = $("alloc-out");
  if (!box) return;
  if (!(b > 0)) { box.innerHTML = `<div class="muted small">Budget number daalo — e.g. 100000.</div>`; return; }
  box.innerHTML = loadingHTML("Plan ban raha hai (latest scan ke BUY stocks se)…");
  try {
    const a = await (await fetch(`/api/allocation?budget=${b}`)).json();
    if (!a || !a.available) { box.innerHTML = `<div class="muted small">${esc((a && a.note) || "Abhi plan nahi ban sakta.")}</div>`; return; }
    const rows = (a.items || []).map(x => `
      <tr>
        <td><b>${esc(x.name || x.symbol)}</b><div class="muted" style="font-size:11px">${esc(x.symbol)} · ${esc(x.sector || "")}</div></td>
        <td><span class="badge ${ratingClass(x.rating)}">${esc(x.rating || "")}</span></td>
        <td class="num">${fmtN(x.score, 1)}</td>
        <td class="num">${fmtPrice(x.price)}</td>
        <td class="num"><b>${fmtN(x.weight_pct, 1)}%</b><div class="muted" style="font-size:10.5px">vol ${x.vol_annual_pct == null ? "–" : fmtN(x.vol_annual_pct, 0) + "%"}</div></td>
        <td class="num">₹${fmtN(x.amount, 0)}</td>
        <td class="num">${fmtN(x.shares, 0)}</td>
      </tr>`).join("");
    box.innerHTML = `
      <table><thead><tr><th>Stock</th><th>Rating</th><th class="num">Score</th><th class="num">Price</th>
        <th class="num">Weight</th><th class="num">Amount</th><th class="num">≈Shares</th></tr></thead>
        <tbody>${rows}</tbody></table>
      <div class="muted small" style="margin-top:8px">✓ ₹${fmtN(a.allocated, 0)} allocated · baki ₹${fmtN(a.unallocated, 0)} (whole-share rounding).
        Method: ${esc(a.method)}. Scan basis: ${a.scan_generated_at ? esc(fmtIST(a.scan_generated_at)) : "–"} IST.</div>
      <div class="muted small">${esc(a.note || "")}</div>`;
  } catch (e) { box.innerHTML = errorHTML("Allocation fail: " + esc(e.message)); }
});

/* ---------------- AI explain layer (Google AI Studio / Gemini) ---------------- */
const ai = { status: null, busy: {} };

function aiEnabled() { return !!(ai.status && ai.status.enabled); }

/* chhota ghost chip — disabled ho to khud gayab */
function aiBtn(sec, sym, label, outId) {
  if (!aiEnabled()) return "";
  return `<button class="ai-btn" data-sec="${esc(sec)}"${sym ? ` data-sym="${esc(sym)}"` : ""}${outId ? ` data-out="${esc(outId)}"` : ""} title="AI is section ko simple bhasha me samjhayega">✨ ${esc(label || "Explain")}</button>`;
}

function aiCardHTML(j) {
  if (!j || j.ok === false) return `<div class="ai-card ai-err">✨ ${esc((j && j.error) || "AI fail — thodi der baad try karo.")}</div>`;
  let h = `<div class="ai-card"><div class="ai-head">✨ SIMPLE EXPLANATION</div>`;
  if (j.summary) h += `<p>${esc(j.summary)}</p>`;
  if ((j.points || []).length) h += `<ul class="pct-list">${j.points.map(p => `<li>${esc(p)}</li>`).join("")}</ul>`;
  if ((j.glossary || []).length) h += `<div class="gloss-wrap">${j.glossary.map(g =>
    `<span class="gloss"><b>${esc(g.term)}</b> — ${esc(g.meaning)}</span>`).join("")}</div>`;
  h += `<div class="muted small ai-src">AI-generated simple explanation${j.cached ? " (cached)" : ""} — education only, not advice. Numbers ko NSE/screener.in se cross-check karo.</div></div>`;
  return h;
}

async function aiInit() {
  try { ai.status = await (await fetch("/api/ai/status")).json(); }
  catch (e) { ai.status = { enabled: false }; }
  renderAiModal();
}

function renderAiModal() {
  const st = $("ai-modal-status");
  if (!st || !ai.status) return;
  const s = ai.status;
  st.innerHTML = s.enabled
    ? `<b style="color:var(--green)">✓ AI on</b> — model ${esc(s.model)} · key ${esc(s.key_hint || "")} (source: ${esc(s.source)})`
    : `<b style="color:var(--amber)">AI off</b> — koi API key set nahi hai. Neeche paste karo.`;
}

$("btn-ai").addEventListener("click", () => { $("ai-modal-back").style.display = "flex"; renderAiModal(); });
$("ai-modal-close").addEventListener("click", () => { $("ai-modal-back").style.display = "none"; });
$("ai-modal-back").addEventListener("click", (e) => { if (e.target.id === "ai-modal-back") $("ai-modal-back").style.display = "none"; });

$("btn-ai-key-save").addEventListener("click", async () => {
  const k = ($("ai-key-input").value || "").trim();
  if (!k) { $("ai-modal-msg").textContent = "Pehle key paste karo."; return; }
  $("ai-modal-msg").textContent = "Saving…";
  try {
    const r = await fetch("/api/ai/key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: k }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || ("HTTP " + r.status));
    ai.status = j.status; renderAiModal();
    $("ai-modal-msg").textContent = "✓ Saved — AI on! Page refresh ho raha hai…";
    setTimeout(() => location.reload(), 800);
  } catch (e) { $("ai-modal-msg").textContent = "Fail: " + e.message; }
});

$("btn-ai-key-clear").addEventListener("click", async () => {
  try {
    await fetch("/api/ai/key/clear", { method: "POST" });
    ai.status = { enabled: false };
    $("ai-modal-msg").textContent = "Key hata di — page refresh…";
    setTimeout(() => location.reload(), 600);
  } catch (e) { $("ai-modal-msg").textContent = "Fail: " + e.message; }
});

/* ek hi delegation: ✨ buttons + Ask AI */
document.addEventListener("click", async (ev) => {
  const b = ev.target.closest(".ai-btn");
  if (b) {
    ev.preventDefault();
    if (!aiEnabled()) return;
    const sec = b.dataset.sec || "report", sym = b.dataset.sym || "";
    const bkey = sec + "|" + sym;
    if (ai.busy[bkey]) return;
    ai.busy[bkey] = true;
    const host = b.closest(".card") || b.closest(".market") || b.parentElement;
    let out = b.dataset.out ? $(b.dataset.out) : null;
    if (!out && host) {
      out = [...host.children].find(c => c.classList && c.classList.contains("ai-out")) || null;
      if (!out) { out = document.createElement("div"); out.className = "ai-out"; host.appendChild(out); }
    }
    const body = { section: sec };
    if (sym) body.symbol = sym;
    if (sec === "portfolio" && state.port) body.data = { totals: state.port.totals, sectors: state.port.sectors, nifty_12m_pct: state.port.nifty_12m_pct };
    if (sec === "fii_dii" && state.market) body.data = state.market.fii_dii || { available: false };
    if (sec === "nifty_valuation" && state.market) body.data = state.market.nifty_valuation || {};
    if (out) out.innerHTML = loadingHTML("AI simple me samjha raha hai…");
    try {
      const r = await fetch("/api/ai/explain", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (out) out.innerHTML = aiCardHTML(await r.json());
    } catch (e) { if (out) out.innerHTML = aiCardHTML({ ok: false, error: e.message }); }
    ai.busy[bkey] = false;
    return;
  }
  if (ev.target.closest("#ai-ask-go")) {
    ev.preventDefault();
    const inp = $("ai-ask-input");
    const q = ((inp && inp.value) || "").trim();
    if (!q || !state.curSym) return;
    const out = $("ai-ask-out");
    out.innerHTML = loadingHTML("AI soch raha hai…");
    try {
      const r = await fetch("/api/ai/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: state.curSym, question: q }) });
      out.innerHTML = aiCardHTML(await r.json());
    } catch (e) { out.innerHTML = aiCardHTML({ ok: false, error: e.message }); }
  }
});

/* deep-link: #stock=RELIANCE.NS  |  start */
(async function start() {
  await aiInit();          // AI status pehle — warna dashboard ke ✨ buttons race me chhoot jaate
  loadDashboard();
  const h = new URLSearchParams(location.hash.replace("#", ""));
  const s = (h.get("stock") || "").toUpperCase();
  if (s) { openStockTab(s); }
})();
