#!/usr/bin/env python
"""
BharatInvest CLI (optional) — terminal access when the UI is not open.

  python cli.py market                 -> indices + FII/DII
  python cli.py top 10                 -> top ranked investment candidates
  python cli.py analyze RELIANCE.NS    -> full deep-dive report
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent   # cli.py project root par hai
sys.path.insert(0, str(ROOT))

from app import engine


def market():
    m = engine.market_overview()
    print("\n  INDICES")
    for k, v in (m.get("indices") or {}).items():
        chg = v.get("chg_pct")
        print(f"    {k:<12} {v.get('value'):>12,}   {chg:+.2f}%" if chg is not None
              else f"    {k:<12} {v.get('value')}")
    fd = m.get("fii_dii") or {}
    if fd.get("available"):
        print("\n  FII/DII (latest, ₹ crore)")
        for r in (fd.get("rows") or [])[:4]:
            print(f"    {r['date'][:10]}  {r['who']:<8} net {r['net_cr']:+,.0f}"
                  f"  (buy {r['buy_cr']:,.0f} / sell {r['sell_cr']:,.0f})")
    else:
        print("\n  FII/DII: not available (NSE blocked/unreachable)")
    print()


def top(n: int):
    s = engine.run_scan()
    rows = s.get("rows", [])
    print(f"\n  TOP {min(n, len(rows))} INVESTMENT CANDIDATES ({s.get('generated_at', '')[:10]}')")
    print(f"  {'#':>2} {'SYMBOL':<14} {'NAME':<26} {'RATING':<16} {'SCORE':>6}  {'PE':>6} {'ROE':>6} {'YoY%':>6}")
    for r in rows[:n]:
        print(f"  {r['rank']:>2} {r['symbol']:<14} {str(r['name'])[:26]:<26} {str(r['rating']):<16} "
              f"{r['total']:>6.1f}  "
              f"{('%g' % r['pe']) if r.get('pe') is not None else '-':>6} "
              f"{('%g' % r['roe_pct']) if r.get('roe_pct') is not None else '-':>6} "
              f"{('%g' % r['yoy_profit_pct']) if r.get('yoy_profit_pct') is not None else '-':>6}")
        if r.get("why_buy"):
            print(f"     ↳ {(r['why_buy'][0] or '')[:110]}")
    if s.get("unavailable"):
        print(f"\n  Data unavailable: {', '.join(s['unavailable'][:10])}")
    print()


def analyze(symbol: str):
    d = engine.analyze(symbol)
    f = d["fundamental"]
    t = d["technical"]
    comp = d["scoring"]
    q = d["quote"]
    print(f"\n  {d['name']}  [{d['symbol']}]  ·  {d['sector']}")
    print(f"  Price {q.get('price')}  ({q.get('chg_1d_pct'):+.2f}%)   "
          f"Score {comp.get('total')}   Rating {comp.get('rating')}   "
          f"Data {d['data_quality']['grade']}")
    print(f"  Pillars: " + "  ".join(
        f"{p['name']} {p['score']:.0f}" for p in comp.get("pillars", [])))
    print(f"  PE {f.get('pe')}   ROE {f.get('roe_pct')}%   D/E {f.get('debt_to_equity')}   "
          f"Profit CAGR {f.get('cagr_profit_pct')}%   CFO/PAT {f.get('cfo_pat')}   "
          f"F-Score {((f.get('fscore') or {}).get('score'))}/9")
    dcf = d["dcf"]
    if dcf.get("available"):
        print(f"  DCF intrinsic ≈ ₹{dcf['intrinsic_per_share']}   "
              f"Margin of safety {dcf.get('mos_pct')}%  ({dcf['source']})")
    sc = d.get("scenarios") or {}
    if sc.get("available"):
        print(f"  1-yr scenarios  BULL ₹{sc['bull']}  BASE ₹{sc['base']}  BEAR ₹{sc['bear']}")
    if comp.get("why_buy"):
        print("\n  WHY BUY:")
        for x in comp["why_buy"]:
            print(f"   + {x}")
    if comp.get("risks"):
        print("\n  RISKS:")
        for x in comp["risks"]:
            print(f"   ! {x}")
    print()

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "top"
    if cmd == "market":
        market()
    elif cmd == "top":
        top(int(sys.argv[2]) if len(sys.argv) > 2 else 10)
    elif cmd == "analyze":
        if len(sys.argv) < 3:
            print("usage: python cli.py analyze RELIANCE.NS")
        else:
            analyze(sys.argv[2].upper())
    else:
        print(__doc__)
