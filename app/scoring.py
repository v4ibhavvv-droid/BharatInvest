"""
BharatInvest scoring engine — 5 pillars like a research desk:
  Quality / Growth / Valuation / Technical / Risk
Each pillar 0-100 with transparent breakdown, then weighted total -> rating,
plain claims: why_buy, risks, verdict, 1-year scenarios, and Investo-style EPV/DCF.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def cfg() -> Dict[str, Any]:
    import yaml
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


CFG = cfg()
W = CFG["weights"]


# ================================================================= quality
def quality_pillar(f: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    score = 0.0

    def add(pt_label: str, pts: float, note: str):
        nonlocal score
        score += pts
        checks.append({"metric": pt_label, "pts": round(pts, 1), "note": note})

    roe = f.get("roe_pct")
    p = _band(roe, [(15, 25), (12, 20), (8, 13), (0, 6), (-1e9, 0)])
    add(f"ROE {fmt(roe)}%", p, "ROE >= 15% is excellent" if (roe or -99) >= 15 else "capital efficiency")

    roce = f.get("roce_pct")
    p = _band(roce, [(15, 20), (12, 16), (8, 10), (0, 5), (-1e9, 0)])
    add(f"ROCE {fmt(roce)}%", p, "return on total capital employed")

    de = f.get("debt_to_equity")
    if de is None:
        pts, note = 7.0, "D/E unknown"
    elif de < 0.3:
        pts, note = 15.0, "very low debt"
    elif de <= 0.7:
        pts, note = 12.0, "healthy leverage"
    elif de <= 1.2:
        pts, note = 7.0, "moderate leverage"
    else:
        pts, note = 2.0, "high debt - risk"
    add(f"D/E {fmt(de)}", pts, note)

    opm = f.get("opm_pct")
    p = _band(opm, [(18, 15), (12, 12), (7, 8), (0, 4), (-1e9, 0)])
    add(f"Operating margin {fmt(opm)}%", p, "pricing power proxy")

    cfop = f.get("cfo_pat")
    if cfop is None:
        pts, note = 8.0, "CFO/PAT unknown"
    elif cfop >= 1.0:
        pts, note = 15.0, "earnings backed by cash"
    elif cfop >= 0.85:
        pts, note = 11.0, "decent cash conversion"
    elif cfop >= 0.6:
        pts, note = 5.0, "weak cash conversion"
    else:
        pts, note = 0.0, "low cash conversion - accounting risk"
    add(f"CFO/PAT {fmt(cfop)}", pts, note)

    fs = (f.get("fscore") or {})
    fscore = fs.get("score")
    if fscore is None:
        pts, note = 5.0, "F-Score unavailable"
    else:
        pts = round(fscore / 9 * 10, 1)
        note = f"Piotroski F-Score {fscore}/9"
    add(note if fscore is not None else "F-Score n/a", pts, "Piotroski health check")

    return {"name": "Quality", "score": min(round(score, 1), 100), "checks": checks}


# ================================================================= growth
def growth_pillar(f: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    score = 0.0

    def add(label: str, pts: float, note: str):
        nonlocal score
        score += pts
        checks.append({"metric": label, "pts": round(pts, 1), "note": note})

    def band_pts(x: Optional[float], bands) -> float:
        if x is None:
            return 0.0
        for thr, p in bands:
            if x >= thr:
                return p
        return bands[-1][1] * 0.3 if x < 0 else 0.0

    cgp = f.get("cagr_profit_pct")
    add(f"Profit CAGR {fmt(cgp)}%", band_pts(cgp, [(20, 40), (15, 32), (10, 24), (5, 14), (0, 6)]),
        "compounded annual profit growth")

    cgr = f.get("cagr_rev_pct")
    add(f"Revenue CAGR {fmt(cgr)}%", band_pts(cgr, [(15, 30), (10, 24), (6, 16), (2, 8), (0, 3)]),
        "revenue compounding")

    yoy = f.get("yoy_profit_pct")
    add(f"Latest YoY profit {fmt(yoy)}%", band_pts(yoy, [(15, 15), (8, 11), (2, 6), (-5, 2)]),
        "most recent momentum")

    npm = f.get("npm_pct")
    add(f"Net margin {fmt(npm)}%", band_pts(npm, [(15, 15), (10, 12), (5, 7), (1, 3)]),
        " profitability per rupee")

    return {"name": "Growth", "score": min(round(score, 1), 100), "checks": checks}


# ================================================================= valuation
def valuation_pillar(f: Dict[str, Any], dcf: Dict[str, Any],
                     sector_stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    score = 0.0

    def add(label: str, pts: float, note: str):
        nonlocal score
        score += pts
        checks.append({"metric": label, "pts": round(pts, 1), "note": note})

    pe = f.get("pe")
    sec_pe = (sector_stats or {}).get("median_pe")
    if pe is None:
        add("P/E unknown", 8.0, "no earnings data")
    elif sec_pe and pe < sec_pe:
        disc = (sec_pe / pe - 1) * 100
        pts = 25 if disc >= 25 else 18 if disc >= 10 else 12
        add(f"P/E {fmt(pe)} vs sector {fmt(sec_pe)}", pts, f"sector discount {disc:.0f}%")
    else:
        pts = 12 if pe <= 25 else 8 if pe <= 35 else 3 if pe <= 50 else 0
        add(f"P/E {fmt(pe)}", pts, "cheaper = better" if pts >= 8 else "expensive vs market")

    cgp = f.get("cagr_profit_pct") or 0
    if pe and cgp and cgp > 0:
        peg = pe / (cgp * 100)
        pts = 25 if peg < 1 else 18 if peg < 1.5 else 10 if peg < 2.2 else 3
        add(f"PEG {fmt(peg)}", pts, "growth-adjusted valuation")
    else:
        add("PEG n/a", 8.0, "insufficient growth/PE data")

    pb = f.get("pb")
    roe = f.get("roe_pct")
    if pb is not None and roe:
        fair_pb = max(roe / 10.0 * 2, 1.0)   # ROE 15% -> fair PB ~3
        pts = 15 if pb < fair_pb * 0.8 else 10 if pb < fair_pb else 4 if pb < fair_pb * 1.4 else 0
        add(f"P/B {fmt(pb)} vs ROE {fmt(roe)}%", pts, "P/B should be justified by ROE")
    else:
        add("P/B vs ROE n/a", 8.0, "missing data")

    mos = dcf.get("mos_pct")
    if mos is None:
        add("DCF n/a", 5.0, "insufficient FCF/profit data")
    elif mos >= CFG["valuation"]["mos_strong"] * 100:
        add(f"Margin of safety {fmt(mos)}%", 25.0, "trading well below intrinsic estimate")
    elif mos >= CFG["valuation"]["mos_some"] * 100:
        add(f"Margin of safety {fmt(mos)}%", 15.0, "some cushion vs intrinsic")
    elif mos >= -15:
        add(f"Margin of safety {fmt(mos)}%", 8.0, "near fair value")
    else:
        add(f"Margin of safety {fmt(mos)}%", 0.0, "expensive vs intrinsic estimate")

    dy = f.get("div_yield_pct")
    add(f"Dividend yield {fmt(dy)}%", _band(dy, [(3, 5), (1.5, 4), (0.7, 2), (0, 0)]),
        "income cushion")

    return {"name": "Valuation", "score": min(round(score, 1), 100), "checks": checks}


# ================================================================= technical
def technical_pillar(t: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    score = 0.0

    def add(label: str, pts: float, note: str):
        nonlocal score
        score += pts
        checks.append({"metric": label, "pts": round(pts, 1), "note": note})

    if not t.get("available"):
        return {"name": "Technical", "score": 50.0, "checks": [{"metric": "no price data", "pts": 0, "note": "insufficient OHLCV"}]}

    price = t.get("price")
    sma50 = t.get("sma50")
    sma200 = t.get("sma200")
    if price and sma50 and price > sma50:
        add("Above 50-DMA", 15.0, "short-term uptrend")
    elif price and sma50:
        add("Below 50-DMA", 4.0, "pullback / downtrend")
    if sma50 and sma200 and sma50 > sma200:
        add("200-DMA uptrend (SMA50>SMA200)", 15.0, "long-term trend intact")
    elif sma50 and sma200:
        add("Long-term downtrend", 2.0, "below 200-DMA structure")
    gc = t.get("golden_cross", None)
    if gc is not None and gc <= 90:
        add(f"Fresh golden cross ({gc}d ago)", 5.0, "recent bullish structure")

    # relative strength (11 x 3-6-12)
    rs_pts = 0.0
    rs_notes = []
    for lbl, k in (("3M", "rs_3m"), ("6M", "rs_6m"), ("12M", "rs_12m")):
        v = t.get(k)
        if v is None:
            continue
        rs_notes.append(f"{lbl} {v:+.0f}%")
        rs_pts += 10 if v > 3 else (6 if v > -3 else 0)
    if rs_notes:
        add(f"Relative strength vs Nifty ({', '.join(rs_notes)})", min(rs_pts, 30.0), "beating the index")

    pos = t.get("pos_52w")
    if pos is not None:
        p = 10 if pos >= 70 else 6 if pos >= 50 else 3 if pos >= 30 else 1
        add(f"52-week position {fmt(pos)}%", p, "where price sits in 52w range")

    rsi = t.get("rsi14")
    if rsi is not None:
        if 45 <= rsi <= 65:
            add(f"RSI {fmt(rsi)}", 10.0, "healthy momentum zone")
        elif 35 <= rsi < 45:
            add(f"RSI {fmt(rsi)}", 6.0, "cooling off")
        elif 65 < rsi <= 75:
            add(f"RSI {fmt(rsi)}", 6.0, "strong but extended")
        elif rsi > 75:
            add(f"RSI {fmt(rsi)}", 2.0, "overbought - chase risk")
        else:
            add(f"RSI {fmt(rsi)}", 4.0, "oversold - falling knife risk")

    dd = t.get("drawdown_52w")
    if dd is not None:
        p = 5 if dd >= -10 else 3 if dd >= -20 else 1 if dd >= -35 else 0
        add(f"Drawdown from 52w high {fmt(dd)}%", p, "distance from peak")

    vt = t.get("vol_trend")
    if vt and price and t.get("chg_1m_pct") and (t.get("chg_1m_pct") or 0) > 0 and vt >= 1.05:
        add(f"Volume trend {fmt(vt)}x", 10.0, "volume supports the move (accumulation)")
    else:
        add("Volume trend", 5.0, "neutral")

    return {"name": "Technical", "score": min(round(score, 1), 100), "checks": checks}


# ================================================================= risk
def risk_pillar(f: Dict[str, Any], t: Dict[str, Any], pledge: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    score = 0.0

    def add(label: str, pts: float, note: str, flag: Optional[str] = None):
        nonlocal score
        score += pts
        checks.append({"metric": label, "pts": round(pts, 1), "note": note,
                       "flag": flag} if flag else {"metric": label, "pts": round(pts, 1), "note": note})

    de = f.get("debt_to_equity")
    if de is None:
        add("Debt unknown", 12.0, "check balance sheet manually")
    elif de < 0.5:
        add(f"D/E {fmt(de)}", 25.0, "low debt - low financial risk")
    elif de <= 1.0:
        add(f"D/E {fmt(de)}", 18.0, "manageable debt")
    elif de <= 2.0:
        add(f"D/E {fmt(de)}", 9.0, "elevated debt")
    else:
        add(f"D/E {fmt(de)}", 0.0, "HIGH DEBT - key risk", flag="high")

    ni = (f.get("profit_series") or [])
    if ni and ni[-1] is not None and ni[0] is not None:
        losing_years = sum(1 for v in ni if v is not None and v < 0)
        p = 20 if losing_years == 0 else 10 if losing_years == 1 else 0
        add(f"Profitable in last {len(ni)} FYs", p, "consistency" if p >= 18 else "losses on record")
    else:
        add("Profit history unknown", 10.0, "insufficient data")

    av = t.get("annual_vol_pct")
    if av is not None:
        p = 15 if av < 18 else 10 if av < 25 else 5 if av < 35 else 0
        add(f"Volatility {fmt(av)}% ann.", p, "smooth compounding" if p >= 10 else "bumpy ride")

    dd = t.get("drawdown_52w")
    if dd is not None:
        p = 10 if dd >= -10 else 7 if dd >= -20 else 4 if dd >= -35 else 0
        add(f"Distance from peak {fmt(dd)}%", p, "small drawdowns help hold through cycles")

    if pledge.get("available"):
        pl = pledge.get("pledged_pct", 0)
        p = 10 if pl <= 2 else 5 if pl <= 10 else 2 if pl <= 25 else 0
        add(f"Promoter pledge {fmt(pl)}%", p, "pledge = distress signal" if p <= 2 else "monitored", flag="high" if pl > 25 else None)
    else:
        add("Pledge data", 6.0, "unknown - cross-check on screener.in")

    fs = (f.get("fscore") or {})
    if fs.get("complete"):
        s = fs.get("score", 5)
        p = 10 if s >= 7 else 6 if s >= 4 else 0
        add(f"F-Score {s}/9", p, "accounting health")

    return {"name": "Risk", "score": min(round(score, 1), 100), "checks": checks}


# ================================================================= DCF / EPV
def dcf_estimate(f: Dict[str, Any], price: Optional[float],
                 is_financial: bool = False) -> Dict[str, Any]:
    v = CFG["valuation"]
    r = v["discount_rate"]
    gt = v["terminal_growth"]
    yrs = v["forecast_years"]
    gcap = v["max_growth_cap"]

    if is_financial:
        # CFO/FCF for banks & insurers is polluted by deposit/claim flows —
        # EPV on it is meaningless. Skip; valuation pillar uses P/B-vs-ROE instead.
        return {"available": False, "reason": "financial: DCF skipped — use P/B vs ROE",
                "growth_used_pct": None, "docs": "financial company"}

    base = None
    src = None
    prof = (f.get("profit_series") or [])
    if f.get("fcf_cr") is not None and f.get("fcf_cr", 0) > 0:
        base, src = f["fcf_cr"] * 1e7, "FCF (latest FY)"          # fcf_cr is in crores
    elif prof and prof[0] is not None and prof[0] > 0:
        base, src = prof[0] * 0.85, "PAT x 0.85 proxy"            # profit_series is raw INR

    ni0 = prof[0] if prof and prof[0] else None
    if base and src == "FCF (latest FY)" and ni0 and ni0 > 0:
        # capex-cycle companies show depressed one-year FCF; clamp to 25%-150% of PAT
        clamped = min(max(base, 0.25 * ni0), 1.5 * ni0)
        if clamped != base:
            base, src = clamped, "FCF (clamped 25–150% of PAT)"

    g = f.get("cagr_profit_pct")
    g = min(max((g or 0) / 100, 0.0), gcap) if g else 0.0
    shares = None
    out: Dict[str, Any] = {"available": False, "source": src, "growth_used_pct": round(g * 100, 1),
                           "docs": "5y two-stage DCF, discount ~11%, terminal 4%"}
    try:
        mc, pe, eps = f.get("market_cap"), f.get("pe"), f.get("eps")
        if mc and pe and pe > 0:
            shares = mc / (pe * eps) if eps and eps > 0 else None
        if shares is None and mc is not None:
            # assume market cap = price * shares; but price may differ; try via eps
            shares = None
        if base and base > 0:
            # explicit stage
            pv = 0.0
            cash = base
            for _ in range(yrs):
                cash = cash * (1 + g)
                pv += cash / ((1 + r) ** (_ + 1))
            # terminal (Gordon on cash after yrs)
            term_val = cash * (1 + gt) / (r - gt)
            pv += term_val / ((1 + r) ** yrs)
            equity_value = pv
            if shares is None and mc and price:
                shares = mc / price
            if shares and shares > 0:
                per_share = equity_value / shares
                mos = None if (price is None or not price) else round((per_share / price - 1) * 100, 1)
                # sanity clamp: absurd valuation = broken inputs -> refuse
                if price and (per_share > price * 50 or per_share < price * 0.02):
                    return {"available": False, "reason": "anomalous inputs",
                            "source": src, "growth_used_pct": round(g * 100, 1)}
                sens = []
                for dr in (r - 0.01, r, r + 0.01):
                    row = []
                    for gg in (g - 0.02, g, g + 0.02):
                        gg = min(max(gg, 0.0), gcap)
                        pvp = 0.0
                        cc = base
                        for _ in range(yrs):
                            cc = cc * (1 + gg)
                            pvp += cc / ((1 + dr) ** (_ + 1))
                        tv = cc * (1 + gt) / (dr - gt)
                        pvp += tv / ((1 + dr) ** yrs)
                        row.append(round(pvp / shares, 1))
                    sens.append({"discount": round(dr * 100, 1), "values": row})
                out = {"available": True, "source": src, "growth_used_pct": round(g * 100, 1),
                       "intrinsic_per_share": round(per_share, 1),
                       "mos_pct": mos,
                       "sensitivity": sens,
                       "docs": "5y two-stage DCF, discount ~11%, terminal 4%"}
    except Exception:
        pass
    return out


# ================================================================= composite
def composite(f: Dict[str, Any], t: Dict[str, Any], dcf: Dict[str, Any],
              sector_stats: Optional[Dict[str, Any]], pledge: Dict[str, Any]) -> Dict[str, Any]:
    pillars = [
        quality_pillar(f),
        growth_pillar(f),
        valuation_pillar(f, dcf, sector_stats),
        technical_pillar(t),
        risk_pillar(f, t, pledge),
    ]
    total = sum(p["score"] * W[p["name"].lower()] for p in pillars)
    total = round(min(max(total, 0), 100), 1)
    rating, label = rating_for(total, f)
    why_buy, risks = explain(f, t, pillars, dcf)
    return {"pillars": pillars, "total": total, "rating": rating, "label": label,
            "why_buy": why_buy, "risks": risks,
            "weights": {p["name"].lower(): W[p["name"].lower()] for p in pillars}}


def rating_for(total: float, f: Dict[str, Any]) -> tuple:
    rc = CFG["ratings"]
    dq_low = (f.get("fscore") is not None and f.get("fscore", {}).get("complete") is False and not f.get("roe_pct"))
    if total >= rc["strong_buy"]:
        return ("STRONG BUY", "Consistent compounder at a sensible price")
    if total >= rc["buy"]:
        return ("BUY", "Good fundamentals + acceptable trend")
    if total >= rc["hold"]:
        return ("HOLD", "Wait for a better price or stronger numbers")
    if total >= rc["weak"]:
        return ("AVOID (for now)", "Fundamentals or trend too weak today")
    return ("AVOID", "Poor quality, expensive, or high risk")


def explain(f: Dict[str, Any], t: Dict[str, Any], pillars, dcf: Dict[str, Any]) -> tuple:
    why: List[str] = []
    risks: List[str] = []
    roe = f.get("roe_pct")
    if roe is not None and roe >= 15:
        why.append(f"ROE {roe}% — strong capital efficiency (compounder trait).")
    roce = f.get("roce_pct")
    if roce is not None and roce >= 18:
        why.append(f"ROCE {roce}% — business earns well above its cost of capital.")
    cgp = f.get("cagr_profit_pct")
    if cgp is not None and cgp >= 15:
        why.append(f"Profit compounding at ~{cgp}% p.a. — growth engine intact.")
    de = f.get("debt_to_equity")
    if de is not None and de <= 0.3:
        why.append(f"Very low debt (D/E {de}) — balance sheet safety.")
    fs = (f.get("fscore") or {})
    if fs.get("complete") and fs.get("score", 0) >= 7:
        why.append(f"Piotroski F-Score {fs['score']}/9 — clean accounting health.")
    mos = dcf.get("mos_pct")
    if mos is not None and mos >= 15:
        why.append(f"Price ~{mos}% below the DCF intrinsic estimate — margin of safety.")
    rs12 = t.get("rs_12m")
    if rs12 is not None and rs12 > 5:
        why.append(f"Beating Nifty by {rs12}% over 12 months (relative strength).")
    dy = f.get("div_yield_pct")
    if dy is not None and dy >= 2:
        why.append(f"Dividend yield {dy}% — income while you wait.")
    sma200 = t.get("sma200")
    price = t.get("price")
    if price and sma200 and price > sma200:
        why.append("Trading above the 200-DMA — long-term trend positive.")

    if de is not None and de > 1.5:
        risks.append(f"High debt (D/E {de}) — interest burden in slowdowns.")
    if cgp is not None and cgp < 0:
        risks.append("Profits have declined over the recent years — structural challenge.")
    npm = f.get("npm_pct")
    if npm is not None and npm < 3:
        risks.append(f"Thin net margin ({npm}%) — small shocks hit hard.")
    if t.get("rsi14") is not None and t["rsi14"] > 75:
        risks.append(f"RSI {t['rsi14']} — overbought; entry now may be poor value.")
    if t.get("drawdown_52w") is not None and t["drawdown_52w"] < -30:
        risks.append(f"Down {abs(t['drawdown_52w'])}% from the 52-week high — check what's broken.")
    mos = dcf.get("mos_pct")
    if mos is not None and mos < -20:
        risks.append(f"Trading {abs(mos):.0f}% ABOVE the intrinsic estimate — valuation risk.")
    cfop = f.get("cfo_pat")
    if cfop is not None and cfop < 0.6:
        risks.append(f"CFO/PAT just {cfop} — reported profits not converting to cash.")
    if f.get("pe") is not None and f["pe"] > 60:
        risks.append(f"P/E {f['pe']:.0f} — narrative is priced in; disappointment hurts.")
    if not risks:
        risks.append("No red flags detected by the engine — still verify pledge/auditor notes on screener.in.")
    return why[:6], risks[:5]


def _band(x, bands) -> float:
    if x is None:
        return 0.0
    for thr, p in bands:
        if x >= thr:
            return p
    return 0.0


def fmt(x) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float) and abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:g}" if abs(x) < 100 else f"{x:,.0f}"


# ================================================================= scenarios
def scenarios(f: Dict[str, Any], t: Dict[str, Any], dcf: Dict[str, Any]) -> Dict[str, Any]:
    price = t.get("price")
    if not price:
        return {"available": False}
    cgp = (f.get("cagr_profit_pct") or 0) / 100
    vol = (t.get("annual_vol_pct") or 25) / 100
    base_growth = min(max(cgp, 0.0), 0.25)
    # valuation pull factor: overpriced -> haircut; underpriced -> mean reversion bonus
    mos = dcf.get("mos_pct")
    adj = 1.0
    if mos is not None:
        adj = 1.0 + max(min(mos / 100 * 0.3, 0.12), -0.12)
    base = price * (1 + base_growth) * adj
    bull = base * (1 + vol)
    bear = base * (1 - vol)
    return {"available": True,
            "base": round(base, 1), "bull": round(bull, 1), "bear": round(bear, 1),
            "note": "Estimates only — profit-growth drift ± 1-σ volatility. Not predictions."}
