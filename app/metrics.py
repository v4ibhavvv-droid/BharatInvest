"""
BharatInvest metric engine.
- Technical: SMA/EMA, RSI(14), MACD, golden/death cross, 52-week position,
  drawdown, relative strength vs NIFTY, support/resistance, volatility, volume trend.
- Fundamental: growth (rev/profit/EPS CAGR + latest YoY), quality (ROE, ROCE,
  margins, CFO/PAT, D/E), Piotroski F-Score, dividend yield, FCF.
Every value degrades gracefully to None instead of crashing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd


# ------------------------------------------------------------------ helpers
def _s(vals: List[Any]) -> pd.Series:
    return pd.Series([None if v is None else float(v) for v in vals], dtype="float64")


def _rowv(frame: Optional[Dict[str, Dict[str, Optional[float]]]], *names: str, n: int = 6) -> List[Optional[float]]:
    """Return values across report dates, newest first, up to n periods."""
    if not frame:
        return []
    for nm in names:
        if nm in frame:
            return [frame[nm][k] for k in list(frame[nm].keys())[:n]]
    # fuzzy match
    for nm, row in frame.items():
        low = nm.lower()
        if any(cand in low for cand in names):
            return [row[k] for k in list(row.keys())[:n]]
    return []


def _cagr(newest: Optional[float], oldest: Optional[float], years: float) -> Optional[float]:
    try:
        if newest is None or oldest is None or years <= 0.5:
            return None
        if oldest <= 0 or newest <= 0:
            return None
        v = (newest / oldest) ** (1.0 / years) - 1
        return round(float(v), 4)
    except Exception:
        return None


def _years_span(dates_newest_first: List[str]) -> float:
    try:
        d0 = pd.Timestamp(dates_newest_first[0])
        d1 = pd.Timestamp(dates_newest_first[-1])
        if len(dates_newest_first) == 1:
            return 1.0
        return max((d0 - d1).days / 365.25, 1.0)
    except Exception:
        return 1.0


# ------------------------------------------------------------------ technical
def compute_technical(ohlcv: List[Dict[str, Any]],
                      nifty_closes: Optional[List[float]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "available": False, "price": None, "sma20": None, "sma50": None, "sma200": None,
        "rsi14": None, "macd": None, "macd_signal": None, "golden_cross": None,
        "death_cross": None, "chg_1m_pct": None, "chg_3m_pct": None, "chg_12m_pct": None,
        "pos_52w": None, "drawdown_52w": None, "rs_3m": None, "rs_6m": None, "rs_12m": None,
        "rsi_status": None,
        "supports": [], "resistances": [], "vol_trend": None, "annual_vol_pct": None,
        "chart": {"dates": [], "close": [], "sma50": [], "sma200": [], "sma20": [],
                  "rsi": [], "volume": []},
    }
    if not ohlcv or len(ohlcv) < 60:
        return out
    close = _s([r["close"] for r in ohlcv])
    high = _s([r["high"] for r in ohlcv])
    low = _s([r["low"] for r in ohlcv])
    vol = _s([r["volume"] if r["volume"] is not None else 0.0 for r in ohlcv])

    price = float(close.iloc[-1]) if pd.notna(close.iloc[-1]) else None
    if price is None:
        return out
    out["available"] = True
    out["price"] = round(price, 2)
    out["price_date"] = ohlcv[-1]["date"]   # kis din ki close hai (date stamp)

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean() if len(close) >= 200 else pd.Series(dtype="float64")
    out["sma20"] = _rnd(sma20.iloc[-1])
    out["sma50"] = _rnd(sma50.iloc[-1])
    if len(sma200.dropna()) > 0:
        out["sma200"] = _rnd(sma200.iloc[-1])

    # RSI (Wilder)
    out["rsi14"] = _rnd(rsi(close, 14).iloc[-1])

    # MACD
    macd_line = ema(close, 12) - ema(close, 26)
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    out["macd"] = _rnd(macd_line.iloc[-1])
    out["macd_signal"] = _rnd(signal_line.iloc[-1])

    # crosses: positive diff -> golden (sma200 maujood ho tabhi)
    n = len(close)
    golden_day = None
    death_day = None
    if out["sma200"] is not None:
        prev_flag = None
        for i in range(max(0, n - 260), n):
            a, b = sma50.iloc[i], sma200.iloc[i]
            if pd.isna(a) or pd.isna(b):
                continue
            flag = a > b
            if prev_flag is False and flag is True:
                golden_day = n - 1 - i
            if prev_flag is True and flag is False:
                death_day = n - 1 - i
            prev_flag = flag
    out["golden_cross"] = golden_day
    out["death_cross"] = death_day

    # returns
    def pct(days: int) -> Optional[float]:
        j = n - 1 - days
        if j < 0 or pd.isna(close.iloc[j]):
            return None
        return round((price / float(close.iloc[j]) - 1) * 100, 2)
    out["chg_1m_pct"] = pct(21)
    out["chg_3m_pct"] = pct(63)
    out["chg_12m_pct"] = pct(252)

    # 52w stats
    w = close.iloc[-252:]
    hi, lo = float(w.max()), float(w.min())
    if hi > 0:
        out["pos_52w"] = round((price - lo) / (hi - lo) * 100, 1) if hi > lo else None
        out["drawdown_52w"] = round((price / hi - 1) * 100, 1)
    # 52w high/low + rsi + volume series (for investigation)
    out["high_52w"] = _rnd(hi)
    out["low_52w"] = _rnd(lo)

    # relative strength vs Nifty
    if nifty_closes and len(nifty_closes) >= 252:
        nf = _s(nifty_closes)
        nl = len(nf)
        stock_ret = _ret(close, 252)
        nifty_ret = _ret(nf, 252)
        out["rs_12m"] = _diff_pct(stock_ret, nifty_ret)
        stock_ret6 = _ret(close, 126)
        nifty_ret6 = _ret(nf, min(126, nl - 1))
        out["rs_6m"] = _diff_pct(stock_ret6, nifty_ret6)
        stock_ret3 = _ret(close, 63)
        nifty_ret3 = _ret(nf, min(63, nl - 1))
        out["rs_3m"] = _diff_pct(stock_ret3, nifty_ret3)

    # support / resistance via 2-bar fractal pivots (last ~250 sessions)
    px = high.to_numpy()[-252:]
    pv = low.to_numpy()[-252:]
    try:
        ph, pl = [], []
        for i in range(2, len(px) - 2):
            if px[i] > px[i - 1] and px[i] > px[i - 2] and px[i] >= px[i + 1] and px[i] >= px[i + 2]:
                ph.append(float(px[i]))
            if pv[i] < pv[i - 1] and pv[i] < pv[i - 2] and pv[i] <= pv[i + 1] and pv[i] <= pv[i + 2]:
                pl.append(float(pv[i]))
        def uniq(v: List[float]) -> List[float]:
            u: List[float] = []
            for x in v:
                if not any(abs(x / y - 1) < 0.012 for y in u):
                    u.append(x)
            return u
        res = sorted([x for x in uniq(ph) if x > price * 1.001])[:3]
        sup = sorted([x for x in uniq(pl) if x < price * 0.999], reverse=True)[:3]
        out["resistances"] = [round(x, 1) for x in res]
        out["supports"] = [round(x, 1) for x in sup]
    except Exception:
        pass

    # volume trend + annualised vol
    try:
        v20 = float(vol.iloc[-20:].mean())
        v60 = float(vol.iloc[-60:].mean())
        out["vol_trend"] = round(v20 / v60, 2) if v60 else None
    except Exception:
        pass
    ret1 = close.pct_change().dropna()
    if len(ret1) > 60:
        out["annual_vol_pct"] = round(float(ret1.std()) * (252 ** 0.5) * 100, 1)

    # RSI status label
    r = out["rsi14"]
    if r is None:
        out["rsi_status"] = None
    elif r < 35:
        out["rsi_status"] = "oversold"
    elif r < 45:
        out["rsi_status"] = "weak"
    elif r <= 65:
        out["rsi_status"] = "healthy"
    elif r <= 75:
        out["rsi_status"] = "strong"
    else:
        out["rsi_status"] = "overbought"

    # chart series (full 2y)
    c = out["chart"]
    c["dates"] = [r["date"] for r in ohlcv]
    c["close"] = [round(float(x), 2) if pd.notna(x) else None for x in close]
    c["sma50"] = [round(float(x), 2) if pd.notna(x) else None for x in sma50]
    c["sma20"] = [round(float(x), 2) if pd.notna(x) else None for x in sma20]
    if len(sma200.dropna()) > 0:
        c["sma200"] = [round(float(x), 2) if pd.notna(x) else None for x in sma200]
    else:
        c["sma200"] = [None] * len(close)
    R = rsi(close, 14)
    c["rsi"] = [round(float(x), 1) if pd.notna(x) else None for x in R]
    c["volume"] = vol.to_list()
    return out


def _rnd(x) -> Optional[float]:
    try:
        v = float(x)
        if pd.isna(v):
            return None
        return round(v, 2)
    except Exception:
        return None


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    ag = gain.ewm(alpha=1 / period, adjust=False).mean()
    al = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = ag / al
    return 100 - 100 / (1 + rs)


def _ret(s: pd.Series, days: int) -> Optional[float]:
    n = len(s)
    j = n - 1 - days
    if j < 0 or pd.isna(s.iloc[j]) or not s.iloc[j]:
        return None
    try:
        return float(s.iloc[-1]) / float(s.iloc[j]) - 1
    except Exception:
        return None


def _diff_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round((a - b) * 100, 1)


# ------------------------------------------------------------------ fundamental
def compute_fundamental(info: Dict[str, Any],
                        financials: Dict[str, Any],
                        price: Optional[float] = None) -> Dict[str, Any]:
    annual = financials.get("annual") or {}
    inc = annual.get("income")
    bal = annual.get("balance")
    cf = annual.get("cashflow")

    rev = _rowv(inc, "Total Revenue", n=5)
    ni = _rowv(inc, "Net Income", "Net Income Common Stockholders", n=5)
    opinc = _rowv(inc, "Operating Income", n=5)
    gp = _rowv(inc, "Gross Profit", n=5)
    cfo = _rowv(cf, "Operating Cash Flow", "Total Cash From Operating Activities", n=4)
    capex = _rowv(cf, "Capital Expenditure", n=4)
    equity = _rowv(bal, "Stockholders Equity", "Total Stockholder Equity",
                   "Common Stockholders Equity", n=4)
    debt = _rowv(bal, "Total Debt", n=4)

    def _trim(vals: List[Optional[float]]) -> List[Optional[float]]:
        v = list(vals)
        while v and v[-1] is None:      # drop missing oldest periods
            v.pop()
        return v
    rev, ni = _trim(rev), _trim(ni)

    inv = inc or {}
    dates = list(list(inv.values())[0].keys())[:5] if inv else []
    span_y = _years_span(dates)

    # growth (min 2 periods required)
    cagr_rev = _cagr(rev[0] if rev else None, rev[-1] if len(rev) > 1 else None, span_y)
    cagr_profit = _cagr(ni[0] if ni else None, ni[-1] if len(ni) > 1 else None, span_y)
    yoy_rev = None
    if len(rev) >= 2 and rev[1]:
        yoy_rev = _ratio(rev[0], rev[1])
    yoy_profit = None
    if len(ni) >= 2 and ni[1]:
        yoy_profit = _ratio(ni[0], ni[1])

    # margins
    opm = None
    if rev and rev[0] and opinc and opinc[0]:
        opm = round(opinc[0] / rev[0] * 100, 1)
    gpm = None
    if rev and rev[0] and gp and gp[0]:
        gpm = round(gp[0] / rev[0] * 100, 1)
    npm = None
    if rev and rev[0] and ni and ni[0]:
        npm = round(ni[0] / rev[0] * 100, 1)

    # returns / capital
    roe = _as_frac(info.get("returnOnEquity"))
    roa = _as_frac(info.get("returnOnAssets"))
    roce = None
    if opinc and opinc[0] is not None:
        cap_emp = None
        if debt and debt[0] is not None and equity and equity[0] is not None:
            cap_emp = debt[0] + equity[0]
        if cap_emp and cap_emp > 0:
            roce = round(opinc[0] / cap_emp * 100, 1)

    # leverage & liquidity
    de = _de_ratio(info.get("debtToEquity"))
    if de is None and debt and debt[0] is not None and equity and equity[0] is not None and equity[0]:
        de = round(debt[0] / equity[0], 2)
    current_ratio = _as_frac(info.get("currentRatio"))

    # cash flow quality
    cfo_pat = None
    if cfo and cfo[0] is not None and ni and ni[0]:
        if ni[0] != 0:
            cfo_pat = round(cfo[0] / ni[0], 2)
    fcf = _as_frac(info.get("freeCashflow"))
    if fcf is None and cfo and cfo[0] is not None and capex:
        cap0 = capex[0]
        if cap0 is not None:
            fcf = cfo[0] + cap0 if cap0 < 0 else cfo[0] - cap0

    # F-score (Piotroski) from annual statements
    fscore = piotroski(info, financials)

    # valuation from info
    price_hint = None
    pe = _as_frac(info.get("trailingPE"))
    fwd_pe = _as_frac(info.get("forwardPE"))
    pb = _as_frac(info.get("priceToBook"))
    eps = _as_frac(info.get("trailingEps"))
    div_yield = _div_yield(info, price)
    mcap = _as_frac(info.get("marketCap"))
    beta = _as_frac(info.get("beta"))
    rec = info.get("recommendationKey")

    out = {
        "available": True,
        "revenue_series": rev[:4],
        "profit_series": ni[:4],
        "cagr_rev_pct": _pct(cagr_rev),
        "cagr_profit_pct": _pct(cagr_profit),
        "yoy_rev_pct": _pct(yoy_rev),
        "yoy_profit_pct": _pct(yoy_profit),
        "opm_pct": opm, "gpm_pct": gpm, "npm_pct": npm,
        "roe_pct": _pct(roe) if roe is not None else None,
        "roa_pct": _pct(roa) if roa is not None else None,
        "roce_pct": roce,
        "debt_to_equity": de,
        "current_ratio": round(current_ratio, 2) if current_ratio else None,
        "cfo_pat": cfo_pat,
        "fcf_cr": _cr(fcf),
        "fscore": fscore,
        "pe": pe, "forward_pe": fwd_pe, "pb": pb, "eps": eps,
        "div_yield_pct": div_yield,
        "market_cap": mcap,
        "beta": beta,
        "analyst_rating": rec if isinstance(rec, str) else None,
        "target_mean_price": _as_frac(info.get("targetMeanPrice")),
        "financial_currency": info.get("financialCurrency"),
    }
    return out


def piotroski(info: Dict[str, Any], financials: Dict[str, Any]) -> Dict[str, Any]:
    annual = financials.get("annual") or {}
    inc = annual.get("income")
    bal = annual.get("balance")
    cf = annual.get("cashflow")
    checks: Dict[str, Any] = {}
    score = 0
    total = 0

    def add(kk: str, val: Optional[bool]):
        nonlocal score, total
        total += 1
        checks[kk] = val
        if val is True:
            score += 1

    ni = _rowv(inc, "Net Income", "Net Income Common Stockholders", n=4)
    ta = _rowv(bal, "Total Assets", n=4)
    cfo = _rowv(cf, "Operating Cash Flow", "Total Cash From Operating Activities", n=4)
    debt = _rowv(bal, "Total Debt", n=4)
    ca = _rowv(bal, "Current Assets", n=4)
    cl = _rowv(bal, "Current Liabilities", n=4)
    gp = _rowv(inc, "Gross Profit", n=4)
    rev = _rowv(inc, "Total Revenue", n=4)
    s_out = _rowv(bal, "Ordinary Shares Number", "Share Issued", n=4)

    # 1 ROA>0  2 CFO>0  3 dROA>0  4 accruals (CFO>NI)  5 dLeverage  6 dCurrentRatio
    # 7 no dilution  8 dGrossMargin  9 dAssetTurnover
    roa0 = None
    if ni and ta and ni[0] is not None and ta[0]:
        roa0 = ni[0] / ta[0]
    add("ROA > 0", roa0 is not None and roa0 > 0)
    add("CFO > 0", cfo and cfo[0] is not None and cfo[0] > 0)
    roa1 = None
    if ni and ta and len(ni) > 1 and len(ta) > 1:
        if ni[1] is not None and ta[1]:
            roa1 = ni[1] / ta[1]
    add("ROA improving", roa0 is not None and roa1 is not None and roa0 > roa1)
    add("CFO > Net Profit", cfo and ni and cfo[0] is not None and ni[0] is not None and cfo[0] > ni[0])
    d0 = debt[0] if debt else None
    d1 = debt[1] if debt and len(debt) > 1 else None
    a0 = ta[0] if ta else None
    a1 = ta[1] if ta and len(ta) > 1 else None
    lev_ok = None
    if None not in (d0, d1, a0, a1) and a0 and a1:
        lev_ok = (d0 / a0) <= (d1 / a1)
    add("Leverage not increasing", lev_ok)
    cr0 = None
    if ca and cl:
        if ca[0] and cl[0]:
            cr0 = ca[0] / cl[0]
    cr1 = None
    if ca and cl and len(ca) > 1 and len(cl) > 1:
        if ca[1] and cl[1]:
            cr1 = ca[1] / cl[1]
    add("Current ratio improving", cr0 is not None and cr1 is not None and cr0 > cr1)
    dil_ok = None
    if s_out and len(s_out) > 1 and s_out[0] is not None and s_out[1] is not None and s_out[1]:
        dil_ok = s_out[0] <= s_out[1] * 1.005
    add("No share dilution", dil_ok)
    gm0 = None
    if gp and rev and gp[0] is not None and rev[0]:
        gm0 = gp[0] / rev[0]
    gm1 = None
    if gp and rev and len(gp) > 1 and len(rev) > 1 and gp[1] is not None and rev[1]:
        gm1 = gp[1] / rev[1]
    add("Gross margin improving", gm0 is not None and gm1 is not None and gm0 > gm1)
    at0 = None
    if rev and ta and rev[0] is not None and ta[0]:
        at0 = rev[0] / ta[0]
    at1 = None
    if rev and ta and len(rev) > 1 and len(ta) > 1 and rev[1] is not None and ta[1]:
        at1 = rev[1] / ta[1]
    add("Asset turnover improving", at0 is not None and at1 is not None and at0 > at1)

    if total < 9:  # some data missing
        return {"score": score, "max": 9, "complete": False, "checks": checks}
    return {"score": score, "max": 9, "complete": True, "checks": checks}


# ------------------------------------------------------------------ converters
def _ratio(new, old) -> Optional[float]:
    try:
        if new is None or old is None or old == 0:
            return None
        return float(new) / float(old) - 1
    except Exception:
        return None


def _pct(x) -> Optional[float]:
    return round(x * 100, 1) if x is not None else None


def _as_frac(v) -> Optional[float]:
    try:
        v = float(v)
        if v != v:
            return None
        return v
    except Exception:
        return None


def _de_ratio(v) -> Optional[float]:
    if v is None:
        return None
    try:
        v = float(v)
        if v >= 20:  # yfinance often reports as percentage
            v = v / 100
        return round(v, 2)
    except Exception:
        return None


def _div_yield(info: Dict[str, Any], price: Optional[float] = None) -> Optional[float]:
    """Prefer dividendRate/price (reliable) over dividendYield (semantics vary)."""
    y = None
    rate = _as_frac(info.get("dividendRate"))
    try:
        if rate is not None and price and price > 0:
            y = rate / price * 100
        else:
            v = _as_frac(info.get("dividendYield"))
            if v is not None:
                y = v * 100 if v < 0.5 else v       # fraction->pct; pct passthrough
    except Exception:
        y = None
    if y is None or y > 15:      # >15% yield = data anomaly, not real
        return None
    return round(y, 2)


def _cr(v) -> Optional[float]:
    if v is None:
        return None
    return round(v / 1e7, 0)  # 1 crore


def data_quality(info: Dict[str, Any], financials: Dict[str, Any]) -> Dict[str, Any]:
    core = [
        info.get("returnOnEquity") is not None,
        info.get("debtToEquity") is not None,
        info.get("marketCap") is not None,
        info.get("trailingPE") is not None or info.get("trailingEps") is not None,
        bool((financials.get("annual") or {}).get("income")),
        bool((financials.get("annual") or {}).get("cashflow")),
    ]
    frac = sum(1 for x in core if x) / len(core)
    if frac >= 0.85:
        grade = "HIGH"
    elif frac >= 0.6:
        grade = "MEDIUM"
    else:
        grade = "LOW"
    return {"grade": grade, "score": round(frac, 2)}
