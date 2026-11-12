"""
Price Action module — 10-category checklist on daily OHLCV.
1. Market structure (HH/HL/LH/LL swings)  2. Support/Resistance zones
3. Candlestick patterns (last ~6 bars)    4. Breakout/breakdown/retest
5. Volume analysis                        6. EMAs 20/50/100/200 + crosses
7. Gap analysis (40 sessions)             8. Multi-timeframe (W/M resample)
9. Chart patterns (limited: DB/DT, H&S±inverse, bull/bear flag)
10. Price strength/weakness (body, pressure, rejection, consolidation)

Design rules (zaroori):
- Display/timing layer ONLY — yeh 5-pillar score override nahi karta.
- Pure pandas + stdlib, ZERO new dependencies.
- NEVER raises: har section apne try/except me; fail -> {"available": False}.
- Input rows = datafeed.get_ohlcv() jaisi list of dicts (date/o/h/l/c/volume).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

K_PIVOT = 3            # swing pivot: k bars dono taraf confirm
K_PIVOT_FAST = 2       # chart patterns ke liye finer pivots
GAP_WINDOW = 40        # gap scan (sessions)
VOL_CONF = 1.5         # volume-confirmed breakout (x avg20)
NEAR_PCT = 3.0         # "zone ke paas" definition (%)
EXTENDED_PCT = 8.0     # EMA20 se itna upar = extended/chasing
PATTERN_LOOKBACK = 130  # bars — pattern search window


# ----------------------------------------------------------------- utils
def _f(x) -> Optional[float]:
    try:
        v = float(x)
        if pd.isna(v):
            return None
        return v
    except Exception:
        return None


def _mean(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """(a-b)/b * 100, b != 0."""
    if a is None or b is None or not b:
        return None
    return round((a - b) / abs(b) * 100, 2)


def _prep(rows: List[Dict[str, Any]]) -> Optional[pd.DataFrame]:
    """rows -> clean ascending frame; None-close drop, dup-date drop."""
    recs = []
    for r in rows or []:
        c = _f(r.get("close"))
        if c is None:
            continue
        recs.append({"date": str(r.get("date") or "")[:10],
                     "open": _f(r.get("open")), "high": _f(r.get("high")),
                     "low": _f(r.get("low")), "close": c,
                     "volume": _f(r.get("volume")) or 0.0})
    if len(recs) < 60:
        return None
    recs.sort(key=lambda x: x["date"])
    px = pd.DataFrame(recs)
    px = px.drop_duplicates(subset="date", keep="last").reset_index(drop=True)
    return px


def _pivots(px: pd.DataFrame, k: int) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Confirmed swing pivots (k bars dono taraf) -> (swing_highs, swing_lows)."""
    highs = px["high"].tolist()
    lows = px["low"].tolist()
    n = len(px)
    sh: List[Tuple[int, float]] = []
    sl: List[Tuple[int, float]] = []
    for i in range(k, n - k):
        h, l = highs[i], lows[i]
        if h is None or l is None:
            continue
        wh = highs[i - k:i + k + 1]
        wl = lows[i - k:i + k + 1]
        if all(x is not None and x <= h for x in wh):
            sh.append((i, h))
        if all(x is not None and x >= l for x in wl):
            sl.append((i, l))
    return sh, sl


def _verdict_from_pivots(sh, sl, last_close: Optional[float]) -> Dict[str, Any]:
    """HH/HL/LH/LL counts (last comparisons) -> UP/DOWN/SIDEWAYS + break check."""
    hh = lh = hl = ll = 0
    hseq = [p for _, p in sh][-6:]
    for a, b in zip(hseq, hseq[1:]):
        if b > a:
            hh += 1
        elif b < a:
            lh += 1
    lseq = [p for _, p in sl][-6:]
    for a, b in zip(lseq, lseq[1:]):
        if b > a:
            hl += 1
        elif b < a:
            ll += 1
    verdict = "SIDEWAYS"
    if hh >= 2 and hl >= 2:
        verdict = "UP"
    elif lh >= 2 and ll >= 2:
        verdict = "DOWN"
    broke = False
    broke_dir = broke_note = None
    last_lh = sh[-1][1] if sh else None
    last_hl = sl[-1][1] if sl else None
    if verdict == "UP" and last_hl is not None and last_close is not None and last_close < last_hl:
        broke, broke_dir = True, "bearish"
        broke_note = "uptrend broken — last higher-low ke neeche close"
    elif verdict == "DOWN" and last_lh is not None and last_close is not None and last_close > last_lh:
        broke, broke_dir = True, "bullish"
        broke_note = "downtrend break — last lower-high ke upar reclaim (reversal attempt)"
    return {"verdict": verdict, "hh": hh, "hl": hl, "lh": lh, "ll": ll,
            "last_swing_high": round(last_lh, 2) if last_lh is not None else None,
            "last_swing_low": round(last_hl, 2) if last_hl is not None else None,
            "broke": broke, "broke_dir": broke_dir, "broke_note": broke_note}


# ----------------------------------------------------------------- 1. structure
def _structure(px: pd.DataFrame) -> Dict[str, Any]:
    sh, sl = _pivots(px, K_PIVOT)
    v = _verdict_from_pivots(sh, sl, px["close"].tolist()[-1])
    v["note"] = (f"{v['hh']}xHH/{v['hl']}xHL vs {v['lh']}xLH/{v['ll']}xLL "
                 f"(last confirmed swings)")
    return v


# ----------------------------------------------------------------- 2. S/R zones
def _cluster_zones(px: pd.DataFrame, sh, sl) -> List[Dict[str, Any]]:
    """Pivot prices ko 1% tolerance me cluster karo -> zones (touch-based)."""
    pts = sorted([(p, "H", i) for i, p in sh] + [(p, "L", i) for i, p in sl])
    zones: List[Dict[str, Any]] = []
    for p, kind, idx in pts:
        if zones and p <= zones[-1]["_sum"] / zones[-1]["n"] * 1.01:
            z = zones[-1]
            z["_sum"] += p
            z["n"] += 1
            z["last_idx"] = max(z["last_idx"], idx)
            z["kinds"].add(kind)
        else:
            zones.append({"_sum": p, "n": 1, "last_idx": idx, "kinds": {kind}})
    dates = px["date"].tolist()
    out = []
    for z in zones:
        price = z["_sum"] / z["n"]
        out.append({"price": round(price, 2), "touches": z["n"],
                    "last_touch": dates[min(z["last_idx"], len(dates) - 1)],
                    "has_high": "H" in z["kinds"], "has_low": "L" in z["kinds"]})
    return out


def _sr_zones(px: pd.DataFrame) -> Dict[str, Any]:
    price = px["close"].tolist()[-1]
    sh, sl = _pivots(px, K_PIVOT)
    zones = _cluster_zones(px, sh, sl)
    for z in zones:
        below = z["price"] < price
        z["kind"] = "support" if below else "resistance"
        z["dist_pct"] = round((price - z["price"]) / price * 100, 2) if below \
            else round((z["price"] - price) / price * 100, 2)
        z["major"] = z["touches"] >= 2
    zones.sort(key=lambda z: z["dist_pct"])
    sup = next((z for z in zones if z["kind"] == "support"), None)
    res = next((z for z in zones if z["kind"] == "resistance"), None)

    # flip detection: last 10 bars me zone price cross hua?
    closes = px["close"].tolist()
    flips: List[str] = []
    for z in zones[:8]:
        zp = z["price"]
        for j in range(max(1, len(closes) - 10), len(closes)):
            if closes[j - 1] >= zp and closes[j] < zp:
                flips.append(f"₹{zp:g} support → resistance tod ke (bar {len(closes) - 1 - j} pehle)")
                break
            if closes[j - 1] <= zp and closes[j] > zp:
                flips.append(f"₹{zp:g} resistance → support cross hua (bar {len(closes) - 1 - j} pehle)")
                break
    return {"available": True, "support": _trim_zone(sup), "resistance": _trim_zone(res),
            "zones": [_trim_zone(z) for z in zones[:8]], "flips": flips[:3]}


def _trim_zone(z: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not z:
        return None
    return {"price": z["price"], "touches": z["touches"], "kind": z["kind"],
            "dist_pct": z["dist_pct"], "major": z["major"],
            "last_touch": z.get("last_touch")}


# ----------------------------------------------------------------- 3. candles
def _candles(px: pd.DataFrame) -> Dict[str, Any]:
    o = px["open"].tolist(); h = px["high"].tolist()
    l = px["low"].tolist(); c = px["close"].tolist()
    n = len(px)
    out: List[Dict[str, Any]] = []
    dates = px["date"].tolist()

    def body(i):
        return abs(c[i] - o[i]) if (c[i] is not None and o[i] is not None) else None

    def rng(i):
        return (h[i] - l[i]) if (h[i] is not None and l[i] is not None and h[i] >= l[i]) else None

    for i in range(max(1, n - 6), n):
        b, r = body(i), rng(i)
        if b is None or r is None or r <= 0:
            continue
        up = c[i] > o[i]
        upw = h[i] - max(o[i], c[i])
        lww = min(o[i], c[i]) - l[i]
        bias = "bullish" if up else "bearish"
        if b <= 0.1 * r:
            out.append({"date": dates[i], "pattern": "Doji (indecision)", "bias": "neutral"})
            continue
        if b >= 0.9 * r:
            out.append({"date": dates[i], "pattern": "Marubozu (full-body)", "bias": bias})
        # 3-bar stars pehle (more specific)
        if i >= 2:
            b1, b3 = body(i - 2), body(i)
            if b1 and b1 > 0.5 * rng(i - 2) and b <= 0.4 * rng(i - 1):
                mid = (o[i - 2] + c[i - 2]) / 2
                if c[i - 2] < o[i - 2] and up and c[i] > mid:
                    out.append({"date": dates[i], "pattern": "Morning star", "bias": "bullish"})
                    continue
                if c[i - 2] > o[i - 2] and not up and c[i] < mid:
                    out.append({"date": dates[i], "pattern": "Evening star", "bias": "bearish"})
                    continue
        # engulfing
        if i >= 1 and body(i - 1):
            po, pc = o[i - 1], c[i - 1]
            if up and pc < po and o[i] <= pc and c[i] >= po:
                out.append({"date": dates[i], "pattern": "Bullish engulfing", "bias": "bullish"})
                continue
            if not up and pc > po and o[i] >= pc and c[i] <= po:
                out.append({"date": dates[i], "pattern": "Bearish engulfing", "bias": "bearish"})
                continue
        # hammer / shooting star (= pin bars)
        if lww >= 2 * b and upw <= b:
            out.append({"date": dates[i], "pattern": "Hammer / bullish pin bar", "bias": "bullish"})
        elif upw >= 2 * b and lww <= b:
            out.append({"date": dates[i], "pattern": "Shooting star / bearish pin bar", "bias": "bearish"})
    last = {"date": dates[-1], "open": o[-1], "high": h[-1], "low": l[-1], "close": c[-1],
            "body_pct": round(body(n - 1) / rng(n - 1) * 100, 0) if body(n - 1) is not None and rng(n - 1) else None}
    bull = sum(1 for x in out if x["bias"] == "bullish")
    bear = sum(1 for x in out if x["bias"] == "bearish")
    return {"available": True, "patterns": out[:6], "last_bar": last,
            "bull_count": bull, "bear_count": bear,
            "note": "Hammer/shooting star hi pin bars hain; 6 sessions scan"}


# ----------------------------------------------------------------- 4. breakout
def _breakout(px: pd.DataFrame, sr: Dict[str, Any]) -> Dict[str, Any]:
    c = px["close"].tolist(); h = px["high"].tolist()
    l = px["low"].tolist(); v = px["volume"].tolist()
    n = len(px)
    res_z = (sr or {}).get("resistance") or {}
    sup_z = (sr or {}).get("support") or {}

    def avg_vol(i, w=20):
        xs = [x for x in v[max(0, i - w):i] if x is not None]
        return sum(xs) / len(xs) if xs else None

    # --- upside: last 10 bars me close > prior-20-high (ya zone breakout)
    up_hit = None
    for i in range(n - 1, max(1, n - 11) - 1, -1):
        win = [x for x in h[max(0, i - 20):i] if x is not None]
        if not win:
            continue
        lvl = max(win)
        if c[i] is not None and c[i] > lvl and (c[i - 1] is None or c[i - 1] <= lvl):
            up_hit = {"i": i, "level": lvl}
            break
        if res_z.get("price") and c[i] is not None and c[i] > res_z["price"] \
                and (c[i - 1] is None or c[i - 1] <= res_z["price"]):
            up_hit = {"i": i, "level": res_z["price"]}
            break
    # --- downside mirror
    dn_hit = None
    for i in range(n - 1, max(1, n - 11) - 1, -1):
        win = [x for x in l[max(0, i - 20):i] if x is not None]
        if not win:
            continue
        lvl = min(win)
        if c[i] is not None and c[i] < lvl and (c[i - 1] is None or c[i - 1] >= lvl):
            dn_hit = {"i": i, "level": lvl}
            break
        if sup_z.get("price") and c[i] is not None and c[i] < sup_z["price"] \
                and (c[i - 1] is None or c[i - 1] >= sup_z["price"]):
            dn_hit = {"i": i, "level": sup_z["price"]}
            break

    def finish(hit, direction: str) -> Dict[str, Any]:
        if not hit:
            return {"status": "none", "note": ""}
        i, lvl = hit["i"], hit["level"]
        av = avg_vol(i)
        vol_conf = bool(av and v[i] and v[i] >= VOL_CONF * av)
        bars_ago = n - 1 - i
        through = (c[-1] < lvl) if direction == "up" else (c[-1] > lvl)
        if through:      # abhi level ke galat taraf close — break fail
            if direction == "up":
                return {"status": "false_breakout", "level": round(lvl, 2),
                        "bars_ago": bars_ago, "volume_confirmed": vol_conf,
                        "retest_done": False,
                        "note": "level ke upar ja ke wapas neeche — trap"}
            return {"status": "false_breakdown", "level": round(lvl, 2),
                    "bars_ago": bars_ago, "volume_confirmed": vol_conf,
                    "retest_done": False,
                    "note": "failed breakdown — wapas upar (bullish sign)"}
        # retest: baad me level touch + close wapas sahi taraf?
        retest = False
        for j in range(i + 1, min(n, i + 9)):
            if direction == "up" and l[j] is not None and l[j] <= lvl * 1.01:
                retest = any(x is not None and x > lvl for x in c[j:n])
                break
            if direction == "down" and h[j] is not None and h[j] >= lvl * 0.99:
                retest = any(x is not None and x < lvl for x in c[j:n])
                break
        if direction == "up":
            status = "fresh_breakout" if bars_ago <= 5 else ("breakout_retest" if retest else "above_level")
        else:
            status = "fresh_breakdown" if bars_ago <= 5 else "breakdown"
        return {"status": status, "level": round(lvl, 2), "bars_ago": bars_ago,
                "volume_confirmed": vol_conf, "retest_done": bool(retest),
                "note": "volume confirm" if vol_conf else "volume weak"}

    if up_hit:
        out = finish(up_hit, "up")
    elif dn_hit:
        out = finish(dn_hit, "down")
    else:
        out = {"status": "none", "level": None, "bars_ago": None,
               "volume_confirmed": False, "retest_done": False,
               "note": "koi fresh 20-day range break nahi"}
    return out


# ----------------------------------------------------------------- 5. volume
def _volume(px: pd.DataFrame) -> Dict[str, Any]:
    v = [x or 0.0 for x in px["volume"].tolist()]
    c = px["close"].tolist()
    n = len(px)
    avg20 = _mean(v[-20:]) or 0.0
    avg60 = _mean(v[-60:]) or 0.0
    last_ratio = round(v[-1] / avg20, 2) if avg20 else None
    spike = bool(avg20 and v[-1] >= 2 * avg20)
    up = dn = 0.0
    for i in range(max(1, n - 20), n):
        if c[i] is None or c[i - 1] is None:
            continue
        if c[i] > c[i - 1]:
            up += v[i]
        elif c[i] < c[i - 1]:
            dn += v[i]
    bp = round(up / (up + dn) * 100, 0) if (up + dn) else None
    v10, vprev10 = _mean(v[-10:]) or 0.0, _mean(v[-20:-10]) or 0.0
    vtrend = "flat"
    if vprev10 and v10 / vprev10 > 1.15:
        vtrend = "rising"
    elif vprev10 and v10 / vprev10 < 0.85:
        vtrend = "falling"
    chg20 = _pct(c[-1], c[-21] if n >= 21 else None) or 0.0
    if chg20 > 3 and vtrend == "falling":
        read = "weak rally — price upar par volume girta (divergence)"
    elif chg20 < -3 and vtrend == "rising":
        read = "distribution — girta price, badhta volume (bearish)"
    elif chg20 > 3 and vtrend == "rising":
        read = "healthy rally — price + volume dono"
    elif chg20 < -3:
        read = "girta price, quiet volume — sellers aggressive nahi"
    else:
        read = "volume normal"
    return {"available": True, "avg20": int(avg20), "avg60": int(avg60),
            "last_ratio": last_ratio, "spike": spike, "buying_pressure_pct": bp,
            "trend": vtrend, "read": read,
            "note": "delivery % NSE ki alag file me — abhi nahi (optional add)"}


# ----------------------------------------------------------------- 6. EMAs
def _emas(px: pd.DataFrame) -> Dict[str, Any]:
    closes = px["close"]
    n = len(px)
    e: Dict[int, Optional[float]] = {}
    for span in (20, 50, 100, 200):
        e[span] = round(float(closes.ewm(span=span, adjust=False).mean().iloc[-1]), 2) \
            if n >= span else None
    price = float(closes.iloc[-1])
    bull_stack = e[20] and e[50] and price > e[20] > e[50] and (e[100] is None or price > e[100]) \
        and (e[200] is None or price > e[200])
    bear_stack = e[20] and e[50] and price < e[20] < e[50] and (e[100] is None or price < e[100]) \
        and (e[200] is None or price < e[200])
    stack = "bullish" if bull_stack else ("bearish" if bear_stack else "mixed")
    golden = death = None
    if n >= 200:
        d = closes.ewm(span=50, adjust=False).mean() - closes.ewm(span=200, adjust=False).mean()
        dl = d.tolist()
        for i in range(n - 1, 200, -1):
            if dl[i] > 0 and dl[i - 1] <= 0:
                golden = n - 1 - i
                break
            if dl[i] < 0 and dl[i - 1] >= 0:
                death = n - 1 - i
                break
    ext = _pct(price, e[20])
    return {"available": True, "ema20": e[20], "ema50": e[50], "ema100": e[100],
            "ema200": e[200], "stack": stack,
            "golden_cross_days": golden, "death_cross_days": death,
            "extended_pct": ext,
            "note": "EMA200 ke liye 200+ bars chahiye" if e[200] is None else ""}


# ----------------------------------------------------------------- 7. gaps
def _gaps(px: pd.DataFrame) -> Dict[str, Any]:
    o = px["open"].tolist(); h = px["high"].tolist()
    l = px["low"].tolist(); c = px["close"].tolist()
    n = len(px)
    dates = px["date"].tolist()
    out: List[Dict[str, Any]] = []
    last_gap: Dict[str, int] = {}
    for i in range(max(1, n - GAP_WINDOW), n):
        if o[i] is None or h[i - 1] is None or l[i - 1] is None:
            continue
        direction = None
        if o[i] > h[i - 1] * 1.002:
            direction = "up"
            g_lo, g_hi = h[i - 1], o[i]
        elif o[i] < l[i - 1] * 0.998:
            direction = "down"
            g_lo, g_hi = o[i], l[i - 1]
        else:
            continue
        fill_idx = None
        for j in range(i + 1, n):
            if direction == "up" and l[j] is not None and l[j] <= g_lo:
                fill_idx = j
                break
            if direction == "down" and h[j] is not None and h[j] >= g_hi:
                fill_idx = j
                break
        gap_pct = round((g_hi - g_lo) / g_lo * 100, 2) if g_lo else None
        typ = "common"
        if last_gap.get(direction) is not None and i - last_gap[direction] <= 10:
            typ = "runaway"
        elif not last_gap.get(direction) and all(i - k >= 10 for k in last_gap.values()):
            prior = _pct(c[i - 1], c[max(0, i - 11)])
            if prior is not None and abs(prior) < 5:
                typ = "breakaway"
        if direction == "up" and fill_idx is not None and fill_idx - i <= 3:
            typ = "exhaustion?"
        out.append({"date": dates[i], "direction": direction, "pct": gap_pct,
                    "filled": fill_idx is not None,
                    "fill_date": dates[fill_idx] if fill_idx is not None else None,
                    "type": typ})
        last_gap[direction] = i
    unfilled_up = [g for g in out if g["direction"] == "up" and not g["filled"]]
    return {"available": True, "list": list(reversed(out[:6])),
            "unfilled_recent": len(unfilled_up),
            "note": "40 sessions scan; exhaustion gap = jaldi fill hua gap-up"}


# ----------------------------------------------------------------- 8. MTF
def _resample(px: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
    try:
        x = px.copy()
        x.index = pd.to_datetime(x["date"])
        agg = x.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                    "close": "last", "volume": "sum"})
        agg = agg.dropna(subset=["close"]).reset_index(drop=True)
        return agg if len(agg) >= 12 else None
    except Exception:
        return None


def _mtf(px: pd.DataFrame) -> Dict[str, Any]:
    res: Dict[str, str] = {}
    for name, rule, k, min_bars in (("daily", "D", K_PIVOT, 60),
                                    ("weekly", "W-FRI", 2, 20),
                                    ("monthly", "ME", 2, 12)):
        f = px if rule == "D" else _resample(px, rule)
        if f is None or len(f) < min_bars:
            res[name] = "NA"
            continue
        sh, sl = _pivots(f, k)
        res[name] = _verdict_from_pivots(sh, sl, f["close"].tolist()[-1])["verdict"]
    vals = [v for v in res.values() if v not in ("NA",)]
    aligned = len(set(vals)) == 1 and len(vals) >= 2
    return {"available": True, **res,
            "alignment": "ALIGNED" if aligned else "MIXED",
            "note": "weekly/monthly bars daily se resample (free, koi naya source nahi)"}


# ----------------------------------------------------------------- 9. patterns
def _patterns(px: pd.DataFrame) -> Dict[str, Any]:
    sh, sl = _pivots(px, K_PIVOT_FAST)
    h = px["high"].tolist(); l = px["low"].tolist(); c = px["close"].tolist()
    v = [x or 0.0 for x in px["volume"].tolist()]
    n = len(px)
    lo_n = max(0, n - PATTERN_LOOKBACK)
    price = c[-1]
    found: List[Dict[str, Any]] = []

    def mv(xs, a, b, fn):
        seg = [x for x in xs[a:b + 1] if x is not None]
        return fn(seg) if seg else None

    # Double Bottom / Double Top (latest pair first)
    lows = [(i, p) for i, p in sl if i >= lo_n]
    for bi in range(len(lows) - 1, 0, -1):
        ib, lb = lows[bi]
        for ai in range(bi - 1, -1, -1):
            ia, la = lows[ai]
            if la and lb and abs(la - lb) / max(la, lb) <= 0.03:
                neckline = mv(h, ia, ib, max)
                base = max(la, lb)
                if neckline and base and neckline >= base * 1.04:
                    status = "confirmed" if price > neckline else \
                        ("forming" if price >= neckline * 0.97 else None)
                    if status:
                        found.append({"pattern": "Double Bottom (W)", "status": status,
                                      "neckline": round(neckline, 2),
                                      "target": round(neckline + (neckline - la), 2) if la else None,
                                      "note": f"lows ₹{la:g} & ₹{lb:g}"})
                        break
        else:
            continue
        break
    highs = [(i, p) for i, p in sh if i >= lo_n]
    for bi in range(len(highs) - 1, 0, -1):
        ib, hb_ = highs[bi]
        for ai in range(bi - 1, -1, -1):
            ia, ha = highs[ai]
            if ha and hb_ and abs(ha - hb_) / max(ha, hb_) <= 0.03:
                neckline = mv(l, ia, ib, min)
                top = min(ha, hb_)
                if neckline and top and top <= neckline * 0.96:
                    status = "confirmed" if price < neckline else \
                        ("forming" if price <= neckline * 1.03 else None)
                    if status:
                        found.append({"pattern": "Double Top (M)", "status": status,
                                      "neckline": round(neckline, 2),
                                      "target": round(neckline - (ha - neckline), 2) if ha else None,
                                      "note": f"tops ₹{ha:g} & ₹{hb_:g}"})
                        break
        else:
            continue
        break

    # Head & Shoulders + inverse (three pivots)
    for bi in range(len(highs) - 2, 0, -1):
        i3, p3 = highs[bi]
        i2, p2 = highs[bi - 1]
        i1, p1 = highs[bi - 2]
        if p2 >= p1 * 1.02 and p2 >= p3 * 1.02 and abs(p1 - p3) <= 0.05 * p2:
            t1 = mv(l, i1, i2, min); t2 = mv(l, i2, i3, min)
            if t1 and t2:
                neckline = (t1 + t2) / 2
                status = "confirmed" if price < neckline else \
                    ("forming" if price <= neckline * 1.03 else None)
                if status:
                    found.append({"pattern": "Head & Shoulders", "status": status,
                                  "neckline": round(neckline, 2),
                                  "target": round(neckline - (p2 - neckline), 2),
                                  "note": f"head ₹{p2:g}"})
                    break
    for bi in range(len(lows) - 2, 0, -1):
        i3, p3 = lows[bi]
        i2, p2 = lows[bi - 1]
        i1, p1 = lows[bi - 2]
        if p2 <= p1 * 0.98 and p2 <= p3 * 0.98 and abs(p1 - p3) <= 0.05 * p2 and p2:
            t1 = mv(h, i1, i2, max); t2 = mv(h, i2, i3, max)
            if t1 and t2:
                neckline = (t1 + t2) / 2
                status = "confirmed" if price > neckline else \
                    ("forming" if price >= neckline * 0.97 else None)
                if status:
                    found.append({"pattern": "Inverse Head & Shoulders", "status": status,
                                  "neckline": round(neckline, 2),
                                  "target": round(neckline + (neckline - p2), 2),
                                  "note": f"head-low ₹{p2:g}"})
                    break

    # Bull / Bear flag (pole + consolidation)
    bull_done = bear_done = False
    for flag_len in range(4, 11):
        fs = n - flag_len
        for pole_len in range(5, 11):
            ps = fs - pole_len
            if ps < 0:
                continue
            p_lo, p_hi = mv(l, ps, fs - 1, min), mv(h, ps, fs - 1, max)
            end_c = c[fs - 1]
            if not (p_lo and p_hi and end_c):
                continue
            f_hi, f_lo = mv(h, fs, n - 1, max), mv(l, fs, n - 1, min)
            if not (f_hi and f_lo):
                continue
            gain = (end_c - p_lo) / p_lo
            rng = (f_hi - f_lo) / price
            v_dry = (_mean(v[fs:]) or 0) <= (_mean(v[ps:fs]) or 0) * 1.2
            if not bull_done and gain >= 0.08 and rng <= 0.05 and f_lo >= p_hi * 0.85:
                status = "confirmed" if price > f_hi else \
                    ("forming" if price >= f_hi * 0.99 else None)
                if status:
                    found.append({"pattern": "Bull flag", "status": status,
                                  "trigger": round(f_hi, 2), "target": round(f_hi + (p_hi - p_lo) * 0.6, 2),
                                  "note": f"pole +{gain * 100:.0f}%, flag {flag_len} bar{', volume dry' if v_dry else ''}"})
                    bull_done = True
            drop = (p_hi - end_c) / p_hi
            if not bear_done and drop >= 0.08 and rng <= 0.05 and f_hi <= p_lo * 1.15:
                status = "confirmed" if price < f_lo else \
                    ("forming" if price <= f_lo * 1.01 else None)
                if status:
                    found.append({"pattern": "Bear flag", "status": status,
                                  "trigger": round(f_lo, 2), "target": round(f_lo - (p_hi - p_lo) * 0.6, 2),
                                  "note": f"pole -{drop * 100:.0f}%, flag {flag_len} bar"})
                    bear_done = True
    found = found[:3]
    return {"available": True, "list": found,
            "note": "limited set (DB/DT, H&S±inverse, flags) — fuzzy by nature, kabhi bhi akela signal nahi"}


# ----------------------------------------------------------------- 10. strength
def _strength(px: pd.DataFrame, sr: Dict[str, Any]) -> Dict[str, Any]:
    o = px["open"].tolist(); h = px["high"].tolist()
    l = px["low"].tolist(); c = px["close"].tolist()
    n = len(px)
    price = c[-1]
    b = abs(c[-1] - o[-1]) if (c[-1] is not None and o[-1] is not None) else None
    r = (h[-1] - l[-1]) if (h[-1] is not None and l[-1] is not None and h[-1] >= l[-1]) else None
    body_pct = round(b / r * 100, 0) if (b is not None and r) else None
    close_pos = round((c[-1] - l[-1]) / r * 100, 0) if r else None
    up = dn = 0.0
    for i in range(max(1, n - 20), n):
        if c[i] is None or c[i - 1] is None:
            continue
        if c[i] > c[i - 1]:
            up += (px["volume"].tolist()[i] or 0)
        elif c[i] < c[i - 1]:
            dn += (px["volume"].tolist()[i] or 0)
    bp = round(up / (up + dn) * 100, 0) if (up + dn) else None
    h10 = mv_safe(h[-10:]); l10 = mv_safe(l[-10:])
    consol = bool(h10 and l10 and price and (h10 - l10) / price <= 0.05)
    rejection = None
    sup = (sr or {}).get("support") or {}
    res = (sr or {}).get("resistance") or {}
    lww = (min(o[-1], c[-1]) - l[-1]) if all(x is not None for x in (o[-1], c[-1], l[-1])) else None
    upw = (h[-1] - max(o[-1], c[-1])) if all(x is not None for x in (o[-1], c[-1], h[-1])) else None
    if sup.get("price") and sup.get("dist_pct", 99) <= NEAR_PCT and lww and b and lww >= 1.5 * b:
        rejection = "support pe bullish rejection (lambi lower-wick)"
    elif res.get("price") and res.get("dist_pct", 99) <= NEAR_PCT and upw and b and upw >= 1.5 * b:
        rejection = "resistance pe bearish rejection (lambi upper-wick)"
    verdict = "neutral"
    if close_pos is not None and bp is not None:
        if close_pos >= 70 and bp >= 55:
            verdict = "strong"
        elif close_pos <= 30 and bp <= 45:
            verdict = "weak"
    return {"available": True, "body_pct": body_pct, "close_pos_pct": close_pos,
            "buying_pressure_pct": bp, "consolidation": consol, "rejection": rejection,
            "verdict": verdict}


def mv_safe(xs: List[Optional[float]]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return max(xs) if xs else None


# ----------------------------------------------------------------- timing
def _timing(structure, sr, breakout, emas, strength, mtf) -> Dict[str, Any]:
    reasons: List[str] = []
    sup = (sr or {}).get("support") or {}
    res = (sr or {}).get("resistance") or {}
    trend = structure.get("verdict")
    near_sup = sup.get("dist_pct") is not None and sup["dist_pct"] <= NEAR_PCT
    near_res = res.get("dist_pct") is not None and res["dist_pct"] <= NEAR_PCT
    extended = (emas.get("extended_pct") or 0) >= EXTENDED_PCT
    bo = breakout.get("status")
    vol_bo = breakout.get("volume_confirmed")

    if structure.get("broke_dir") == "bearish":
        signal = "CAUTION"
        reasons.append(structure.get("broke_note") or "uptrend structure broke")
    elif bo == "fresh_breakout" and vol_bo:
        signal = "BREAKOUT"
        reasons.append(f"fresh volume-confirmed breakout ₹{breakout.get('level')} se")
    elif structure.get("broke_dir") == "bullish":
        signal = "TURN"
        reasons.append(structure.get("broke_note") or "downtrend reclaim — reversal attempt")
        if bo == "fresh_breakout":
            reasons.append(f"breakout ₹{breakout.get('level')} se{'' if vol_bo else ' (volume confirm nahi)'}")
        if near_res:
            reasons.append(f"resistance ₹{res.get('price')} ({res.get('dist_pct')}% upar) — yahan supply aa sakti hai")
    elif trend == "DOWN":
        signal = "WAIT"
        reasons.append("downtrend (LH/LL) — falling knife mat pakdo")
    elif trend == "UP" and near_sup:
        signal = "BUY_ZONE"
        reasons.append(f"uptrend + support ₹{sup.get('price')} ke paas ({sup.get('dist_pct')}%)")
    elif trend == "UP" and (near_res or extended):
        signal = "WAIT"
        if near_res:
            reasons.append(f"resistance ₹{res.get('price')} ke paas ({res.get('dist_pct')}%) — chasing risk")
        if extended:
            reasons.append(f"EMA20 se {emas.get('extended_pct')}% extended")
    elif trend == "SIDEWAYS" and near_sup:
        signal = "BUY_ZONE"
        reasons.append("range ke neeche (support pe) — accumulate zone")
    elif trend == "SIDEWAYS":
        signal = "WAIT"
        reasons.append("sideways range — breakout ka intezaar")
    else:
        signal = "NEUTRAL"
        reasons.append("koi strong edge nahi — score aur fundamentals dekho")
    if mtf.get("alignment") == "ALIGNED":
        reasons.append(f"MTF aligned: {'/'.join(str(mtf.get(k, '')) for k in ('daily', 'weekly', 'monthly'))}")
    if strength.get("rejection"):
        reasons.append(strength["rejection"])
    return {"signal": signal, "reasons": reasons[:5],
            "note": "PA sirf timing batata hai — kya khareedna hai wo 5-pillar score decide karta hai"}


# ----------------------------------------------------------------- main
def analyze_price_action(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "warnings": []}
    try:
        px = _prep(rows)
        if px is None:
            out["warnings"].append("60+ clean bars nahi mile — PA limited/NA")
            return out
        price = float(px["close"].iloc[-1])
        out.update({"available": True, "bars": len(px), "price": round(price, 2),
                    "date": px["date"].tolist()[-1]})
        sec = out
        try:
            sec["structure"] = _structure(px)
        except Exception as e:
            sec["structure"] = {"verdict": "NA", "error": str(e)[:120]}
        try:
            sec["sr"] = _sr_zones(px)
        except Exception as e:
            sec["sr"] = {"available": False, "error": str(e)[:120]}
        try:
            sec["candles"] = _candles(px)
        except Exception as e:
            sec["candles"] = {"available": False, "error": str(e)[:120]}
        try:
            sec["breakout"] = _breakout(px, sec.get("sr") or {})
        except Exception as e:
            sec["breakout"] = {"status": "none", "error": str(e)[:120]}
        try:
            sec["volume"] = _volume(px)
        except Exception as e:
            sec["volume"] = {"available": False, "error": str(e)[:120]}
        try:
            sec["emas"] = _emas(px)
        except Exception as e:
            sec["emas"] = {"available": False, "error": str(e)[:120]}
        try:
            sec["gaps"] = _gaps(px)
        except Exception as e:
            sec["gaps"] = {"available": False, "error": str(e)[:120]}
        try:
            sec["mtf"] = _mtf(px)
        except Exception as e:
            sec["mtf"] = {"available": False, "error": str(e)[:120]}
        try:
            sec["patterns"] = _patterns(px)
        except Exception as e:
            sec["patterns"] = {"available": False, "error": str(e)[:120]}
        try:
            sec["strength"] = _strength(px, sec.get("sr") or {})
        except Exception as e:
            sec["strength"] = {"available": False, "error": str(e)[:120]}
        try:
            sec["timing"] = _timing(sec.get("structure") or {}, sec.get("sr") or {},
                                    sec.get("breakout") or {}, sec.get("emas") or {},
                                    sec.get("strength") or {}, sec.get("mtf") or {})
        except Exception as e:
            sec["timing"] = {"signal": "NEUTRAL", "reasons": [], "error": str(e)[:120]}
        if len(px) < 200:
            out["warnings"].append(f"sirf {len(px)} bars — EMA200/long patterns limited")
        out["warnings"].append("delivery % data optional hai (NSE alag file) — volume analysis price-based")
        # one-line summary
        st = sec.get("structure") or {}
        bo = sec.get("breakout") or {}
        cd = sec.get("candles") or {}
        sig = (sec.get("timing") or {}).get("signal")
        last_pat = (cd.get("patterns") or [{}])[-1].get("pattern") if cd.get("patterns") else None
        parts = [f"{st.get('verdict', 'NA')} structure"]
        m = sec.get("mtf") or {}
        if m.get("alignment") == "ALIGNED":
            parts.append(f"MTF aligned ({m.get('daily')}/{m.get('weekly')}/{m.get('monthly')})")
        if bo.get("status") not in (None, "none"):
            parts.append(str(bo.get("status")).replace("_", " ") + ("+vol" if bo.get("volume_confirmed") else ""))
        if last_pat:
            parts.append(f"last candle: {last_pat}")
        parts.append(f"→ {sig}")
        out["summary"] = " · ".join(parts)
    except Exception as e:
        out = {"available": False, "error": str(e)[:200]}
    return out


# ------------------------------------------------- portfolio hint (light)
def pa_hint(rows: List[Dict[str, Any]], price: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Portfolio ke liye halka hint: trend + nearest S/R distance."""
    try:
        px = _prep(rows)
        if px is None:
            return None
        sh, sl = _pivots(px, K_PIVOT)
        st = _verdict_from_pivots(sh, sl, float(px["close"].iloc[-1]))
        price = price or float(px["close"].iloc[-1])
        zones = _cluster_zones(px, sh, sl)
        sup = max((z["price"] for z in zones if z["price"] < price), default=None)
        res = min((z["price"] for z in zones if z["price"] > price), default=None)
        return {"trend": st["verdict"], "broke": bool(st.get("broke")),
                "support": round(sup, 2) if sup else None,
                "support_dist_pct": _pct(price, sup),
                "resistance": round(res, 2) if res else None,
                "resistance_dist_pct": _pct(res, price)}
    except Exception:
        return None


# ------------------------------------------------- scan row fields
def scan_fields(rep: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """(trend, breakout) — scan table columns ke liye short strings."""
    if not rep or not rep.get("available"):
        return None, None
    st = rep.get("structure") or {}
    bo = rep.get("breakout") or {}
    trend = st.get("verdict")
    if st.get("broke") and trend:
        trend = f"{trend}?"
    bo_map = {"fresh_breakout": "fresh", "fresh_breakdown": "breakdown",
              "breakout_retest": "retest", "false_breakout": "false",
              "false_breakdown": "false"}
    status = bo_map.get(bo.get("status") or "")
    if status == "fresh" and bo.get("volume_confirmed"):
        status = "fresh+"     # volume-confirmed breakout (checklist #4)
    return trend, status
