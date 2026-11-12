"""
BharatInvest AI layer — Google AI Studio (Gemini) integration.

Ek hi maqsad: page par JO DIKHE use beginner asaan bhasha me samjhana
(ROE, DCF, RSI, margin of safety... sab terms glossary ke saath).

Design (professional + safe):
- API key: `.ai_key` file (local, gitignored) YA env GOOGLE_AI_API_KEY /
  GEMINI_API_KEY YA config.yaml `ai.api_key`. Key UI se save hoti hai,
  kabhi client ko wapas nahi jaati (sirf masked hint).
- Google Gemini REST — `requests` se, ZERO new dependency.
- Disk cache: `.cache_bh/ai:<hash>.json` — same content -> same explanation
  (quota bachao; volatile fields hash se bahar).
- NEVER raises: har error friendly Hinglish message me; key na ho to
  UI khud buttons hide karti hai (status endpoint).
- Sirf EXPLANATION layer hai — score/verdict override nahi karta, koi
  advice nahi (educational only, jaise pura app).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / ".ai_key"
CACHE_DIR = ROOT / ".cache_bh"

# Chhoti volatile fields — cache-hash me nahi jaani chahiye (roz badalte hain)
_VOLATILE = {"generated_at", "cached", "stale", "live", "as_of", "price_time",
             "scraped_at", "duration_sec", "updated_at"}

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_SEM = threading.Semaphore(2)          # parallel AI calls cap (quota guard)

# Model fallback chain — Google kabhi-kabhi purane models ko new keys ke liye
# band kar deta hai ya 503 high-demand deta hai; chain me agla khud try hota hai.
# Jo model kaam kare wo session bhar yaad rakhta hai (_MODEL_OVERRIDE).
_MODEL_OVERRIDE: Optional[str] = None
_MODEL_OVERRIDE_LOCK = threading.Lock()


# ----------------------------------------------------------------- config / key
def _cfg() -> Dict[str, Any]:
    try:
        import yaml
        cfg = yaml.safe_load(open(ROOT / "config.yaml")) or {}
        return cfg.get("ai") or {}
    except Exception:
        return {}


def _model() -> str:
    with _MODEL_OVERRIDE_LOCK:
        if _MODEL_OVERRIDE:
            return _MODEL_OVERRIDE
    return str(_cfg().get("model") or "gemini-3-flash-preview").strip()


def _model_chain() -> List[str]:
    cfg_m = str(_cfg().get("model") or "gemini-3-flash-preview").strip()
    fb = [str(x).strip() for x in (_cfg().get("fallback_models") or []) if str(x).strip()]
    chain, seen = [], set()
    with _MODEL_OVERRIDE_LOCK:
        if _MODEL_OVERRIDE:
            chain.append(_MODEL_OVERRIDE); seen.add(_MODEL_OVERRIDE)
    for m in [cfg_m] + fb:
        if m and m not in seen:
            chain.append(m); seen.add(m)
    return chain


def _enabled_flag() -> bool:
    v = _cfg().get("enabled")
    return True if v is None else bool(v)


def _key_from_sources() -> Optional[Tuple[str, str]]:
    env = os.environ.get("GOOGLE_AI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if env and env.strip():
        return env.strip(), "env"
    try:
        if KEY_FILE.exists():
            k = KEY_FILE.read_text().strip()
            if k:
                return k, "file"
    except Exception:
        pass
    k = str(_cfg().get("api_key") or "").strip()
    if k:
        return k, "config"
    return None


def save_key(key: str) -> Dict[str, Any]:
    k = (key or "").strip()
    if len(k) < 20 or " " in k:
        return {"ok": False, "error": "Ye API key jaisi nahi lagti (Google AI Studio key: purani 'AIza…' ~39 chars ya nayi 'AQ.Ab8…' ~54 chars, no spaces)."}
    try:
        KEY_FILE.write_text(k + "\n")
        try:
            os.chmod(KEY_FILE, 0o600)
        except Exception:
            pass
        return {"ok": True, "status": status()}
    except Exception as e:
        return {"ok": False, "error": f"Key save nahi hui: {e}"}


def clear_key() -> Dict[str, Any]:
    try:
        if KEY_FILE.exists():
            KEY_FILE.unlink()
    except Exception:
        pass
    return {"ok": True, "status": status()}


def status() -> Dict[str, Any]:
    ks = _key_from_sources()
    enabled = bool(ks) and _enabled_flag()
    hint = None
    if ks:
        k = ks[0]
        hint = (k[:6] + "…" + k[-4:]) if len(k) > 12 else "set"
    return {"enabled": enabled, "model": _model(), "source": ks[1] if ks else None,
            "key_hint": hint, "config_enabled": _enabled_flag(),
            "note": "Free key: aistudio.google.com/app/apikey — key sirf tumhari machine par rehti hai."}


# ----------------------------------------------------------------- cache
def _cache_path(hash_key: str) -> Path:
    return CACHE_DIR / f"ai:{hash_key}.json"


def _cache_get(hash_key: str) -> Optional[Dict[str, Any]]:
    p = _cache_path(hash_key)
    try:
        if p.exists():
            j = json.loads(p.read_text())
            ttl_h = float(_cfg().get("cache_hours") or 168)
            if time.time() - j.get("_ts", 0) < ttl_h * 3600:
                out = dict(j.get("data") or {})
                out["cached"] = True
                return out
    except Exception:
        pass
    return None


def _cache_set(hash_key: str, data: Dict[str, Any]) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        _cache_path(hash_key).write_text(json.dumps(
            {"_ts": time.time(), "data": data}, ensure_ascii=False))
    except Exception:
        pass


def _strip_volatile(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _strip_volatile(v) for k, v in x.items() if k not in _VOLATILE}
    if isinstance(x, list):
        return [_strip_volatile(i) for i in x]
    return x


def _hash_key(section: str, payload: Any) -> str:
    stable = json.dumps(_strip_volatile(payload), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(f"{_model()}|{section}|v1|{stable}".encode()).hexdigest()[:24]


# ----------------------------------------------------------------- digest (report -> compact AI payload)
def report_digest(rep: Dict[str, Any]) -> Dict[str, Any]:
    """Pura analyze report -> chhota AI digest (sirf explain karne layak fields)."""
    q = rep.get("quote") or {}
    f = rep.get("fundamental") or {}
    t = rep.get("technical") or {}
    comp = rep.get("scoring") or {}
    d = rep.get("dcf") or {}
    pa = rep.get("price_action") or {}
    fs = f.get("fscore") or {}
    return {
        "name": rep.get("name"), "sector": rep.get("sector"), "symbol": rep.get("symbol"),
        "price": q.get("price"), "chg_1d_pct": q.get("chg_1d_pct"),
        "prev_close": q.get("prev_close"), "price_as_of": ("live " + q["as_of"]) if q.get("live") else q.get("date"),
        "scoring": {
            "total": comp.get("total"), "rating": comp.get("rating"), "label": comp.get("label"),
            "pillars": [{"name": p.get("name"), "score": p.get("score")} for p in (comp.get("pillars") or [])],
            "why_buy": (comp.get("why_buy") or [])[:3], "risks": (comp.get("risks") or [])[:4],
        },
        "fundamental": {k: f.get(k) for k in (
            "pe", "pb", "roe_pct", "roce_pct", "debt_to_equity", "cfo_pat", "opm_pct", "npm_pct",
            "cagr_profit_pct", "cagr_rev_pct", "yoy_profit_pct", "yoy_rev_pct",
            "div_yield_pct", "fcf_cr", "market_cap")},
        "fscore": {"score": fs.get("score"), "max": fs.get("max")},
        "technical": {k: t.get(k) for k in (
            "rsi14", "sma50", "sma200", "rsi_status", "golden_cross", "death_cross",
            "rs_12m", "drawdown_52w", "annual_vol_pct", "pos_52w", "high_52w", "low_52w",
            "supports", "resistances", "vol_trend")},
        "dcf": {k: d.get(k) for k in ("available", "intrinsic_per_share", "mos_pct", "growth_used_pct", "source")},
        "scenarios": rep.get("scenarios") or {},
        "price_action": {
            "summary": pa.get("summary"),
            "timing": pa.get("timing"),
            "structure": {k: (pa.get("structure") or {}).get(k) for k in ("verdict", "broke_dir", "broke_note")},
            "breakout": {k: (pa.get("breakout") or {}).get(k) for k in ("status", "level", "volume_confirmed", "retest_done")},
            "emas": {k: (pa.get("emas") or {}).get(k) for k in ("stack", "extended_pct", "golden_cross_days", "death_cross_days")},
            "mtf": {k: (pa.get("mtf") or {}).get(k) for k in ("daily", "weekly", "monthly", "alignment")},
        },
        "pledge": rep.get("pledge") or {},
        "data_quality": (rep.get("data_quality") or {}).get("grade"),
        "pros_cons": (rep.get("screener") or {}).get("proscons") or {},
    }


def _section_payload(section: str, symbol: Optional[str], payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Client payload aaye to wahi; warna symbol ke report se slice karo."""
    if payload:
        return payload
    rep = _load_report(symbol or "")
    if not rep:
        return {"error": "report nahi mila"}
    if section == "report":
        return report_digest(rep)
    dig = report_digest(rep)
    by = {
        "verdict": {"name": dig["name"], "scoring": dig["scoring"], "dcf": dig["dcf"],
                    "scenarios": dig["scenarios"], "data_quality": dig["data_quality"]},
        "dcf": {"price": dig["price"], **dig["dcf"], "scenarios": dig["scenarios"]},
        "fundamentals": {"name": dig["name"], **dig["fundamental"], "fscore": dig["fscore"]},
        "technicals": {"price": dig["price"], **dig["technical"]},
        "price_action": dig["price_action"],
        "checklist": {"fscore": dig["fscore"], "scoring": dig["scoring"]},
        "screener": {"name": dig["name"], "pros_cons": dig["pros_cons"], "pledge": dig["pledge"]},
    }
    return by.get(section) or dig


def _load_report(symbol: str) -> Optional[Dict[str, Any]]:
    if not symbol:
        return None
    try:
        import app.engine as engine
        return engine.analyze(symbol)
    except Exception:
        return None


# ----------------------------------------------------------------- prompts
_BASE_SYSTEM = (
    "Tum BharatInvest naam ke Indian stock research app ke AI explainer ho. "
    "Audience: bilkul naya retail investor jo ROE/DCF/RSI jaise terms nahi jaanta. "
    "Bhasha: simple Hinglish (Roman script) + easy English mix — chhote sentences, bina jargon, "
    "aur jo technical term aaye use bracket me asaan matlab do. "
    "Niym: (1) sirf diye gaye data se baat karo, naya number khud se mat banao; "
    "(2) investment advice NAHI — kabhi 'kharido', 'becho', 'target price' mat do; education only; "
    "(3) achha/kharab dono batao, sirf sugar-coating nahi; (4) concise — mat labao. "
    "Jawaab SIRF JSON me do is shape me: "
    '{"summary": "1-2 line simple takeaway", "points": ["..."], '
    '"glossary": [{"term": "...", "meaning": "..."}]} '
    "points me max 6 chhote bullets; glossary me max 8 terms jo is section me use hue."
)

_SECTION_TASK = {
    "report": "Ye ek stock ka pura research digest hai. Isko aise samjhao jaise kisi dost ko 2 minute me samjha rahe ho: company kya hai, score/rating ka matlab, sabse important 4-5 numbers (ROE, debt, growth, valuation, DCF) ka simple matlab, aur red flags kya hain. Har technical term glossary me do.",
    "verdict": "Ye stock ka final investment verdict hai (5-pillar score). Samjhao: rating ka matlab kya hai, score kahan se aaya (pillars), kya achha hai, kya khatre hain, aur 'HOLD/AVOID jaise labels' ka beginner matlab.",
    "dcf": "Ye DCF / intrinsic value calculation hai. Samjhao: intrinsic value kya hota hai, margin of safety ka matlab, growth rate assumption kya hai, aur ye estimates kyun galat ho sakte hain. Sensitivity ka concept simple do.",
    "fundamentals": "Ye company ke fundamental ratios hain (P/E, P/B, ROE, ROCE, D/E, margins, CFO/PAT, F-Score...). Har ratio ka simple matlab + is company ke number ka simple interpretation do (achha/normal/chinta).",
    "technicals": "Ye technical indicators hain (RSI, 200-DMA, relative strength, drawdown, volatility). Samjhao ye long-term investor ke liye kya batate hain (entry timing/context), aur overbought/oversold jaise words ka matlab.",
    "price_action": "Ye price action checklist hai (structure, support/resistance, breakout, volume, MTF). Samjhao: signal (BREAKOUT/WAIT/BUY_ZONE/TURN/CAUTION) ka beginner matlab, aur ye timing layer hai — kya khareedna hai wo fundamentals decide karte hain.",
    "checklist": "Ye engine ke Quality checks / F-Score breakdown hai. Samjhao Piotroski F-Score kya hai (9-point health check), aur 2-3 important checks ka matlab.",
    "screener": "Ye Screener.in se 10-year statements, pros/cons aur promoter pledge hai. Samjhao: kya dekhna chahiye, pledge ka matlab (risk kyun), pros/cons ko simple me.",
    "portfolio": "Ye user ka portfolio summary hai (invested, current value, P&L, CAGR, sector mix). Samjhao har number ka matlab — khaas kar CAGR vs total return ka fark, aur 'broker ka portfolio hi final sach' wali baat.",
    "fii_dii": "Ye FII/DII flows hain (videsi aur desi bade investors ka cash in/out). Samjhao FII/DII kya hote hain, net buying/selling ka market par asar, aur ek din ke number ko over-read kyun nahi karna chahiye.",
    "nifty_valuation": "Ye Nifty 50 ka valuation gauge hai (P/E, P/B, dividend yield, CHEAP/FAIR/EXPENSIVE). Samjhao index P/E kya hota hai aur iska use market timing ke liye kyun limited hai.",
}


def _build_prompt(section: str, payload: Any, question: Optional[str] = None) -> str:
    task = _SECTION_TASK.get(section) or "Is data ko beginner ko samjhao."
    data = json.dumps(payload, ensure_ascii=False, default=str)
    if len(data) > 14000:
        data = data[:14000] + " ...(trimmed)"
    if question:
        return (f"{task}\n\nDATA:\n{data}\n\nUSER KA SAWAAL: {question}\n"
                f"Sawaal ka seedha, simple jawaab do (data ke hisaab se), "
                f"aur naye technical terms glossary me daalo.")
    return f"{task}\n\nDATA (JSON):\n{data}"


# ----------------------------------------------------------------- Gemini call
class AIError(Exception):
    pass


def _friendly(status_code: int, body: str) -> str:
    if status_code in (400, 401, 403):
        if "API key not valid" in body or status_code == 403:
            return "API key Google ne reject ki — AI settings me sahi key daalo (aistudio.google.com/app/apikey)."
        return f"Google ne request thukra di ({status_code}) — model name ya key check karo."
    if status_code == 404:
        return f"Model nahi mila — config.yaml me ai.model badlo (abhi: {_model()})."
    if status_code == 429:
        return "Google ki free quota/rate limit hit ho gayi — thodi der baad try karo."
    if status_code >= 500:
        return "Google AI thoda busy hai — thodi der baad try karo."
    return f"AI call fail (HTTP {status_code})."


def _gemini(prompt: str, system: str) -> Dict[str, Any]:
    ks = _key_from_sources()
    if not ks:
        raise AIError("AI off hai — ⚙️ AI button se free API key daalo (aistudio.google.com/app/apikey).")
    key = ks[0]
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": int(_cfg().get("max_output_tokens") or 2048),
            "responseMimeType": "application/json",
        },
    }
    last_err = "AI call fail."
    recommended: Optional[str] = None     # 404 me Google khud naya model suggest karta hai
    chain = _model_chain()
    for mi, model in enumerate(chain):
        url = _GEMINI_URL.format(model=model)
        try:
            with _SEM:
                r = requests.post(url, json=body,
                                  headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                  timeout=90)
        except requests.RequestException:
            raise AIError("Google tak network nahi pahuncha — internet check karo.")
        if r.status_code == 200:
            try:
                j = r.json()
                parts = ((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts).strip()
            except Exception:
                raise AIError("AI jawaab parse nahi hua — dobara try karo.")
            if not text:
                fb = ((j.get("candidates") or [{}])[0].get("finishReason") or "")
                last_err = f"AI ne khaali jawaab diya ({fb or 'no output'}) — dobara try karo."
                continue      # agla model try karo
            with _MODEL_OVERRIDE_LOCK:
                globals()["_MODEL_OVERRIDE"] = model    # ye model chal gaya — aage yahi use hoga
            return _parse_json_reply(text)
        msg = r.text[:400]
        if r.status_code == 404:
            m2 = re.search(r"models/([A-Za-z0-9\.\-]+)", msg)
            if m2 and m2.group(1) != model and m2.group(1) not in chain:
                recommended = m2.group(1)
            last_err = f"Model {model} is key ke liye nahi chala."
        elif r.status_code == 403 or (r.status_code == 400 and "API key not valid" in msg):
            raise AIError("API key Google ne reject ki — AI settings me sahi key daalo (aistudio.google.com/app/apikey).")
        elif r.status_code == 429:
            last_err = "Google ki free quota/rate limit hit ho gayi — thodi der baad try karo."
        elif r.status_code >= 500:
            last_err = "Google AI thoda busy hai (high demand) — thodi der baad try karo."
        else:
            last_err = _friendly(r.status_code, msg)
        # 404-recommendation ko sabse aage daal do (sabse fresh model)
        if recommended:
            chain.insert(mi + 1, recommended)
            recommended = None
    raise AIError(last_err)


def _parse_json_reply(text: str) -> Dict[str, Any]:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        j = json.loads(t)
        if isinstance(j, dict):
            out = {
                "summary": str(j.get("summary") or "").strip(),
                "points": [str(p) for p in (j.get("points") or []) if str(p).strip()][:6],
                "glossary": [{"term": str(g.get("term") or "").strip(),
                              "meaning": str(g.get("meaning") or "").strip()}
                             for g in (j.get("glossary") or []) if str(g.get("term") or "").strip()][:8],
            }
            if not out["summary"] and not out["points"]:
                out["summary"] = t[:400]
            return out
    except Exception:
        pass
    # JSON hi nahi bana — plain text ko summary bana do (kabhi fail nahi hota)
    return {"summary": t[:400], "points": [], "glossary": []}


# ----------------------------------------------------------------- public API
def explain(section: str, symbol: Optional[str] = None,
            payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not _enabled_flag():
        return {"ok": False, "error": "AI layer config me band hai (ai.enabled: false)."}
    section = (section or "report").strip().lower()
    data = _section_payload(section, symbol, payload)
    if not data or data.get("error"):
        return {"ok": False, "error": "Is stock ka report nahi mila — pehle analyze karo."}
    hk = _hash_key(section, data)
    c = _cache_get(hk)
    if c:
        c["section"] = section
        return {"ok": True, **c}
    try:
        out = _gemini(_build_prompt(section, data), _BASE_SYSTEM)
    except AIError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"AI fail: {str(e)[:120]}"}
    out["section"] = section
    _cache_set(hk, out)
    return {"ok": True, **out}


def ask(symbol: str, question: str) -> Dict[str, Any]:
    """Free-text sawaal report ke context me — 'Ask AI' box ke liye."""
    q = (question or "").strip()
    if not _enabled_flag():
        return {"ok": False, "error": "AI layer config me band hai (ai.enabled: false)."}
    if len(q) < 3:
        return {"ok": False, "error": "Sawaal thoda lamba likho."}
    if len(q) > 500:
        q = q[:500]
    rep = _load_report(symbol or "")
    if not rep:
        return {"ok": False, "error": "Is stock ka report nahi mila."}
    dig = report_digest(rep)
    hk = _hash_key("ask:" + q.lower()[:120], {"sym": rep.get("symbol"), "d": dig, "q": q})
    c = _cache_get(hk)
    if c:
        return {"ok": True, **c}
    try:
        out = _gemini(_build_prompt("ask", dig, question=q), _BASE_SYSTEM)
    except AIError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"AI fail: {str(e)[:120]}"}
    _cache_set(hk, out)
    return {"ok": True, **out}
