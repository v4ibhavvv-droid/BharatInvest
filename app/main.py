"""BharatInvest FastAPI app — JSON API + static web UI."""
from __future__ import annotations

import re

from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import engine, portfolio as pf, allocation as alloc
from app import datafeed as df
from app import ai as ai_mod

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load(open(ROOT / "config.yaml"))

app = FastAPI(title="BharatInvest", version="1.9.0",
              description="Investment-only research desk for NSE/BSE (long-term, clean & simple)")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat(),
            "universe": len(engine.universe_list())}


@app.get("/api/universe")
def universe():
    return engine.universe_list()


@app.get("/api/market")
def market(refresh: bool = False):
    return engine.market_overview(refresh=refresh)


@app.get("/api/scan")
def scan(refresh: bool = False, limit: int = Query(default=None, ge=1, le=400)):
    try:
        return engine.run_scan(refresh=refresh, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analyze/{symbol}")
def analyze(symbol: str, refresh: bool = False):
    symbol = symbol.strip().upper()
    if not (1 <= len(symbol) <= 20):
        raise HTTPException(status_code=400, detail="Invalid symbol")
    d = engine.analyze(symbol, refresh=refresh)
    q = d.get("quote") or {}
    t = d.get("technical") or {}
    if not (q.get("price") or t.get("available")):
        raise HTTPException(status_code=404,
            detail=f"No data for '{symbol}' — check the NSE symbol (e.g. RELIANCE, HDFCBANK) or BSE code like 500325.BO")
    return d


# ------------------------------------------------------------------ portfolio
@app.get("/api/portfolio")
def portfolio():
    return pf.snapshot()


@app.post("/api/portfolio")
def portfolio_set(payload: dict = None):
    payload = payload or {}
    holdings = payload.get("holdings")
    if not isinstance(holdings, list):
        raise HTTPException(status_code=400, detail="holdings list chahiye thi")
    pf.replace_all(holdings)
    return pf.snapshot()


@app.post("/api/portfolio/import")
def portfolio_import(payload: dict = None):
    payload = payload or {}
    text = payload.get("csv") or ""
    if not isinstance(text, str) or len(text) < 5:
        raise HTTPException(status_code=400, detail="csv text body me bhejo")
    snap = pf.snapshot()
    snap["import_result"] = pf.import_csv(text)
    return snap


@app.post("/api/portfolio/clear")
def portfolio_clear():
    pf.replace_all([])
    return pf.snapshot()


# ------------------------------------------------------------------ allocation guru
@app.get("/api/allocation")
def allocation(budget: float = Query(..., gt=0), count: int = Query(default=6, ge=2, le=12)):
    return alloc.plan(budget=budget, count=count)


# ------------------------------------------------------------------ AI explain layer (Google AI Studio / Gemini)
@app.get("/api/ai/status")
def ai_status():
    return ai_mod.status()


@app.post("/api/ai/key")
def ai_key_save(payload: dict = None):
    payload = payload or {}
    key = str(payload.get("key") or "")
    res = ai_mod.save_key(key)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "Key save fail")
    return res


@app.post("/api/ai/key/clear")
def ai_key_clear():
    return ai_mod.clear_key()


@app.post("/api/ai/explain")
def ai_explain(payload: dict = None):
    payload = payload or {}
    symbol = (str(payload.get("symbol") or "").strip().upper() or None)
    section = str(payload.get("section") or "report")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if symbol and not (1 <= len(symbol) <= 20):
        raise HTTPException(status_code=400, detail="Invalid symbol")
    return ai_mod.explain(section, symbol=symbol, payload=data)


@app.post("/api/ai/ask")
def ai_ask(payload: dict = None):
    payload = payload or {}
    symbol = str(payload.get("symbol") or "").strip().upper()
    question = str(payload.get("question") or "")
    if symbol and not (1 <= len(symbol) <= 20):
        raise HTTPException(status_code=400, detail="Invalid symbol")
    if len(question) < 3:
        raise HTTPException(status_code=400, detail="question chhota hai")
    return ai_mod.ask(symbol, question)


# ------------------------------------------------------------------ NSE announcements (deep-dive async)
@app.get("/api/announcements/{symbol}")
def announcements(symbol: str):
    tkr = symbol.strip().upper()
    tkr = re.sub(r"\.(NS|BO)$", "", tkr)
    if not (1 <= len(tkr) <= 20):
        raise HTTPException(status_code=400, detail="Invalid symbol")
    return df.nse_announcements(tkr)


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
