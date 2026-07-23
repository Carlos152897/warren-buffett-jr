"""Charles Schwab Trader API provider — OAuth2 refresh + accounts/positions.

Schwab access tokens expire after 30 minutes; refresh tokens are valid 7
days but are *rotated* on every refresh call (the previous refresh token
is invalidated as soon as a new one is issued). `SchwabProvider` refreshes
proactively a few minutes before expiry and persists both the new access
and refresh token back to the `.env` file named by `settings.env_file`,
so rotation is transparent across process runs. If `env_file` is unset
(e.g. `Settings` built without `load_settings`), the refreshed tokens are
kept in memory only and are not written anywhere.

`SchwabProvider` is disabled (`available == False`) when client_id,
client_secret, or a refresh_token are not configured; every public method
then returns `None` immediately without touching the network. Unlike
`FMPProvider` (per-ticker fundamentals), this provider serves account
state, which must always be current — callers get a fresh fetch on every
call (`max_age_days=0`), not stale cached financials.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wbj.providers.base import Provider

TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
BASE_URL = "https://api.schwabapi.com/trader/v1"

_ACCESS_TOKEN_LIFETIME = timedelta(minutes=30)
_REFRESH_MARGIN = timedelta(minutes=5)
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_obtained_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, _TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _persist_tokens(
    env_file: Path | None, access_token: str, refresh_token: str, obtained_at: str
) -> None:
    """Rewrite SCHWAB_ACCESS_TOKEN/SCHWAB_REFRESH_TOKEN/SCHWAB_TOKEN_OBTAINED_AT
    in `env_file` in place, preserving every other line. No-op if `env_file`
    is unset or doesn't exist yet."""
    if env_file is None or not env_file.exists():
        return

    replacements = {
        "SCHWAB_ACCESS_TOKEN": access_token,
        "SCHWAB_REFRESH_TOKEN": refresh_token,
        "SCHWAB_TOKEN_OBTAINED_AT": obtained_at,
    }
    lines = env_file.read_text().splitlines()
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Z_]+)=", line)
        if m and m.group(1) in replacements:
            key = m.group(1)
            lines[i] = f"{key}={replacements[key]}"
            seen.add(key)
    for key, value in replacements.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n")


class SchwabProvider(Provider):
    """Charles Schwab Trader API: linked brokerage accounts and positions."""

    @property
    def available(self) -> bool:
        s = self.settings
        return bool(
            s
            and getattr(s, "schwab_client_id", None)
            and getattr(s, "schwab_client_secret", None)
            and getattr(s, "schwab_refresh_token", None)
        )

    def _basic_auth_header(self) -> str:
        raw = f"{self.settings.schwab_client_id}:{self.settings.schwab_client_secret}"
        return "Basic " + base64.b64encode(raw.encode()).decode()

    def _ensure_access_token(self) -> str | None:
        """Return a valid access token, refreshing (and persisting) first if
        the current one is missing or within `_REFRESH_MARGIN` of expiry."""
        if not self.available:
            return None

        obtained_at = _parse_obtained_at(self.settings.schwab_token_obtained_at)
        access_token = self.settings.schwab_access_token
        if (
            access_token
            and obtained_at
            and _now() < obtained_at + _ACCESS_TOKEN_LIFETIME - _REFRESH_MARGIN
        ):
            return access_token

        return self._refresh()

    def _refresh(self) -> str | None:
        """Exchange the current refresh_token for a new access+refresh pair."""
        try:
            response = self.client.post(
                TOKEN_URL,
                headers={
                    "Authorization": self._basic_auth_header(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.settings.schwab_refresh_token,
                },
            )
        except Exception:
            return None

        if response.status_code >= 400:
            return None

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            return None
        refresh_token = payload.get("refresh_token") or self.settings.schwab_refresh_token
        obtained_at = _now().strftime(_TS_FORMAT)

        self.settings.schwab_access_token = access_token
        self.settings.schwab_refresh_token = refresh_token
        self.settings.schwab_token_obtained_at = obtained_at
        _persist_tokens(self.settings.env_file, access_token, refresh_token, obtained_at)
        return access_token

    def accounts(self) -> list | dict | None:
        """Linked Schwab brokerage accounts, each with balances and positions."""
        token = self._ensure_access_token()
        if not token:
            return None
        return self.get_json(
            f"{BASE_URL}/accounts",
            {"fields": "positions"},
            "schwab_accounts", "_account", max_age_days=0,
            headers={"Authorization": f"Bearer {token}"},
        )

    def account_numbers(self) -> list | dict | None:
        """Map of plain account numbers to Schwab's encrypted account ids
        (required to address other per-account endpoints)."""
        token = self._ensure_access_token()
        if not token:
            return None
        return self.get_json(
            f"{BASE_URL}/accounts/accountNumbers",
            {},
            "schwab_account_numbers", "_account", max_age_days=0,
            headers={"Authorization": f"Bearer {token}"},
        )
