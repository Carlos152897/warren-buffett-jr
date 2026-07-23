#!/usr/bin/env python3
"""Resumen diario del watchlist por Telegram — corre en GitHub Actions.

Lee /api/watchlist del webapp ya desplegado (Render) y manda un resumen
corto al bot de Telegram de Carlos. No toca Robinhood — eso sigue siendo
un chequeo local (tarea programada wbj-agentic-daily-review), fuera del
alcance de este script.

Usage:
    TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... WBJ_WEB_URL=https://... \
    WBJ_WEB_PASSWORD=... python3 scripts/telegram_watchlist.py
    DRY_RUN=1 python3 scripts/telegram_watchlist.py   # prueba local sin enviar

Env vars:
    TELEGRAM_BOT_TOKEN  token del bot (de @BotFather)
    TELEGRAM_CHAT_ID    chat id numérico al que mandar el mensaje
    WBJ_WEB_URL         URL pública del webapp desplegado (sin / final)
    WBJ_WEB_PASSWORD    password de HTTP Basic Auth del webapp
    DRY_RUN=1           imprime el mensaje en stdout en vez de enviarlo

Stdlib only — sin dependencias.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request

WBJ_WEB_URL = os.environ.get("WBJ_WEB_URL", "").rstrip("/")
WBJ_WEB_PASSWORD = os.environ.get("WBJ_WEB_PASSWORD", "")


def fetch_watchlist() -> list[dict]:
    req = urllib.request.Request(f"{WBJ_WEB_URL}/api/watchlist")
    if WBJ_WEB_PASSWORD:
        token = base64.b64encode(f":{WBJ_WEB_PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fmt_pct(p: float | None) -> str:
    if p is None:
        return "s/d"
    return f"{'+' if p > 0 else ''}{p * 100:.1f}%"


def build_message(items: list[dict]) -> str:
    lines = ["📋 Watchlist Warren Buffett Jr"]
    for item in items:
        price = item.get("price")
        price_str = f"${price:.2f}" if price is not None else "s/d"
        lines.append(
            f"{item['ticker']}: {price_str} ({fmt_pct(item.get('change_pct'))})"
        )
    lines.append("")
    lines.append("Clasificación de research — no es recomendación de compra/venta.")
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
    if not WBJ_WEB_URL:
        print("ERROR: falta WBJ_WEB_URL", file=sys.stderr)
        return 1
    try:
        items = fetch_watchlist()
    except urllib.error.URLError as e:
        print(f"ERROR: no pude leer {WBJ_WEB_URL}/api/watchlist: {e}", file=sys.stderr)
        return 1
    if not items:
        print("Watchlist vacío — nada que mandar.")
        return 0

    text = build_message(items)

    if os.environ.get("DRY_RUN") == "1":
        print(f"[DRY RUN]\n{text}")
        return 0
    send_telegram(text)
    print("Enviado a Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
