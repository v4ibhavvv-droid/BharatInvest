"""
BharatInvest local portfolio — apni holdings ka sach (investment-only).
- portfolio.json me save hota hai (sirf tumhari machine par)
- Manual add/update ya Zerodha/Groww holdings CSV import
- Current value / PnL / weighted PnL (Yahoo EOD, date stamped)
- Bought date dene par per-holding annualized return (CAGR) + XIRR-style
- Sector allocation + Nifty 50 ka 12M return benchmark context
[tatvadesai/tearsheet, lokal-first portfolio-tracker ideas — sasta & simple]
"""
from __future__ import annotations

import csv
import io
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import app.datafeed as df
import app.priceaction as pa

ROOT = df.ROOT
PORT_PATH = ROOT / "portfolio.json"
_LOCK = threading.Lock()

_SYM_RX = re.compile(r"^[A-Z0-9&\-\.]{2,20}$")


# --------------------------------------------------------------- persistence
def _load() -> Dict[str, Any]:
    try:
        j = json.loads(PORT_PATH.read_text())
        if isinstance(j, dict) and isinstance(j.get("holdings"), list):
            return j
    except Exception:
        pass
    return {"holdings": []}


def _save(h: Dict[str, Any]) -> None:
    h["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = PORT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(h, indent=1, ensure_ascii=False))
    tmp.replace(PORT_PATH)


def replace_all(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate + normalize (dedup by symbol, numeric BSE resolve) and persist."""
    out: Dict[str, Dict[str, Any]] = {}
    for h in holdings or []:
        if not isinstance(h, dict):
            continue
        sym = str(h.get("symbol") or "").strip().upper()
        if not sym:
            continue
        sym = re.sub(r"\s+", "", sym)
        if sym.startswith("^") or not _SYM_RX.match(sym):
            continue
        sym = df._clean_symbol(sym)
        try:
            qty = float(h.get("qty"))
            avg = float(h.get("avg"))
        except Exception:
            continue
        if qty <= 0 or avg <= 0:
            continue
        m = re.fullmatch(r"(\d{5,6})\.(?:BO|BS|NS)", sym)
        if m:                                   # numeric BSE code -> NSE (jaise 500325 -> RELIANCE)
            res = df.resolve_numeric_bse(sym)
            if res and res.get("yahoo_symbol"):
                sym = res["yahoo_symbol"]
        bought = str(h.get("bought") or "").strip() or None
        if bought:
            try:
                datetime.strptime(bought[:10], "%Y-%m-%d")
            except Exception:
                bought = None
        out[sym] = {"symbol": sym, "qty": round(qty, 6), "avg": round(avg, 4), "bought": bought}
    with _LOCK:
        _save({"holdings": list(out.values())})
    return list(out.values())


def import_csv(text: str) -> Dict[str, Any]:
    """Zerodha Holdings CSV / Groww holdings CSV / simple
    symbol,qty,avg,date file — tolerant parser."""
    res: Dict[str, Any] = {"path": "csv", "rows_found": 0, "added": 0, "skipped": 0, "note": ""}
    try:
        rd = csv.reader(io.StringIO(text or ""))
        rows = [r for r in rd if any(str(c).strip() for c in r)]
    except Exception as e:
        res["note"] = f"CSV parse fail: {e}"
        return res
    if not rows:
        res["note"] = "File khaali hai."
        return res
    # header dhoondo (Instrument / Symbol / Qty / Average cost ...)
    hdr_i, col = -1, {}
    for i, r in enumerate(rows[:15]):
        low = [str(c).strip().lower().rstrip(".") for c in r]
        for j, c in enumerate(low):
            if c in ("instrument", "symbol", "trading symbol", "stock", "ticker", "script"):
                col.setdefault("symbol", j)
            elif c.startswith(("qty", "quantity", "number")):
                col.setdefault("qty", j)
            elif "average cost" in c or "avg" in c or "average price" in c or c == "price":
                col.setdefault("avg", j)
            elif "date" in c:
                col.setdefault("date", j)
        if "symbol" in col and "qty" in col and "avg" in col:
            hdr_i = i
            break
    # header na mila to pehli line hi data maan lo (symbol,qty,avg[,date])
    if hdr_i < 0:
        hdr_i = -1
        col = {"symbol": 0, "qty": 1, "avg": 2, "date": 3} if rows and len(rows[0]) >= 3 else dict(col)
    current = _load().get("holdings", [])
    parsed: List[Dict[str, Any]] = [dict(h) for h in current]
    seen = {h["symbol"] for h in parsed}

    def _num(x) -> Optional[float]:
        try:
            v = float(str(x).replace(",", "").replace("₹", "").strip())
            return v if v == v else None
        except Exception:
            return None

    for r in rows[hdr_i + 1 if hdr_i >= 0 else 0:]:
        if len(r) <= (col.get("avg") or 1):
            continue
        sym = str(r[col["symbol"]] if col.get("symbol") is not None and col["symbol"] < len(r) else "").strip().upper()
        sym = re.sub(r"[-\s](EQ|BE|BZ|BT|BM)$", "", sym)          # Zerodha "-EQ" suffix
        sym = df._clean_symbol(sym) if sym else ""                # "TCS" -> "TCS.NS" (matching for dedupe)
        if not sym:
            continue
        qty_n = _num(r[col["qty"]]) if col.get("qty") is not None and col["qty"] < len(r) else None
        avg_n = _num(r[col["avg"]]) if col.get("avg") is not None and col["avg"] < len(r) else None
        if not sym or not qty_n or not avg_n or avg_n <= 0 or qty_n <= 0:
            res["skipped"] += 1
            continue
        iso = None
        if col.get("date") is not None and col["date"] < len(r):
            iso = _parse_csv_date(str(r[col["date"]]))
        cur = next((h for h in parsed if h["symbol"] == sym), None)   # file authoritative — replace
        if cur is None:
            parsed.append({"symbol": sym, "qty": qty_n, "avg": avg_n, "bought": iso})
            res["added"] += 1
        else:
            cur["qty"], cur["avg"] = qty_n, avg_n
            if iso:
                cur["bought"] = iso
        res["rows_found"] += 1
    replace_all(parsed)
    return res


def _parse_csv_date(s: str) -> Optional[str]:
    s = (s or "").strip()[:10]
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except Exception:
            continue
    return None


# --------------------------------------------------------------- snapshot
def snapshot() -> Dict[str, Any]:
    data = _load()
    raw = [dict(h) for h in data.get("holdings", [])]
    return _build(raw)


def _nifty_12m() -> Optional[float]:
    try:
        rows = df.cache_get_stale("px:^NSEI") or df.get_ohlcv("^NSEI")
        closes = [(r.get("date"), r.get("close")) for r in rows if r.get("close")]
        if len(closes) < 252:
            return None
        last, back = closes[-1], closes[-252]
        if back[1]:
            return round((last[1] / back[1] - 1) * 100, 1)
    except Exception:
        pass
    return None


def _build(raw: List[Dict[str, Any]]) -> Dict[str, Any]:
    univ = {u["symbol"]: u for u in df.UNIVERSE}
    rows: List[Dict[str, Any]] = []
    inv_total = cur_total = 0.0
    latest_price_date = None
    for h in raw:
        sym = h["symbol"]
        q = df.quote(sym)
        price = q.get("price")
        invested = round(h["qty"] * h["avg"], 2)
        value = round(h["qty"] * price, 2) if price is not None else None
        pnl = round(value - invested, 2) if value is not None else None
        pnl_pct = round(pnl / invested * 100, 1) if (pnl is not None and invested) else None
        cagr = None
        if h.get("bought") and invested > 0 and value is not None:
            try:
                bdate = datetime.strptime(h["bought"][:10], "%Y-%m-%d").date()
                cbdays = (datetime.now(timezone.utc).date() - bdate).days
                if cbdays >= 90 and value > 0:
                    cagr = round((pow(value / invested, 365.0 / cbdays) - 1) * 100, 1)
            except Exception:
                cagr = None
        u = univ.get(sym) or {}
        pa_h = None
        try:
            _rows = df.get_ohlcv(sym)          # px: cache se — sasta
            if _rows and len(_rows) >= 60:
                pa_h = pa.pa_hint(_rows, price)
        except Exception:
            pa_h = None
        rows.append({
            "symbol": sym, "name": u.get("name") or sym.split(".")[0],
            "sector": u.get("sector") or "–",
            "qty": h["qty"], "avg": h["avg"], "bought": h.get("bought"),
            "price": price, "price_date": q.get("date"),
            "invested": invested, "value": value, "pnl": pnl, "pnl_pct": pnl_pct,
            "cagr_pct": cagr, "chg_1d_pct": q.get("chg_1d_pct"),
            "live": bool(q.get("live")), "price_time": q.get("as_of"),
            "pa": pa_h,
        })
        inv_total += invested
        cur_total += value if value is not None else invested
        if q.get("date"):
            latest_price_date = max(latest_price_date or "", q.get("date") or "")
    # allocation % (value ka share; missing price -> invested price fallback)
    for r in rows:
        base = r["value"] if r["value"] is not None else r["invested"]
        r["alloc_pct"] = round(base / cur_total * 100, 1) if cur_total > 0 else None
    pnl_total = round(cur_total - inv_total, 2) if cur_total else None
    pnl_pct = round(pnl_total / inv_total * 100, 1) if (pnl_total is not None and inv_total) else None
    # sector-wise split
    sec: Dict[str, float] = {}
    for r in rows:
        base = r["value"] if r["value"] is not None else r["invested"]
        sec[r["sector"]] = sec.get(r["sector"], 0) + base
    sectors = [{"sector": k, "value": round(v, 0), "pct": round(v / cur_total * 100, 1) if cur_total else None}
               for k, v in sorted(sec.items(), key=lambda x: -x[1])]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdings": raw,
        "rows": sorted(rows, key=lambda r: -(r["value"] if r["value"] is not None else r["invested"])),
        "totals": {
            "invested": round(inv_total, 2) if inv_total else None,
            "current": round(cur_total, 2) if cur_total else None,
            "pnl": pnl_total, "pnl_pct": pnl_pct,
            "n": len(rows),
            "price_date": latest_price_date,
        },
        "sectors": sectors,
        "nifty_12m_pct": _nifty_12m(),
        "portfolio_path": str(PORT_PATH.name),
        "note": "Prices Yahoo intraday (live, market hours 09:15-15:30 IST) ya EOD close — har row par stamp. Bhoolna mat: apne broker ka official portfolio hi final sach hai.",
    }
