#!/usr/bin/env python3
"""Escaneo diario del watchlist: corre `wbj analyze` en cada ticker y avisa por
Telegram los que saquen score >= 8/10. Corre en GitHub Actions — no depende del
webapp (Render) ni de la PC de Carlos, solo de EDGAR (sin key) + FMP.

Usage:
    FMP_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
    python3 scripts/telegram_score_alert.py
    DRY_RUN=1 python3 scripts/telegram_score_alert.py   # imprime, no manda

Requiere que `wbj` este instalado (pip install -e ./engine).
Stdlib only aparte del subprocess a `wbj` — sin dependencias nuevas.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = REPO_ROOT / "Watchlist" / "watchlist.json"
REPORTS_DIR = REPO_ROOT / "Reportes"
SCORE_THRESHOLD = 8.0


def load_tickers() -> list[str]:
    if not WATCHLIST_PATH.exists():
        return []
    items = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    return [i["ticker"] for i in items]


def run_analyze(ticker: str) -> None:
    subprocess.run(["wbj", "analyze", ticker], capture_output=True, text=True)


def read_prediction(ticker: str) -> dict | None:
    path = REPORTS_DIR / ticker / date.today().isoformat() / "prediccion.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_message(hits: list[dict]) -> str | None:
    if not hits:
        return None
    lines = ["🚀 Warren Buffett Jr — Oportunidades de hoy (score 8+)"]
    for h in hits:
        lines.append(
            f"\n{h['ticker']} — {h['score10']}/10\n"
            f"Precio: ${h['price']:.2f} | "
            f"Bear ${h['bear']:.2f} / Base ${h['base']:.2f} / Bull ${h['bull']:.2f}\n"
            f"Evidencia: {h['evidence_points']}/100"
        )
    lines.append("\nClasificación de research — no es recomendación de compra/venta.")
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"Telegram: {r.status} {r.read().decode()}")


def main() -> int:
    tickers = load_tickers()
    if not tickers:
        print("Watchlist vacío — nada que analizar.")
        return 0

    hits: list[dict] = []
    for ticker in tickers:
        run_analyze(ticker)
        result = read_prediction(ticker)
        if result and result.get("score10", 0) >= SCORE_THRESHOLD:
            hits.append(result)

    text = build_message(hits)
    if text is None:
        print("Ningún ticker cruzó el umbral de 8/10 hoy — no se manda nada.")
        return 0

    if os.environ.get("DRY_RUN") == "1":
        print(f"[DRY RUN]\n{text}")
        return 0

    try:
        send_telegram(text)
    except urllib.error.URLError as e:
        print(f"ERROR: no pude mandar a Telegram: {e}", file=sys.stderr)
        return 1
    print("Enviado a Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
