"""
BharatInvest Allocation Guru — "itna budget hai, kisme kitna dalu"
[PyPortfolioOpt ka core idea, but zero-dependency native implementation]

- Latest scan ke BUY / STRONG BUY stocks (score-order me top N)
- Weights: inverse-volatility — kam volatile stock ko zyada weight
  (risk-balanced; 35% single-stock cap ke saath)
- Har stock ke liye: amount ₹, whole shares, leftover ₹
- Ye investment allocation planning hai — trading signal NAHI.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import app.datafeed as df

CAP = 0.35          # single stock max weight
MIN_PICKS = 3


def _annual_vol(symbol: str) -> Optional[float]:
    """Daily returns se annualised vol (cached px data; naya fetch sirf zarüri ho to)."""
    try:
        rows = df.cache_get_stale(f"px:{symbol}") or df.get_ohlcv(symbol)
        closes = [float(r["close"]) for r in rows if r.get("close")]
        if len(closes) < 120:
            return None
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
        if len(rets) < 100:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return round(math.sqrt(var) * math.sqrt(252) * 100, 1)
    except Exception:
        return None


def plan(budget: float, count: int = 6) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "budget": round(budget, 0) if budget else None}
    budget = float(budget or 0)
    if budget <= 0:
        out["note"] = "Budget ₹ daalo."
        return out
    count = max(2, min(int(count or 6), 12))
    scan = df.cache_get_stale("scan4:latest") or {}
    rows = scan.get("rows") or []
    if not rows:
        out["note"] = "Scan data nahi mila — pehle Dashboard par scan warm kar lo."
        return out

    picks = [r for r in rows if (r.get("rating") or "").startswith(("STRONG BUY", "BUY"))][:count]
    if len(picks) < MIN_PICKS:
        picks = (picks + [r for r in rows if r not in picks])[:count]
    picks = [p for p in picks if p.get("price")]
    if not picks:
        out["note"] = "Buy-worthy stocks with valid price nahi mile is scan me."
        return out

    vols: List[Optional[float]] = [_annual_vol(p["symbol"]) for p in picks]
    known = [v for v in vols if v]
    weights: List[float] = []
    if len(known) >= len(picks) * 0.6:
        iv = [1.0 / v if v is not None and v > 0 else (1.0 / (sum(known) / len(known))) for v in vols]
        tot = sum(iv)
        weights = [x / tot for x in iv]
        for _ in range(25):                      # cap loop — over-35% weight re-distribute
            over = [(i, w) for i, w in enumerate(weights) if w > CAP]
            if not over:
                break
            extra = sum(w - CAP for _, w in over)
            for i, _w in over:
                weights[i] = CAP
            rest = [i for i, w in enumerate(weights) if w < CAP]
            if not rest:
                break
            rtot = sum(weights[i] for i in rest)
            for i in rest:
                weights[i] += extra * weights[i] / rtot
        weights = [w / sum(weights) for w in weights]
    else:
        weights = [1.0 / len(picks)] * len(picks)   # vol data thin -> equal weight

    allocated = 0.0
    items: List[Dict[str, Any]] = []
    for i, (p, w) in enumerate(zip(picks, weights)):
        amount = round(budget * w, 0)
        price = p.get("price")
        shares = int(amount / price) if price else 0
        spent = round(shares * price, 0)
        allocated += spent
        items.append({
            "symbol": p["symbol"], "name": p.get("name"), "sector": p.get("sector"),
            "rating": p.get("rating"), "score": p.get("total"),
            "price": price, "price_date": "",        # UI scan stamp use karega
            "vol_annual_pct": vols[i],
            "weight_pct": round(w * 100, 1),
            "amount": amount, "shares": shares, "spent": spent,
        })
    out.update({
        "available": True,
        "items": items,
        "allocated": round(allocated, 0),
        "unallocated": round(budget - allocated, 0),
        "scan_generated_at": scan.get("generated_at"),
        "method": "inverse-volatility weights, 35% single-stock cap" if len(known) >= len(picks) * 0.6
                  else "equal weights (vol data thin tha)",
        "note": "Ye research allocation hai — final decision + broker-side execution tumhara hai. "
                "Naya paisa ek saath dalne ke bajay monthly kist me spread karna aam tor par better hota hai.",
    })
    return out
