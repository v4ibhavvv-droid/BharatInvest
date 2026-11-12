"""
BharatInvest data layer.
Sources (free, no API key):
  - Yahoo Finance (yfinance): prices, financials, key ratios  [Rhythmjain12, Investo style]
  - NSE public API: FII/DII flows + promoter pledge           [TradingAgent style]
  - Screener.in page: pledge / auditor cross-check            [screener-ai-tool style]
  - Google News RSS: latest news + naive sentiment            [Artha style]
Everything cached on disk (JSON) with TTLs from config.yaml.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import time
import threading
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import yfinance as yf
import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache_bh"
CACHE_DIR.mkdir(exist_ok=True)

CFG: Dict[str, Any] = yaml.safe_load(open(ROOT / "config.yaml"))
TTL: Dict[str, float] = {k: float(v) * 3600 for k, v in CFG["cache_ttl_hours"].items()}

_CACHE_LOCK = threading.Lock()
_sym_locks: Dict[str, threading.Lock] = {}

# Bound every Yahoo call with a hard timeout — yfinance can stall on
# rate-limited/cold sessions and a hung thread must never poison a scan.
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTimeout
_POOL = ThreadPoolExecutor(max_workers=16)


def _call(fn, *args, timeout: float = 30.0, **kwargs):
    """Run fn in the shared pool with a wall-clock cap (None on timeout)."""
    try:
        return _POOL.submit(fn, *args, **kwargs).result(timeout=timeout)
    except _FTimeout:
        return None
    except Exception:
        return None


def cache_get_stale(key: str) -> Optional[Any]:
    """Read cache ignoring TTL (last-resort fallback)."""
    p = CACHE_DIR / f"{key}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def sym_lock(symbol: str) -> threading.RLock:
    with _CACHE_LOCK:
        if symbol not in _sym_locks:
            _sym_locks[symbol] = threading.RLock()   # reentrant: analyze holds it while inner fetchers re-acquire
        return _sym_locks[symbol]


# ---------------------------------------------------------------- cache utils
def cache_get(key: str, ttl_group: Optional[str] = None, ttl_hours: Optional[float] = None) -> Optional[Any]:
    p = CACHE_DIR / f"{key}.json"
    if not p.exists():
        return None
    ttl = ttl_hours * 3600 if ttl_hours is not None else TTL.get(ttl_group or "", 3600 * 6)
    try:
        if (time.time() - p.stat().st_mtime) <= ttl:
            return json.loads(p.read_text())
    except Exception:
        return None
    return None


def cache_set(key: str, data: Any) -> None:
    p = CACHE_DIR / f"{key}.json"
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=_json_default))
        tmp.replace(p)
    except Exception:
        pass


def _json_default(o: Any):
    if isinstance(o, (datetime,)):
        return o.isoformat()
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return None if math.isnan(float(o)) else float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:
        pass
    return str(o)


def clear_cache():
    for f in CACHE_DIR.glob("*.json"):
        try:
            f.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------- universe
def load_universe() -> List[Dict[str, str]]:
    raw = yaml.safe_load(open(ROOT / "universe.yaml"))
    out: Dict[str, Dict[str, str]] = {}
    sectors = raw.get("sectors") or {}
    if isinstance(sectors, dict):
        for sector, mapping in sectors.items():
            if not isinstance(mapping, dict):
                continue
            for sym, name in mapping.items():
                s = str(sym).upper().strip()
                if not s:
                    continue
                out[s] = {"symbol": s, "name": str(name).strip() or s, "sector": sector}
    for sym in raw.get("extra_focus") or []:
        s = str(sym).upper().strip()
        out.setdefault(s, {"symbol": s, "name": s, "sector": "Focus / Other"})
    lst = list(out.values())
    for e in lst:
        if not re.match(r".*\.(NS|BO)$", e["symbol"]):
            e["symbol"] = e["symbol"] + ".NS"
    return lst


UNIVERSE: List[Dict[str, str]] = load_universe()


# ---------------------------------------------------------------- yfinance fetchers
def _clean_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    if re.fullmatch(r"\d+\.BS", s):
        s = s[:-3] + ".BO"
    if s.startswith("^") or s.startswith("$"):
        return s            # index symbols pass through untouched
    if not s.endswith((".NS", ".BO")):
        s += ".NS"
    return s


def get_ohlcv(symbol: str, refresh: bool = False) -> List[Dict[str, Any]]:
    symbol = _clean_symbol(symbol)
    key = f"px:{symbol}"
    if not refresh:
        c = cache_get(key, "prices")
        if c is not None:
            return c
    with sym_lock(symbol):
        if not refresh:
            c = cache_get(key, "prices")
            if c is not None:
                return c
        t = yf.Ticker(symbol)
        df_hist = _call(lambda: t.history(period="2y", interval="1d", auto_adjust=True,
                                          raise_errors=False), timeout=30)
        rows: List[Dict[str, Any]] = []
        try:
            idx = pd.to_datetime(df_hist.index)
            vals = df_hist.reset_index()
            for i in range(len(df_hist)):
                d = pd.Timestamp(idx[i])
                rows.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "open": _f(vals["Open"].iloc[i]),
                    "high": _f(vals["High"].iloc[i]),
                    "low": _f(vals["Low"].iloc[i]),
                    "close": _f(vals["Close"].iloc[i]),
                    "volume": _i(vals["Volume"].iloc[i]),
                })
        except Exception:
            rows = []
        # Yahoo kabhi-kabhi NaN rows (open/close None, volume present) daal deta hai
        # — aise bars metrics aur quote ko crash karte hain, drop karo
        rows = [r for r in rows if r.get("close") is not None]
        rows = _fill_recent_gaps(symbol, rows)     # NaN/gap wale din official bhavcopy se
        rows = _mc_append_today(symbol, rows)      # market khula/pichhla -> MC live bar
        if rows:
            cache_set(key, rows)
            return rows
        rows = _fallback_ohlcv(symbol)   # Yahoo down -> official bhavcopy -> mtf.trading -> Moneycontrol
        if rows:
            cache_set(key, rows)
            return rows
        return cache_get_stale(key) or []


def _f(x) -> Optional[float]:
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except Exception:
        return None


def _i(x) -> Optional[int]:
    try:
        v = int(x)
        return v
    except Exception:
        return None


def get_info(symbol: str, refresh: bool = False) -> Dict[str, Any]:
    symbol = _clean_symbol(symbol)
    key = f"info:{symbol}"
    if not refresh:
        c = cache_get(key, "fundamentals")
        if c is not None:
            return c
    with sym_lock(symbol):
        if not refresh:
            c = cache_get(key, "fundamentals")
            if c is not None:
                return c
        data: Dict[str, Any] = {}
        data = _call(lambda: (yf.Ticker(symbol).info or {}), timeout=30)
        if not data:
            data = cache_get_stale(key) or {}
        if data:
            cache_set(key, data)
        return data


def get_financials(symbol: str, refresh: bool = False) -> Dict[str, Any]:
    """Annual statements + quarterly P&L, reduced to python-friendly dict."""
    symbol = _clean_symbol(symbol)
    key = f"fin:{symbol}"
    if not refresh:
        c = cache_get(key, "fundamentals")
        if c is not None:
            return c
    with sym_lock(symbol):
        if not refresh:
            c = cache_get(key, "fundamentals")
            if c is not None:
                return c
        out: Dict[str, Any] = {"annual": {}, "quarterly": {}}

        def _grab():
            t = yf.Ticker(symbol)
            acc: Dict[str, Any] = {"annual": {}, "quarterly": {}}
            for name, frame in (
                ("income", t.income_stmt),
                ("balance", t.balance_sheet),
                ("cashflow", t.cashflow),
            ):
                if frame is None or frame.empty:
                    continue
                cols = [str(c.date()) if hasattr(c, "date") else str(c) for c in frame.columns]
                rows: Dict[str, Dict[str, Optional[float]]] = {}
                for r in frame.index:
                    vals: Dict[str, Optional[float]] = {}
                    for j, c in enumerate(frame.columns):
                        vals[cols[j]] = _f(frame.loc[r, c])
                    rows[str(r)] = vals
                acc["annual"][name] = rows
            q = t.quarterly_income_stmt
            if q is not None and not q.empty:
                qcols = [str(c.date()) if hasattr(c, "date") else str(c) for c in q.columns]
                qrows: Dict[str, Dict[str, Optional[float]]] = {}
                for r in q.index:
                    vals: Dict[str, Optional[float]] = {}
                    for j, c in enumerate(q.columns):
                        vals[qcols[j]] = _f(q.loc[r, c])
                    qrows[str(r)] = vals
                acc["quarterly"] = qrows
            return acc

        got = _call(_grab, timeout=40)
        if got and got["annual"]:
            out = got
        else:
            out = cache_get_stale(key) or {"annual": {}, "quarterly": {}}
        if out["annual"]:
            cache_set(key, out)
        return out


# ---------------------------------------------------------------- intraday live LTP (Yahoo 1m, market hours)
_LQ_TTL_OK = 60.0     # seconds — hit cache (Yahoo burst guard)
_LQ_TTL_MISS = 10.0   # seconds — fail bhi thodi der yaad rakho (hammer mat karo)
_LQ: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
_LQ_LOCK = threading.Lock()


def _market_open_now() -> bool:
    """NSE market hours (IST): Mon–Fri 09:15–15:30. Holiday check heavy hai —
    live bar hi nahi milega to fallback EOD, isliye simple gate kaafi hai."""
    now = datetime.now(_IST)
    hm = now.strftime("%H:%M")
    return now.weekday() < 5 and "09:15" <= hm <= "15:30"


def _intraday_live(symbol: str) -> Optional[Dict[str, Any]]:
    """Market hours me Yahoo 1m series ki last bar -> live LTP (~15 min delay,
    free, koi token nahi). TTL cache; fail -> None (caller EOD fallback)."""
    now_ts = time.time()
    with _LQ_LOCK:
        hit = _LQ.get(symbol)
        if hit is not None:
            ts, data = hit
            if data is not None and (now_ts - ts) < _LQ_TTL_OK:
                return data
            if data is None and (now_ts - ts) < _LQ_TTL_MISS:
                return None
    out: Optional[Dict[str, Any]] = None
    try:
        h = yf.Ticker(symbol).history(period="1d", interval="1m",
                                      auto_adjust=False, raise_errors=False)
        c = h["Close"].dropna() if h is not None and not h.empty else None
        if c is not None and len(c) > 0:
            ltp = float(c.iloc[-1])
            if ltp and ltp == ltp:          # NaN guard
                o = h["Open"].dropna()
                hi = h["High"].dropna()
                lo = h["Low"].dropna()
                as_of_ts = c.index[-1]
                try:
                    as_of = as_of_ts.tz_convert(_IST).strftime("%H:%M")
                except Exception:
                    as_of = as_of_ts.strftime("%H:%M")
                out = {"ltp": round(ltp, 2), "as_of": as_of,
                       "open": round(float(o.iloc[0]), 2) if len(o) else None,
                       "high": round(float(hi.max()), 2) if len(hi) else None,
                       "low": round(float(lo.min()), 2) if len(lo) else None}
    except Exception:
        out = None
    with _LQ_LOCK:
        _LQ[symbol] = (now_ts, out)
    return out


def quote(symbol: str, refresh: bool = False) -> Dict[str, Any]:
    """Quote: market hours me Yahoo intraday se LIVE LTP (~15 min delay),
    warna EOD close. chg_1d hamesha pichhle COMPLETE din ke close se."""
    symbol = _clean_symbol(symbol)
    rows = get_ohlcv(symbol, refresh=refresh)
    out: Dict[str, Any] = {"symbol": symbol, "price": None, "prev_close": None, "chg_1d_pct": None,
                           "date": None, "prev_date": None, "live": False, "as_of": None}
    if rows:
        out["date"] = rows[-1]["date"]
    if len(rows) >= 2:
        last, prev = rows[-1], rows[-2]
        out["price"] = last["close"]
        out["prev_close"] = prev["close"]
        out["prev_date"] = prev["date"]
        if last["close"] is not None and prev["close"]:
            out["chg_1d_pct"] = round((last["close"] / prev["close"] - 1) * 100, 2)
        out["day_high"] = last["high"]
        out["day_low"] = last["low"]
        # market khula -> live overlay (EOD rows/px-cache ko touch nahi karte).
        # Live chg ka baseline = pichhla COMPLETE din (aaj ke bar se pehle wala).
        if _market_open_now():
            eod_today = rows[-1]["date"] == _ist_today_str()
            base = rows[-2] if eod_today else rows[-1]
            li = _intraday_live(symbol)
            if li and li.get("ltp") and base["close"]:
                out["price"] = li["ltp"]
                out["date"] = _ist_today_str()
                out["prev_close"] = base["close"]
                out["prev_date"] = base["date"]
                out["chg_1d_pct"] = round((li["ltp"] / base["close"] - 1) * 100, 2)
                if li.get("high"):
                    out["day_high"] = max(li["high"], li["ltp"])
                if li.get("low"):
                    out["day_low"] = min(li["low"], li["ltp"])
                out["open"] = li.get("open")
                out["live"] = True
                out["as_of"] = li.get("as_of")
    return out


# ---------------------------------------------------------------- price backup chain (Yahoo down ho to)
# B1. Official exchange bhavcopy (archives.nseindia.com / bseindia.com UDiFF
#     files) — exchange khud publish karta hai, koi bot-wall nahi.
# B2. mtf.trading — wahi files ka static mirror (rare case: archive CDN down).
# B3. Moneycontrol priceapi — market khula to aaj ka live LTP bar append.
# Har date ki bhavcopy immutable hai -> .cache_bh/bcopy:<exch>:<ymd>.json me
# ek baar aa gayi (sab symbols ke liye reuse), kabhi expire nahi hoti.

_IST = timezone(timedelta(hours=5, minutes=30))
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
_BCOPY_POOL = ThreadPoolExecutor(max_workers=6)
_BCOPY_MAX_SESSIONS = 300    # ~14 mahine — RSI, vol, 12m return, 52w high sab cover
_MTF_MAX_SESSIONS = 200
_MC_SEED = {"RELIANCE": "RI", "ONGC": "ONG"}   # legacy DISPID jo verify hue
_MC_MAP_LOCK = threading.RLock()


def _ist_today_str() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


def ist_today() -> str:
    """Public IST date (YYYY-MM-DD) — cache keys / as-of stamps ke liye."""
    return _ist_today_str()


def _sym_specs(cleaned: str) -> Tuple[Optional[str], Optional[str]]:
    s = cleaned.upper()
    if s.startswith("^") or s.startswith("$"):
        return None, None        # indices sirf Yahoo se — backup chain unko jhotha nahi kare
    if s.endswith(".BO"):
        return s[:-3], "bse"
    if s.endswith(".NS"):
        return s[:-3], "nse"
    return s, "nse"


def _chunks(lst: List[Any], n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _ts_to_rows(collected: List[Tuple[str, List[float]]]) -> List[Dict[str, Any]]:
    out_rows = []
    for ymd, bar in sorted(collected):
        out_rows.append({
            "date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}",
            "open": bar[0], "high": bar[1], "low": bar[2], "close": bar[3],
            "volume": int(bar[4]) if bar[4] else None,
        })
    return out_rows


def _sessions_back(max_sessions: int, max_cal: int) -> List[str]:
    """Aaj (IST) se piche ki Saturday-chhodi trading-ish dates, newest first."""
    d = datetime.now(_IST).date()
    out: List[str] = []
    for i in range(max_cal):
        day = d - timedelta(days=i)
        if day.weekday() >= 5:
            continue
        out.append(day.strftime("%Y%m%d"))
        if len(out) >= max_sessions:
            break
    return out


def _bcopy_url(exch: str, ymd: str) -> str:
    if exch == "nse":
        return f"https://archives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
    return f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"


def _storage_store(path: Path, rows: Dict[str, List[float]]) -> None:
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as fh:
            json.dump(rows, fh, separators=(",", ":"))
        tmp.rename(path)
    except Exception:
        pass


_BC_COPY_MISS_TTL = 600.0   # seconds — bhavcopy file na mile to itni der tak dobara mat try
_BCOPY_MISS: Dict[str, float] = {}


def _bcopy_date_rows(exch: str, ymd: str, ticker: Optional[str] = None) -> Dict[str, List[float]]:
    """Poore din ki bhavcopy {SYMBOL: [o,h,l,c,v]} — ek hi download sab symbols
    ke liye. NSE file chhoti (~200KB zip) hoti hai → poori cache; BSE ~900KB hoti
    hai → sirf requested ticker cache me rakhte hain."""
    tag = "" if exch == "nse" else f":{(ticker or '').upper()}"
    miss_key = f"{exch}:{ymd}{tag}"
    hit = _BCOPY_MISS.get(miss_key)
    if hit and (time.time() - hit) < _BC_COPY_MISS_TTL:
        return {}
    path = CACHE_DIR / f"bcopy:{exch}:{ymd}{tag}.json"
    if path.exists():
        try:
            return json.load(open(path))
        except Exception:
            pass
    rows: Dict[str, List[float]] = {}
    try:
        r = requests.get(_bcopy_url(exch, ymd), headers=_UA, timeout=25)
        if r.status_code == 200 and r.content:
            data = r.content
            if exch == "nse":
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    data = z.read(z.namelist()[0])
            utf8 = data.decode("utf-8", "replace")
            for rec in csv.DictReader(io.StringIO(utf8)):
                if rec.get("FinInstrmTp") != "STK":
                    continue
                sym = (rec.get("TckrSymb") or "").strip().upper()
                if not sym:
                    continue
                if exch != "nse" and ticker:
                    # BSE file me numeric scrip code "500325" ki jagah
                    # TckrSymb "RELIANCE" hota hai, FinInstrmId me code
                    if ticker.isdigit():
                        if (rec.get("FinInstrmId") or "").strip() != ticker:
                            continue
                        sym = ticker
                    elif sym != ticker.upper():
                        continue
                d = _store_bar(rec)
                if d:
                    rows[sym] = d
    except Exception:
        return rows
    if rows:
        _storage_store(path, rows)
    else:
        _BCOPY_MISS[miss_key] = time.time()
    return rows


def _fill_recent_gaps(cleaned: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Yahoo ke NaN/glitch rows ke baad missing recent trading days official
    bhavcopy se bhar do (max ~6 din). Weekend/holiday: kuch nahi milta —
    fake bar nahi banta."""
    ticker, exch = _sym_specs(cleaned)
    if not ticker or not rows:
        return rows
    last_date = rows[-1]["date"] or ""
    out = list(rows)
    for ymd in reversed(_sessions_back(6, 10)):   # oldest-first → ascending append
        ds = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        if ds <= last_date:
            continue
        bar = _bcopy_date_rows(exch, ymd, ticker).get(ticker)
        if bar:
            out.append({"date": ds, "open": bar[0], "high": bar[1], "low": bar[2],
                        "close": bar[3], "volume": int(bar[4]) if bar[4] else None})
            last_date = ds
    return out


def _mc_append_today(cleaned: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Market khula/pichhla ho to MC live LTP bar append (sirf 09:15 IST ke baad,
    aur sirf jab aaj ki bhavcopy file abhi nahi aayi — warna fake bar banega)."""
    ticker, exch = _sym_specs(cleaned)
    if not ticker:
        return rows
    today = _ist_today_str()
    if rows and rows[-1]["date"] == today:
        return rows
    now = datetime.now(_IST)
    if now.weekday() >= 5 or now.strftime("%H:%M") < "09:15":
        return rows
    if _bcopy_date_rows(exch, now.strftime("%Y%m%d"), ticker):
        return rows     # bhavcopy aa chuki — MC ki zarurat nahi
    bar = _mc_today_bar(cleaned)
    if bar and bar["date"] == today and bar.get("close"):
        return rows + [bar]
    return rows


def _store_bar(rec: Dict[str, str]) -> Optional[List[float]]:
    o = _f(rec.get("OpnPric")); h = _f(rec.get("HghPric"))
    l = _f(rec.get("LwPric")); c = _f(rec.get("ClsPric"))
    v = _f(rec.get("TtlTradgVol"))
    if c is None or c <= 0:
        return None
    return [o if o is not None else c, h if h is not None else c,
            l if l is not None else c, c, v or 0.0]


def _bcopy_history(cleaned: str) -> List[Dict[str, Any]]:
    ticker, exch = _sym_specs(cleaned)
    if not ticker:
        return []
    collected: List[Tuple[str, List[float]]] = []
    empty_streak = 0
    for chunk in _chunks(_sessions_back(_BCOPY_MAX_SESSIONS + 8, 476), 12):
        got = list(_BCOPY_POOL.map(lambda y: _bcopy_date_rows(exch, y, ticker), chunk))
        for ymd, all_rows in zip(chunk, got):
            bar = all_rows.get(ticker)
            if bar:
                collected.append((ymd, bar))
                empty_streak = 0
            elif all_rows:
                empty_streak += 1        # file mili par ye symbol nahi — listing baad me hui
        if len(collected) >= _BCOPY_MAX_SESSIONS:
            break
        if empty_streak > 30 and len(collected) >= 40:
            break
    return _ts_to_rows(collected)


def _mtf_date(ticker: str, exch: str, ymd: str) -> Optional[List[float]]:
    try:
        r = requests.get("https://mtf.trading/api/v1/bhavcopy",
                         params={"date": ymd, "exchange": exch, "symbol": ticker},
                         headers=_UA, timeout=15)
        got = ((r.json() or {}).get("rows") or [None])[0]
        if not got:
            return None
        return _store_bar(got)
    except Exception:
        return None


def _mtf_history(cleaned: str) -> List[Dict[str, Any]]:
    ticker, exch = _sym_specs(cleaned)
    if not ticker:
        return []
    if exch == "bse" and ticker.isdigit():
        res = resolve_numeric_bse(cleaned)
        ticker = (res or {}).get("nse_symbol") or ticker
    collected: List[Tuple[str, List[float]]] = []
    empty_streak = 0
    for chunk in _chunks(_sessions_back(_MTF_MAX_SESSIONS + 6, 340), 10):
        got = list(_BCOPY_POOL.map(lambda y: _mtf_date(ticker, exch, y), chunk))
        for ymd, bar in zip(chunk, got):
            if bar:
                collected.append((ymd, bar))
                empty_streak = 0
            else:
                empty_streak += 1
        if len(collected) >= _MTF_MAX_SESSIONS or (empty_streak > 30 and len(collected) >= 40):
            break
    return _ts_to_rows(collected)


def _universe_name(ticker: str) -> Optional[str]:
    for u in UNIVERSE:
        if u["symbol"].split(".")[0].upper() == ticker.upper():
            return u["name"]
    return None


def _mc_dispid(ticker: str) -> Optional[str]:
    """Moneycontrol legacy SCRIP code dhundo (RI/UT...). Seed map + autosuggest,
    mila to .cache_bh/mcmap.json me yaad rakhte hain."""
    path = CACHE_DIR / "mcmap.json"
    with _MC_MAP_LOCK:
        try:
            m = json.load(open(path)) if path.exists() else {}
        except Exception:
            m = {}
        m.setdefault("RELIANCE", "RI"); m.setdefault("ONGC", "ONG")
        if ticker in m:
            return str(m[ticker])
        found: Optional[str] = None
        for query, allow_first in [(ticker.lower(), False), (_universe_name(ticker) or "", True)]:
            if not query:
                continue
            try:
                r = requests.get("https://www.moneycontrol.com/mccode/common/autosuggestion_solr.php",
                                 params={"classic": "true", "query": query,
                                         "type": 1, "format": "json"},
                                 headers=_UA, timeout=12)
                rows = r.json() if r.status_code == 200 else []
            except Exception:
                rows = []
            for row in rows:
                nm = str(row.get("pdt_dis_nm") or "").replace("&nbsp;", " ")
                if re.search(rf"(?i)\b{re.escape(ticker)}\b", nm):
                    found = row.get("sc_id")
                    break
            if found is None and allow_first and rows:
                found = rows[0].get("sc_id")   # sirf company-name query ka 'first row' guess
            if found:
                break
        if found:
            m[ticker] = str(found)
            try:
                json.dump(m, open(path, "w"))
            except Exception:
                pass
            return str(found)
        return None


def _mc_today_bar(cleaned: str) -> Optional[Dict[str, Any]]:
    """Aaj ka live LTP bar (MC priceapi) — market khula to backup history bhi fresh."""
    ticker, exch = _sym_specs(cleaned)
    if not ticker:
        return None
    if exch == "bse" and ticker.isdigit():
        res = resolve_numeric_bse(cleaned)
        ticker = (res or {}).get("nse_symbol") or ticker
    disp = _mc_dispid(ticker)
    if not disp:
        return None
    try:
        r = requests.get(f"https://priceapi.moneycontrol.com/pricefeed/{exch}/equitycash/{disp}",
                         headers=_UA, timeout=12)
        data = (r.json() or {}).get("data") or {}
    except Exception:
        return None
    price = _f(data.get("HP") or data.get("pricecurrent"))
    if price is None or price <= 0:
        return None
    o = _f(data.get("OPN")) or price
    return {"date": _ist_today_str(), "open": o, "high": max(o, price),
            "low": min(o, price), "close": price, "volume": None}


def _fallback_ohlcv(cleaned: str) -> List[Dict[str, Any]]:
    """Yahoo fail -> official bhavcopy -> mtf.trading -> (MC live bar).
    BSE files bade hain (~900KB/date) — wahan ulta order: pehle mtf (chhota)."""
    _ticker, exch = _sym_specs(cleaned)
    if exch == "bse":
        rows = _mtf_history(cleaned)
        src = "mtf.trading"
        if len(rows) < 60:
            rows = _bcopy_history(cleaned)
            src = "bhavcopy-official"
    else:
        rows = _bcopy_history(cleaned)
        src = "bhavcopy-official"
        if len(rows) < 60:
            mrows = _mtf_history(cleaned)
            if len(mrows) > len(rows):
                rows, src = mrows, "mtf.trading"
    if not rows:
        return []
    rows = _fill_recent_gaps(cleaned, rows)
    before = len(rows)
    rows = _mc_append_today(cleaned, rows)
    if len(rows) > before:
        src += "+mc-live"
    print(f"[datafeed] backup used: {cleaned} <- {src} ({len(rows)} bars)")
    return rows


# ---------------------------------------------------------------- market / indices
_INDEXES = {
    "NIFTY 50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANK NIFTY": "^NSEBANK",
    "INDIA VIX": "^INDIAVIX",
}


def market_overview(refresh: bool = False) -> Dict[str, Any]:
    key = "market:overview"
    if not refresh:
        c = cache_get(key, "market")
        if c is not None:
            return c
    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "indices": {},
        "fii_dii": {"available": False},
        "nifty_60d": [],
        "breadth": {"available": False},
    }
    for label, sym in _INDEXES.items():
        try:
            h = _call(lambda: yf.Ticker(sym).history(period="1mo", interval="1d",
                                                    auto_adjust=False, raise_errors=False),
                      timeout=25)
            if h is None or h.empty:
                continue
            closes = h["Close"].dropna()
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2]) if len(closes) > 1 else None
            chg = round((last / prev - 1) * 100, 2) if prev else None
            out["indices"][label] = {"value": round(last, 2), "chg_pct": chg,
                                     "date": str(closes.index[-1].date())}
            if label == "NIFTY 50":
                out["nifty_60d"] = [{"d": str(d.date()), "c": round(float(v), 2)}
                                    for d, v in list(closes.items())[-60:]]
        except Exception:
            continue
    out["fii_dii"] = fetch_fii_dii()
    if out["indices"]:
        cache_set(key, out)
    return out


def fetch_fii_dii() -> Dict[str, Any]:
    """Best-effort NSE FII/DII daily cash flows. Graceful fail -> available: False."""
    cached = cache_get(f"fiidii:{_ist_today_str()}", ttl_hours=3)
    if cached:
        return cached
    u = "https://www.nseindia.com/api/fiiDiiTradeReact"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.nseindia.com/reports/fii-dii",
    }
    try:
        s = requests.Session()
        try:
            s.get("https://www.nseindia.com", headers=headers, timeout=6)
        except Exception:
            pass
        r = s.get(u, headers=headers, timeout=8)
        j = r.json() if r.ok else {}
        out: Dict[str, Any] = {"available": False}
        data = j if isinstance(j, list) else j.get("data", [])
        rows = []
        for row in (data or [])[:30]:
            try:
                date = row.get("date") or row.get("snx_date") or ""
                cat = (row.get("category") or row.get("categoryName") or "").strip().upper()
                buy = row.get("buyValue") or row.get("buy") or None
                sell = row.get("sellValue") or row.get("sell") or None
                if buy is None or sell is None:
                    continue
                if "FII" in cat or "FPI" in cat:
                    who = "FII/FPI"
                elif "DII" in cat:
                    who = "DII"
                else:
                    continue
                rows.append({
                    "date": date,
                    "who": who,
                    "buy_cr": round(float(buy) / 10, 0),      # scale differences tolerated
                    "sell_cr": round(float(sell) / 10, 0),
                    "net_cr": round((float(buy) - float(sell)) / 10, 0),
                })
            except Exception:
                continue
        # collapse duplicates (multiple entries per date/category)
        agg: Dict[str, Dict[str, float]] = {}
        for r2 in rows:
            kk = r2["date"] + "|" + r2["who"]
            ag = agg.setdefault(kk, {"date": r2["date"], "who": r2["who"], "buy_cr": 0.0, "sell_cr": 0.0, "net_cr": 0.0})
            ag["buy_cr"] += r2["buy_cr"]
            ag["sell_cr"] += r2["sell_cr"]
            ag["net_cr"] += r2["net_cr"]
        final = sorted(agg.values(), key=lambda x: x["date"], reverse=True)[:10]
        if final:
            out = {"available": True, "rows": final}
            cache_set(f"fiidii:{_ist_today_str()}", out)
        return out
    except Exception:
        return {"available": False}


# ---------------------------------------------------------------- numeric-BSE resolve
def resolve_numeric_bse(symbol: str) -> Optional[Dict[str, Any]]:
    """Yahoo par 5-6 digit BSE codes (500325.BO) nahi chalte — company symbol
    (BOSCHLTD.NS) chalta hai. Screener.in page se NSE symbol nikaal ke map karo."""
    m = re.fullmatch(r"(\d{5,6})\.(?:BO|BS|NS)", symbol.upper())
    if not m:
        return None
    code = m.group(1)
    key = f"bseres:{code}"
    cached = cache_get_stale(key)
    if cached:
        return cached
    try:
        r = requests.get(f"https://www.screener.in/company/{code}/",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if not r.ok:
            return None
        m2 = re.search(r"NSE[:\s]+([A-Z0-9&\-]+)", r.text)
        if not m2:
            return None
        out = {"bse_code": code, "nse_symbol": m2.group(1), "yahoo_symbol": m2.group(1) + ".NS"}
        cache_set(key, out)
        return out
    except Exception:
        return None


# ---------------------------------------------------------------- news (Google News RSS)
_POS = ["profit", "growth", "beats", "record", "surge", "dividend", "upgrade", "strong",
        "gain", "rises", "higher", "expansion", "order win", "bonus", "buyback"]
_NEG = ["fraud", "pledge", "downgrade", "loss", "probe", "penalty", "falls", "drop",
        "weaker", "default", "raid", "resign", "recall", "slump", "crash", "cut", "decline",
        "stress", "closure", "layoff"]


def _sentiment(title: str) -> str:
    t = title.lower()
    p = sum(1 for w in _POS if w in t)
    n = sum(1 for w in _NEG if w in t)
    if p > n:
        return "positive"
    if n > p:
        return "negative"
    return "neutral"


def news_for(symbol: str, company_name: str, refresh: bool = False) -> List[Dict[str, str]]:
    symbol = _clean_symbol(symbol)
    key = f"news:{symbol}"
    if not refresh:
        c = cache_get(key, "news")
        if c is not None:
            return c
    base = (company_name or "").split("(")[0].strip() or symbol
    tag = re.sub(r"\.NS$|\.BO$", "", symbol)
    q = f"{base} OR {tag} stock"
    url = ("https://news.google.com/rss/search?q=" + requests.utils.quote(q)
           + "&hl=en-IN&gl=IN&ceid=IN:en")
    items: List[Dict[str, str]] = []
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.ok:
            root = ET.fromstring(r.content)
            for node in root.iter("item"):
                title = (node.findtext("title") or "").strip()
                link = (node.findtext("link") or "").strip()
                pub = (node.findtext("pubDate") or "").strip()
                src = node.find("source")
                source = (src.text or "").strip() if src is not None and src.text else ""
                if title:
                    items.append({"title": title, "link": link, "date": pub,
                                  "source": source, "sentiment": _sentiment(title)})
    except Exception:
        pass
    items = items[:10]
    if items:
        cache_set(key, items)
    return items


# ---------------------------------------------------------------- pledge (Screener.in best-effort)
def _pledge_from_html(html_text: str) -> Dict[str, Any]:
    m = re.search(r"Pledged percentage[^<]*<[^>]*>\s*([0-9]+(?:\.[0-9]+)?)\s*%", html_text)
    if not m:
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*pledged", html_text, re.IGNORECASE)
    if m:
        try:
            return {"available": True, "pledged_pct": float(m.group(1))}
        except Exception:
            return {"available": False}
    return {"available": False}


_SCREENER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def pledge_for(symbol: str, refresh: bool = False) -> Dict[str, Any]:
    """Try to read promoter pledge % from Screener.in company page. Fail -> unknown."""
    symbol = _clean_symbol(symbol)
    tkr = re.sub(r"\.NS$", "", symbol)
    key = f"pledge:{tkr}"
    if not refresh:
        c = cache_get(key, "fundamentals")
        if c is not None:
            return c
    try:
        u = f"https://www.screener.in/company/{tkr}/consolidated/"
        r = requests.get(u, headers={"User-Agent": _SCREENER_UA},
                         timeout=10)
        if not r.ok:
            u = f"https://www.screener.in/company/{tkr}/"
            r = requests.get(u, headers={"User-Agent": _SCREENER_UA}, timeout=10)
        if r.ok:
            out = _pledge_from_html(r.text)
            if out.get("available"):
                cache_set(key, out)
            return out
    except Exception:
        pass
    return {"available": False}


# ---------------------------------------------------------------- NSE session (shared)
def _nse_session() -> "requests.Session":
    """NSE public JSON needs browser-ish headers + a warm homepage cookie."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
    })
    try:
        s.get("https://www.nseindia.com", timeout=6)
    except Exception:
        pass
    return s


def _pf(x) -> Optional[float]:
    try:
        return round(float(str(x).replace(",", "").strip()), 4)
    except Exception:
        return None


# ---------------------------------------------------------------- Nifty valuation gauge
def nifty_valuation(refresh: bool = False) -> Dict[str, Any]:
    """NIFTY 50 P/E–P/B–div yield (NSE running-day stats). Apni local daily-PE
    history bhi jama karta hai (.cache_bh me) — ~60 market days baad usse real
    median niklega. Jab tak history chhota hai: widely-used rule-of-thumb bands
    (<20 cheap / 20-26 fair / >26 expensive), clearly labelled."""
    today = _ist_today_str()
    key = f"niftyval:{today}"
    if not refresh:
        c = cache_get(key, "market")
        if c is not None:
            return c
    out: Dict[str, Any] = {"available": False}
    try:
        s = _nse_session()
        r = s.get("https://www.nseindia.com/api/AllIndices", timeout=12)
        j = r.json() if r.ok else {}
        row = next((d for d in (j.get("data") or []) if d.get("index") == "NIFTY 50"), None)
        if row and row.get("pe"):
            out = {
                "available": True, "index": "NIFTY 50",
                "pe": _pf(row.get("pe")), "pb": _pf(row.get("pb")), "dy": _pf(row.get("dy")),
                "last": _pf(row.get("last")), "prev_close": _pf(row.get("previousClose")),
                "chg_pct": _pf(row.get("percentChange")),
                "year_high": _pf(row.get("yearHigh")), "year_low": _pf(row.get("yearLow")),
                "as_of": today,          # NSE running-day stats (refresh through the session)
            }
            # — local PE history: no API pull, no our own daily sample —
            hist = cache_get_stale("niftype:hist") or {}
            try:
                hist[today] = float(out["pe"])
                hist = dict(sorted(hist.items())[-400:])
                cache_set("niftype:hist", hist)
            except Exception:
                pass
            vals = sorted(v for v in hist.values() if v)
            if len(vals) >= 60:
                import statistics as _st
                med = _st.median(vals)
                out["history_median"] = round(med, 2)
                out["history_days"] = len(vals)
                out["basis"] = f"vs own {len(vals)}-day median {med:.1f}"
                if out["pe"] is not None:
                    ratio = out["pe"] / med
                    out["verdict"] = "CHEAP" if ratio <= 0.85 else "FAIR" if ratio <= 1.12 else "EXPENSIVE"
            else:
                out["history_days"] = len(vals)
                out["basis"] = "rule of thumb (<20 cheap · 20-26 fair · >26 expensive); live history ban rahi hai"
                if out["pe"] is not None:
                    p = out["pe"]
                    out["verdict"] = "CHEAP" if p < 20 else "FAIR" if p <= 26 else "EXPENSIVE"
            cache_set(key, out)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- NSE corporate announcements
def nse_announcements(tkr: str, refresh: bool = False) -> Dict[str, Any]:
    """Official NSE corporate announcements ek company ke liye (deep-dive only,
    background se load hota hai — scan ke time nahi)."""
    tkr = (tkr or "").strip().upper()
    key = f"ann:{tkr}"
    if not refresh:
        c = cache_get(key, ttl_hours=2)
        if c is not None:
            return c
    out: Dict[str, Any] = {"available": False, "symbol": tkr}
    try:
        s = _nse_session()
        r = s.get(f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={tkr}",
                  timeout=18)
        j = r.json() if r.ok else {}
        data = (j.get("data") if isinstance(j, dict) else j) if isinstance(j, (dict, list)) else []
        items: List[Dict[str, Any]] = []
        for x in data or []:
            if str(x.get("symbol") or "").strip().upper() != tkr:
                continue
            items.append({
                "date": str(x.get("an_dt") or "").strip(),
                "title": str(x.get("attchmntText") or "").strip(),
                "category": str(x.get("newssub") or "").strip(),
                "pdf": x.get("attchmntFile") or None,
            })
            if len(items) >= 8:
                break
        if items:
            out = {"available": True, "symbol": tkr, "items": items}
            cache_set(key, out)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- Screener.in full page (10-yr statements + pros/cons)
def _html_txt(x: str) -> str:
    x = re.sub(r"<[^>]+>", " ", x or "")
    x = x.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    return re.sub(r"\s+", " ", x).strip()


def _html_num(x: str) -> Optional[float]:
    t = _html_txt(x or "").replace(",", "").replace("%", "").strip()
    if not t or not re.search(r"\d", t):
        return None
    try:
        return float(t)
    except Exception:
        return None


def _parse_screener_section(html_text: str, sid: str) -> Optional[Dict[str, Any]]:
    sm = re.search(rf'<section id="{sid}".*?</section>', html_text, re.S)
    if not sm:
        return None
    tm = re.search(r"<table.*?</table>", sm.group(0), re.S)
    if not tm:
        return None
    tbl = tm.group(0)
    years: List[str] = [y for y in (_html_txt(h) for h in
                        re.findall(r"<th[^>]*>(.*?)</th>", tbl, re.S)) if y]
    rows: Dict[str, List[Optional[float]]] = {}
    for trm in re.finditer(r"<tr[^>]*>(.*?)</tr>", tbl, re.S):
        cells = [c for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", trm.group(1), re.S)]
        if len(cells) < 2:
            continue
        label = _html_txt(cells[0])
        if not label or label in years:
            continue
        vals = [_html_num(c) for c in cells[1:]]
        if all(v is None for v in vals):
            continue
        rows[label] = vals
    if not years or not rows:
        return None
    return {"years": years[:10], "rows": {k: v[:10] for k, v in rows.items()}}


def screener_scrape(symbol: str, refresh: bool = False) -> Dict[str, Any]:
    """Screener.in consolidated page: pledge + 10-year statements
    (P&L / Balance Sheet / Cash Flow / Ratios / Quarters) + Pros & Cons.
    [openscreener / screenercli ideas — but zero dependency, stdlib parsing]"""
    syn = _clean_symbol(symbol)
    tkr = re.sub(r"\.(NS|BO)$", "", syn)
    key = f"scrfull:{tkr}"
    if not refresh:
        c = cache_get(key, "fundamentals")
        if c is not None:
            return c
    html_text = None
    consolidated = True
    for u in (f"https://www.screener.in/company/{tkr}/consolidated/",
              f"https://www.screener.in/company/{tkr}/"):
        try:
            r = requests.get(u, headers={"User-Agent": _SCREENER_UA}, timeout=12)
            if r.ok and "profit-loss" in r.text:
                html_text = r.text
                consolidated = u.endswith("/consolidated/")
                break
        except Exception:
            continue
    if not html_text:
        return {"available": False, "symbol": tkr}
    statements: Dict[str, Any] = {}
    for sid, nm in (("profit-loss", "profit_loss"), ("balance-sheet", "balance_sheet"),
                    ("cash-flow", "cash_flow"), ("ratios", "ratios"), ("quarters", "quarterly")):
        sec = _parse_screener_section(html_text, sid)
        if sec:
            statements[nm] = sec
    pros = cons = []
    m = re.search(r'<div class="pros".*?<ul>(.*?)</ul>', html_text, re.S)
    if m:
        pros = [_html_txt(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.S)]
    m = re.search(r'<div class="cons".*?<ul>(.*?)</ul>', html_text, re.S)
    if m:
        cons = [_html_txt(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.S)]
    out: Dict[str, Any] = {
        "available": True, "symbol": tkr, "consolidated": consolidated,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "pledge": _pledge_from_html(html_text),
        "statements": statements,
        "proscons": {"pros": pros, "cons": cons},
    }
    cache_set(key, out)
    return out
