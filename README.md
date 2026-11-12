<p align="center">
  <img src="https://readme-typing-svg.demolab.com?font=Fredoka:wght@500&size=30&pause=900&color=0F4C81&center=true&lines=BharatInvest+%3A+Simple+Stock+Research+with+AI+Explainer" alt="typing svg" />
</p>

<h1 align="center">💼 BharatInvest</h1>

<p align="center">
  <b>Indian stock investment research desk</b> — screens, scores, DCF, AI simple bhasha
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Gemini%20AI-8E75B2?logo=google" alt="Gemini AI" />
  <img src="https://img.shields.io/badge/Yahoo%20Finance-6000?logo=googlechrome&logoColor=white" alt="Yahoo Finance" />
  <img src="https://img.shields.io/badge/Yahoo-Finance-600000?logo=google&logoColor=white" alt="Yahoo" />
  <img src="https://img.shields.io/badge/NSE-Data-0D5C46?logo=nodedotjs&logoColor=white" alt="NSE" />
  <img src="https://img.shields.io/badge/Screener.in-0D0D0D?logo=googlechrome" alt="Screener.in" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/AI-simple-English-8E75B2?logo=googlegemini&logoColor=white" alt="AI" />
  <img src="https://img.shields.io/badge/5-Pillars%20Score-0F4C81" alt="5 Pillars" />
  <img src="https://img.shields.io/badge/Live%20LTP-Yahoo%201m-2196F3" alt="Live" />
  <img src="https://img.shields.io/badge/License-MIT-blue" alt="License" />
</p>

---

## 🧠 What is BharatInvest?

**BharatInvest** ek AI-powered Indian stock research web app hai — jisme aap kisi bhi NSE/BSE stock ko open karte ho aur **poor research report, score, charts, risks aur AI simple-explanation** mil jaati hai.

**Hamara manna:** aapki bachchi (bilkul naya investor) jo market se dar lagta hai, ko wo sahi jagah par **seekh** deta hai — kitna invest karna, kis company se — bina technical language ki mushkilat ke.

**Unique promise:**
- 🏛️ **Long-term only** — intraday ya momentum trading nahi, sirf sahi se badhi aur stable companyon ka ye badlav.
- 🧹 **Clean & simple UI** — jaise koi modern SaaS product ho: bhi toh charts hain, bhi toh data hain, lekin **English bhi, Hinglish bhi, Simple bhi**.
- 🤖 **AI explains everything in simple Hinglish** — jo bhi number dikhta hai use samjhta hai.
- 🔒 **Zero broker integration** — koi real order nahi, koi risk na lo; educational research only.

---

## 🖼️ Screenshots — Live App

### 🏠 Dashboard — market pulse + top candidates

![BharatInvest Dashboard](docs/screenshots/01_dashboard.png)

### 📊 Full ranking — Nifty 50 + quality extras

![BharatInvest Ranking](docs/screenshots/02_ranking.png)

### 📈 Stock deep-dive — fundamentals + verdict

![BharatInvest Deep Dive](docs/screenshots/03_deepdive.png)

### ✨ AI Simple Explanation + glossary chips

![BharatInvest AI Explain](docs/screenshots/04_ai_explain.png)

### 🧪 Screener — your own filters

![BharatInvest Screener](docs/screenshots/05_screener.png)

### 💼 Portfolio + Allocation Guru

![BharatInvest Portfolio](docs/screenshots/06_portfolio.png)

### ⚙️ AI settings modal (key add karna)

![BharatInvest AI Modal](docs/screenshots/07_ai_modal.png)

---

## ✨ Main Features

| Area | Kya hai | Bhejta hai |
|---|---|---|
| **🔍 Screening** | 62 NSE stocks (Nifty 50 + quality extras) ke liye scan: Quality, Growth, Valuation, Technical, Risk pillars. | `top candidates` ranked + full ranking table |
| **📈 Live prices** | Market hours (09:15–15:30 IST) me Yahoo 1-minute intraday data. | Live price + live chips UI-wide + 60 sec cache |
| **🧮 Scoring** | 5 pillars (Quality 30, Growth 20, Valuation 20, Technical 15, Risk 15). | STRONG BUY ≥ 78 · BUY ≥ 68 · HOLD ≥ 55 · AVOID (for now) ≥ 40 |
| **🔬 DCF / Valuation** | Two-stage DCF + EPV + bull/bear scenarios + margin of safety. | `Intrinsic value`, `MOS%` explained by AI |
| **🧪 Piotroski F-Score** | 9-point financial health check (from 10-year statements). | Breakdown + AI explanation |
| **📊 Price Action** | Breakout, retest, support/resistance, volume, MTF alignment → display-only timing layer. | BUY_ZONE / WAIT / CAUTION / NEUTRAL signals |
| **📰 News & Announcements** | NSE corporate announcements + Google News headlines per stock. | Filter by category |
| **💼 Portfolio** | Local holdings (JSON / Zerodha & Groww CSV import) + P&L, CAGR, sector mix. | Allocation Guru (inverse-vol budget, 35% cap/stock) |
| **🔭 FII/DII + Nifty Valuation** | Live flows + Nifty P/E gauge. | `AI Explain` from dashboard |
| **✨ AI Explain** | Google AI Studio (Gemini) integration. | Explain page, per-card ✨, Ask AI, glossary chips — all simple Hinglish |

## 🤖 AI — Simple Hinglish Explainer

- Har card par **✨ Explain** button — Fundamentals, Technicals, DCF, Price Action, Verdict, Checklist, Statements.
- Deep-dive header me **"✨ Explain page"** — poora report ek 2-minute friendly explanation me.
- **"Ask AI"** box — us stock ke report ke against aap koi bhi sawaal pooch sakte ho.
- Har answer me **summary + 6 points + glossary chips** (term → simple meaning) + education-only note.
- **AI sirf explains** — kabhi score/verdict override nahi karta, kabhi advice nahi deta.
- Model fallback chain: `gemini-3-flash-preview → gemini-3.8-flash → gemini-flash-latest` (Google ke changes handle hota hai).
- Disk cache: same content → same explanation (7 days), quota bachta hai.

## 🛠️ Technologies Used (jo tech use ki humne)

| Layer | Technology |
|---|---|
| Backend | Python 3.14, FastAPI, Uvicorn, Pydantic |
| Data + ML engines | pandas, yfinance, PyYAML, requests |
| External data | Yahoo Finance, NSE public endpoints, Screener.in, Google News |
| AI layer | Google AI Studio (Gemini) via REST — zero new dependency |
| Frontend | Vanilla JS, custom CSS, Chart.js (CDN) — no build step |
| Dev ops | Uvicorn server, uvicorn reload |

## 🏗️ Architecture

```mermaid
flowchart LR
    U[User / Investor] -->|HTTP GET/POST| F[FastAPI - app/main.py]
    F --> E[engine.py - analyze + scan orchestration]
    E --> D[datafeed.py - Yahoo + NSE + Screener + News]
    E --> M[metrics.py - technicals + fundamentals]
    E --> S[scoring.py - pillars + DCF + F-Score + scenarios]
    E --> P[priceaction.py - display-only timing]
    S --> F
    F --> UI[Frontend - static/app.js + style.css + Chart.js]
    G[AI layer - app/ai.py + Gemini]
    G -->|digest of report| F
    F --> UI
```

## 🚀 Quick Start

```bash
git clone <your-repo-url>
cd BharatInvest
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py          # or: uvicorn app.main:app --reload
```

Then open **http://127.0.0.1:8000**

---

## ⚖️ Disclaimer

**BharatInvest educational research tool hai. Koi investment advice nahi deta. Financial markets risky hain — always cross-check numbers on NSE / Screener.in before investing.**

---

<p align="center">
  Made with ❤️ in simple Hinglish — research, screen, score, AI explain.
</p>
