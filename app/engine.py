"""
BharatInvest orchestration layer.
- analyze(symbol): full research report for one stock
- run_scan(): batch-analysis of the universe + sector-relative stats + ranking
- market_overview(): indices + FII/DII + breadth (from latest scan)
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import app.datafeed as df
import app.metrics as M
import app.scoring as S
import app.priceaction as PA

# For financials (banks/NBFC/insurers) a DCF on their CFO/FCF is meaningless —
# deposit and claim flows pollute operating cash flow. Engine skips it there.
_FIN_KEYWORDS = ("bank", "nbfc", "insurance", "finance", "financial")


def _is_financial(sector: Optional[str]) -> bool:
    s = (sector or "").lower()
    return any(k in s for k in _FIN_KEYWORDS)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def universe_list() -> List[Dict[str, str]]:
    return df.UNIVERSE


def sector_map() -> Dict[str, Dict[str, str]]:
    return {u["symbol"]: u for u in df.UNIVERSE}


# ----------------------------------------------------------------- helpers
def _median(vals: List[float]) -> Optional[float]:
    v = sorted([x for x in vals if x is not None])
    if not v:
        return None
    n = len(v)
    return round(v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2, 1)


def sector_medians(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r.get("sector") or "Other", []).append(r)
    for sec, rs in groups.items():
        if len(rs) < 3:
            continue
        out[sec] = {
            "n": len(rs),
            "median_pe": _median([r.get("pe") for r in rs]),
            "median_roe": _median([r.get("roe_pct") for r in rs]),
            "median_de": _median([r.get("debt_to_equity") for r in rs]),
            "median_cagr": _median([r.get("cagr_profit_pct") for r in rs]),
        }
    return out


def fetch_nifty_closes() -> List[float]:
    """2y Nifty closes for Relative Strength (cached, thread-safe via px cache)."""
    try:
        rows = df.get_ohlcv("^NSEI", refresh=False)
        return [r["close"] for r in rows if r.get("close") is not None]
    except Exception:
        return []


# ----------------------------------------------------------------- analyze one
def analyze(symbol: str, refresh: bool = False) -> Dict[str, Any]:
    symbol = df._clean_symbol(symbol)
    DBG = __import__("os").environ.get("BH_DEBUG")

    def _dbg(m: str):
        if DBG:
            print(f"[bhinvest {m}]", flush=True)

    # numeric BSE code (500325.BO) — Yahoo company-symbol me map karo (nost of 10s, cached)
    bse_resolve: Optional[Dict[str, Any]] = None
    mnum = re.fullmatch(r"(\d{5,6})\.(?:BO|BS|NS)", symbol)
    if mnum:
        bse_resolve = df.resolve_numeric_bse(symbol)
        if bse_resolve and bse_resolve.get("yahoo_symbol"):
            _dbg(f"BSE {mnum.group(1)} -> {bse_resolve['yahoo_symbol']}")
            symbol = bse_resolve["yahoo_symbol"]

    key = f"ana5:{symbol}"

    def _live_touch(rep: Dict[str, Any]) -> None:
        """Cache-hit par bhi price LIVE rakho — quote() ka apna 60s TTL hai,
        isliye sasta. Report ka baaki content cached hi rehta hai."""
        try:
            q = df.quote(symbol)
            if q and q.get("price") is not None:
                rep["quote"] = q
        except Exception:
            pass

    _dbg(f"analyze start {symbol} refresh={refresh}")
    if not refresh:
        c = df.cache_get(key, "analyze")
        if c is not None:
            c["cached"] = True
            _live_touch(c)
            if bse_resolve:
                c["bse_resolve"] = bse_resolve
            return c
    with df.sym_lock(symbol):
        if not refresh:
            c = df.cache_get(key, "analyze")
            if c is not None:
                c["cached"] = True
                _live_touch(c)
                if bse_resolve:
                    c["bse_resolve"] = bse_resolve
                return c

        uinfo = sector_map().get(symbol, {})
        name = uinfo.get("name") or symbol.replace(".NS", "").replace(".BO", "")
        sector = uinfo.get("sector") or "Unknown"

        info = df.get_info(symbol, refresh=refresh); _dbg("info done")
        financials = df.get_financials(symbol, refresh=refresh); _dbg("financials done")
        ohlcv = df.get_ohlcv(symbol, refresh=refresh); _dbg("ohlcv done")
        q = df.quote(symbol, refresh=refresh); _dbg("quote done")
        nifty_closes = fetch_nifty_closes(); _dbg(f"nifty closes: {len(nifty_closes)}")

        t = M.compute_technical(ohlcv, nifty_closes or None); _dbg("technical done")
        f = (M.compute_fundamental(info, financials, price=q.get("price"))
             if info else M.compute_fundamental({}, financials)); _dbg("fundamental done")

        # Price Action (10-category) — display/timing layer, score me nahi jaata
        pa_rep = PA.analyze_price_action(ohlcv); _dbg(f"price action done: {(pa_rep.get('timing') or {}).get('signal')}")

        scrp = df.screener_scrape(symbol, refresh=refresh); _dbg("screener scrape done")
        pledge = (scrp.get("pledge") if scrp else None) or {"available": False}
        scan_cache = df.cache_get("scan4:latest", "scan") or {}
        sec_stats = (scan_cache.get("sector_medians") or {}).get(sector)

        d = S.dcf_estimate(f, q.get("price"), is_financial=_is_financial(sector))
        comp = S.composite(f, t, d, sec_stats, pledge)
        sc = S.scenarios(f, t, d)
        dq = M.data_quality(info, financials) if info else {"grade": "LOW", "score": 0.0}

        news: List[Dict[str, str]] = []
        try:
            news = df.news_for(symbol, name, refresh=refresh); _dbg("news done")
        except Exception:
            pass

        report = {
            "symbol": symbol,
            "bse_resolve": bse_resolve,     # numeric .BO/.BS input -> NSE map (if any)
            "name": name,
            "sector": sector,
            "generated_at": now_iso(),
            "cached": False,
            "quote": q,
            "fundamental": f,
            "technical": t,
            "price_action": pa_rep,
            "dcf": d,
            "scoring": comp,
            "scenarios": sc,
            "data_quality": dq,
            "news": news,
            "pledge": pledge,
            "screener": scrp,               # 10-yr statements + pros/cons (Screener.in)
            "weights": comp.get("weights"),
            "peer_stats": sec_stats,
        }
        df.cache_set(key, report)
        return report


# ----------------------------------------------------------------- scan / rank
_SCAN_BG_LOCK = threading.Lock()


def _bg_rescan() -> None:
    try:
        run_scan(refresh=True)
    except Exception as e:
        print(f"[bhinvest] background scan failed: {e}", flush=True)


def run_scan(refresh: bool = False, limit: Optional[int] = None) -> Dict[str, Any]:
    full = limit is None
    key = "scan4:latest" if full else f"scan4:limit{limit}"
    if not refresh:
        c = df.cache_get(key, "scan")
        if c is not None:
            return c
        stale = df.cache_get_stale(key)
        if stale is not None and full:
            # TTL ke baad bhi purana result TURANT do + baqground me fresh scan
            # (warna har page-load par 25-60s ka synchronous scan hota hai)
            stale = dict(stale)
            stale["stale"] = True
            df.cache_set(key, stale)     # mtime refresh + stale flag bhi save hota hai
            with _SCAN_BG_LOCK:
                if not any(t.name == "bh-rescan" for t in threading.enumerate()):
                    threading.Thread(target=_bg_rescan, daemon=True, name="bh-rescan").start()
            return stale

    universe = df.UNIVERSE
    if limit:
        universe = universe[:limit]

    results: List[Optional[Dict[str, Any]]] = [None] * len(universe)
    started = time.time()

    def work(u: Dict[str, str]) -> Dict[str, Any]:
        sym = u["symbol"]
        for attempt in (1, 2):
            try:
                r = analyze(sym, refresh=refresh if attempt == 1 else True)
                q = r.get("quote") or {}
                fu = r.get("fundamental") or {}
                core_ok = (q.get("price") is not None) or (fu.get("pe") is not None) \
                    or (fu.get("roe_pct") is not None)
                if core_ok:
                    return r
            except Exception:
                pass
            if attempt == 1:
                time.sleep(3.0)     # Yahoo burst throttle — ruk ke ek retry
        return {"symbol": sym, "ok": False}

    if limit == 1:
        for idx, u in enumerate(universe):
            results[idx] = work(u)
    else:
        workers = max(2, int(S.CFG.get("scan", {}).get("max_workers", 8)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(work, u): i for i, u in enumerate(universe)}
            for fut in as_completed(futs):
                i = futs[fut]
                results[i] = fut.result()

    rows: List[Dict[str, Any]] = []
    unavailable: List[str] = []
    for u, r in zip(universe, results):
        sym = u["symbol"]
        if not r or r.get("ok") is False:
            unavailable.append(sym)
            continue
        try:
            f = r["fundamental"]
            t = r["technical"]
            q = r.get("quote") or {}
            comp = r.get("scoring", {})
            pa_trend, pa_bo = PA.scan_fields(r.get("price_action"))
            has_core = (q.get("price") or t.get("price")) is not None and \
                       (f.get("pe") is not None or f.get("roe_pct") is not None)
            if not has_core:
                unavailable.append(sym)   # e.g. delisted symbol — skip junk row
                continue
            rows.append({
                "symbol": sym,
                "name": r.get("name"),
                "sector": r.get("sector"),
                "price": q.get("price") or t.get("price"),
                "chg_1d": q.get("chg_1d_pct"),
                "live": bool(q.get("live")),
                "price_time": q.get("as_of"),
                "market_cap_cr": _cr(f.get("market_cap")),
                "pe": f.get("pe"),
                "pb": f.get("pb"),
                "roe_pct": f.get("roe_pct"),
                "roce_pct": f.get("roce_pct"),
                "de": f.get("debt_to_equity"),
                "cagr_profit_pct": f.get("cagr_profit_pct"),
                "yoy_profit_pct": f.get("yoy_profit_pct"),
                "div_yield_pct": f.get("div_yield_pct"),
                "fscore": (f.get("fscore") or {}).get("score"),
                "above_200dma": bool(t.get("sma200") and t.get("price") and t["price"] > t["sma200"]),
                "rsi14": t.get("rsi14"),
                "rs_12m": t.get("rs_12m"),
                "drawdown_52w": t.get("drawdown_52w"),
                "trend": pa_trend,
                "breakout": pa_bo,
                "pillars": {p["name"].lower(): p["score"] for p in comp.get("pillars", [])},
                "total": comp.get("total"),
                "rating": comp.get("rating"),
                "label": comp.get("label"),
                "why_buy": comp.get("why_buy", [])[:2],
                "risks": comp.get("risks", [])[:2],
                "data_quality": (r.get("data_quality") or {}).get("grade"),
            })
        except Exception:
            unavailable.append(sym)

    med = sector_medians(rows)
    _apply_sector_relative(rows, med)

    rows.sort(key=lambda r: (r.get("total") or 0), reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1

    out = {
        "generated_at": now_iso(),
        "duration_sec": round(time.time() - started, 1),
        "count": len(rows),
        "unavailable": sorted(unavailable),
        "sector_medians": med,
        "breadth": breadth(rows),
        "rows": rows,
    }
    df.cache_set(key, out)
    return out


def _apply_sector_relative(rows: List[Dict[str, Any]], med: Dict[str, Dict[str, Any]]) -> None:
    """Re-score the Valuation pillar with sector medians now known (quant-alpha style)."""
    if not med:
        return
    for r in rows:
        st = med.get(r.get("sector") or "Other")
        if not st:
            continue
        # re-run only the valuation pillar using tiny synthetic fundamental dict
        f = {"pe": r.get("pe"), "pb": r.get("pb"), "roe_pct": r.get("roe_pct"),
             "cagr_profit_pct": r.get("cagr_profit_pct"), "div_yield_pct": r.get("div_yield_pct"),
             "fscore": {}, "debt_to_equity": None, "profit_series": []}
        dv = {"mos_pct": None}
        new_val = S.valuation_pillar(f, dv, st)
        old = (r.get("pillars") or {}).get("valuation")
        if old is not None and S.W["valuation"]:
            delta = (new_val["score"] - old) * S.W["valuation"]
            r["pillars"]["valuation"] = new_val["score"]
            r["total"] = round(min(max((r.get("total") or 0) + delta, 0), 100), 1)


def breadth(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"available": False}
    above = sum(1 for r in rows if r.get("above_200dma"))
    return {"available": True, "above_200dma_pct": round(above / len(rows) * 100, 0),
            "n": len(rows)}


def _cr(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v) / 1e7, 0)
    except Exception:
        return None


# ----------------------------------------------------------------- market
def market_overview(refresh: bool = False) -> Dict[str, Any]:
    mo = df.market_overview(refresh=refresh)
    nv = df.nifty_valuation(refresh=refresh) or {"available": False}
    if not nv.get("available"):                     # live fail -> stale (aakhri known value)
        nv = df.cache_get_stale(f"niftyval:{df.ist_today()}") or {"available": False}
        nv["stale"] = True
    mo["nifty_valuation"] = nv
    if not mo.get("indices") and nv.get("available"):
        # Yahoo down -> NSE AllIndices se NIFTY 50 fallback (as-of stamp ke saath)
        mo["indices"]["NIFTY 50"] = {"value": nv.get("last"), "chg_pct": nv.get("chg_pct"),
                                     "date": nv.get("as_of")}
    scan_cache = df.cache_get("scan4:latest", "scan") or {}   # scan4 = PA-era key (pehle scan3/latest tha)
    if scan_cache.get("breadth"):
        mo["breadth"] = scan_cache["breadth"]
    # top candidates snapshot for dashboard
    rows = scan_cache.get("rows") or []
    mo["top"] = [
        {"rank": r["rank"], "symbol": r["symbol"], "name": r["name"], "rating": r["rating"],
         "total": r["total"], "sector": r["sector"],
         "why": (r.get("why_buy") or [""])[0]}
        for r in rows[:6]
    ]
    return mo
