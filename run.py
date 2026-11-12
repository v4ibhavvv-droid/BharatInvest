"""BharatInvest launcher — python run.py  ->  http://localhost:8000"""
from __future__ import annotations

import os
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn
import yaml

ROOT = Path(__file__).resolve().parent   # run.py project root par hai
os.chdir(ROOT)

CFG = yaml.safe_load(open(ROOT / "config.yaml"))["service"]


def browser_open(url: str):
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    host = CFG.get("host", "127.0.0.1")
    port = int(CFG.get("port", 8000))
    url = f"http://{host}:{port}"
    print()
    print("  BharatInvest — Indian equity investment research desk")
    print(f"  Opening  {url}   (Ctrl+C to stop)")
    print()
    threading.Timer(1.5, lambda: browser_open(url)).start()
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
