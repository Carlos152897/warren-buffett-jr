"""Weekly job: re-run the screener and replace the webapp's watchlist with the
current top picks. Usage: .venv/bin/python scripts/refresh_watchlist.py
"""

from __future__ import annotations

import json

from wbj.config import load_settings
from wbj.screener import screen as run_screen

LIMIT = 15


def main() -> None:
    settings = load_settings()
    watchlist_path = settings.reports_dir.parent / "Watchlist" / "watchlist.json"
    rows = run_screen(limit=LIMIT)
    items = [{"ticker": r["ticker"], "name": r["name"]} for r in rows]
    watchlist_path.parent.mkdir(parents=True, exist_ok=True)
    watchlist_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Watchlist actualizada ({len(items)} tickers): {', '.join(i['ticker'] for i in items)}")


if __name__ == "__main__":
    main()
