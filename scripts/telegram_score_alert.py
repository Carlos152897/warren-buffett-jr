#!/usr/bin/env python3
"""Escaneo diario del watchlist en dos etapas. Corre en GitHub Actions — no
depende del webapp (Render) ni de la PC de Carlos.

Etapa 1 (barata, los ~15 tickers del watchlist): `wbj analyze` — solo EDGAR
(sin key) + FMP basico — filtra los que saquen score >= 8/10.

Etapa 2 (cara, solo los hits de la etapa 1 -- normalmente 0-2 al dia): arma
el Packet completo (mas llamadas a FMP: estados financieros, 252+ sesiones
de OHLCV, peers, estimados, insiders) y corre el specialist real de
valuation.py (DCF/WACC) para sacar un precio justo HOY (no una proyeccion a
12 meses) via `reference_bands.base`. Correr esto sobre las 15 tickers todos
los dias agotaria la cuota gratis de FMP -- por eso solo se hace para los
hits ya filtrados por la etapa 1.

La etapa 2 necesita `FRED_API_KEY` (gratis, fred.stlouisfed.org/docs/api/api_key.html)
para la tasa libre de riesgo del WACC -- sin ella el DCF sale NOT_SCORABLE,
nunca un numero inventado. Si falla (datos insuficientes o rate-limit de
FMP), el mensaje lo dice explicitamente en vez de omitir el precio justo.

Usage:
    FMP_API_KEY=... FRED_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
    python3 scripts/telegram_score_alert.py
    DRY_RUN=1 python3 scripts/telegram_score_alert.py   # imprime, no manda
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

sys.path.insert(0, str(REPO_ROOT / "engine"))


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


def fair_value_now(ticker: str) -> dict:
    """DCF-based fair value TODAY (not a 12-month projection) via the real
    valuation.py specialist. Returns a dict describing the outcome --
    never fabricates a number when inputs are missing/NOT_SCORABLE."""
    from datetime import datetime, timezone

    from wbj.config import load_settings
    from wbj.packet.builder import PacketRejected, Providers, build_packet
    from wbj.providers.cache import Cache
    from wbj.providers.edgar import EdgarProvider
    from wbj.providers.finnhub import FinnhubProvider
    from wbj.providers.fmp import FMPProvider
    from wbj.providers.fred import FredProvider
    from wbj.specialists import valuation as valuation_specialist

    settings = load_settings()
    cache = Cache(settings.cache_dir)
    providers = Providers(
        fmp=FMPProvider(settings, cache),
        edgar=EdgarProvider(settings, cache),
        finnhub=FinnhubProvider(settings, cache),
        fred=FredProvider(settings, cache),
    )
    try:
        packet = build_packet(ticker, providers, datetime.now(timezone.utc))
    except PacketRejected as e:
        return {"available": False, "reason": str(e)}
    except Exception as e:  # provider/network failures (e.g. FMP rate-limit)
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}

    result = valuation_specialist.run(packet)
    base = result.reference_bands.base
    if base is None:
        return {
            "available": False,
            "reason": "DCF no calculable (datos insuficientes -- ver assumptions)",
            "assumptions": result.assumptions,
            "company_name": packet.security.company_name,
            "logo_url": packet.security.logo_url,
        }
    return {
        "available": True,
        "fair_value": base,
        "bear": result.reference_bands.bear,
        "bull": result.reference_bands.bull,
        "margin_of_safety_pct": (
            (base - packet.facts_table["price"].value) / packet.facts_table["price"].value
            if packet.facts_table["price"].is_valid
            else None
        ),
        "verdict": result.verdict,
        "assumptions": result.assumptions,
        "company_name": packet.security.company_name,
        "logo_url": packet.security.logo_url,
    }


def build_intro(hits: list[dict]) -> str | None:
    if not hits:
        return None
    return (
        "🚀 *Warren Buffett Jr* — Oportunidades de hoy (score 8+)\n"
        "Clasificación de research — no es recomendación de compra/venta."
    )


def build_hit_caption(h: dict) -> str:
    fv = h.get("fair_value")
    name = fv.get("company_name") if fv else None
    header = f"*{h['ticker']}* — {name}" if name else f"*{h['ticker']}*"
    lines = [
        header,
        f"Score: {h['score10']}/10",
        f"Precio: ${h['price']:.2f} | "
        f"Bear ${h['bear']:.2f} / Base ${h['base']:.2f} / Bull ${h['bull']:.2f} (12 meses)",
        f"Evidencia: {h['evidence_points']}/100",
    ]
    if fv is None:
        pass
    elif fv["available"]:
        lines.append(
            f"Precio justo HOY (DCF): ${fv['fair_value']:.2f} "
            f"({fv['margin_of_safety_pct']:+.1%} vs precio actual) — {fv['verdict']}"
        )
    else:
        lines.append(f"Precio justo HOY (DCF): no calculable — {fv['reason']}")
    return "\n".join(lines)


def _telegram_post(method: str, payload: dict) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"Telegram {method}: {r.status} {r.read().decode()}")


def send_telegram(text: str) -> None:
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    _telegram_post("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})


def send_telegram_hit(h: dict) -> None:
    """One elegant message per ticker — its logo as the photo when FMP has
    one, plain text otherwise. Never fabricates a logo URL."""
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    caption = build_hit_caption(h)
    logo_url = (h.get("fair_value") or {}).get("logo_url")
    if logo_url:
        try:
            _telegram_post(
                "sendPhoto",
                {"chat_id": chat_id, "photo": logo_url, "caption": caption, "parse_mode": "Markdown"},
            )
            return
        except urllib.error.URLError as e:
            print(f"WARN: sendPhoto falló para {h['ticker']} ({e}), mando texto.", file=sys.stderr)
    send_telegram(caption)


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

    # Etapa 2: DCF completo solo para los hits (evita agotar la cuota de FMP).
    for h in hits:
        h["fair_value"] = fair_value_now(h["ticker"])

    intro = build_intro(hits)
    if intro is None:
        print("Ningún ticker cruzó el umbral de 8/10 hoy — no se manda nada.")
        return 0

    if os.environ.get("DRY_RUN") == "1":
        print(f"[DRY RUN]\n{intro}")
        for h in hits:
            fv = h.get("fair_value") or {}
            print(f"[DRY RUN] logo={fv.get('logo_url')}\n{build_hit_caption(h)}")
        return 0

    try:
        send_telegram(intro)
        for h in hits:
            send_telegram_hit(h)
    except urllib.error.URLError as e:
        print(f"ERROR: no pude mandar a Telegram: {e}", file=sys.stderr)
        return 1
    print("Enviado a Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
