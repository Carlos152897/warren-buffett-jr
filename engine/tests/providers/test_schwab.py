"""Tests for wbj.providers.schwab.SchwabProvider."""

from datetime import datetime, timedelta, timezone

import httpx

from wbj.config import Settings
from wbj.providers.cache import Cache
from wbj.providers.schwab import SchwabProvider

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _make_provider(tmp_path, handler, env_file=None, **settings_kwargs):
    settings = Settings(
        schwab_client_id=settings_kwargs.pop("schwab_client_id", "cid"),
        schwab_client_secret=settings_kwargs.pop("schwab_client_secret", "secret"),
        schwab_refresh_token=settings_kwargs.pop("schwab_refresh_token", "rtok"),
        env_file=env_file,
        **settings_kwargs,
    )
    cache = Cache(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SchwabProvider(settings, cache, client=client)


# --- availability -----------------------------------------------------------


def test_available_true_when_credentials_set(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json={}))
    assert p.available is True


def test_available_false_when_refresh_token_missing(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json={}), schwab_refresh_token=None)
    assert p.available is False


def test_methods_return_none_and_skip_network_when_unavailable(tmp_path):
    def handler(request):
        raise AssertionError("transport should not be called when unavailable")

    p = _make_provider(tmp_path, handler, schwab_client_id=None)
    assert p.accounts() is None
    assert p.account_numbers() is None


# --- token refresh ------------------------------------------------------------


def test_reuses_unexpired_access_token_without_network_call(tmp_path):
    def handler(request):
        raise AssertionError("should not refresh a still-valid token")

    obtained_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(_TS_FORMAT)
    p = _make_provider(
        tmp_path, handler,
        schwab_access_token="still-valid",
        schwab_token_obtained_at=obtained_at,
    )
    assert p._ensure_access_token() == "still-valid"


def test_refreshes_expired_token_and_persists_to_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FMP_API_KEY=unrelated\n"
        "SCHWAB_ACCESS_TOKEN=old-access\n"
        "SCHWAB_REFRESH_TOKEN=old-refresh\n"
        "SCHWAB_TOKEN_OBTAINED_AT=2020-01-01T00:00:00Z\n"
    )
    captured = {}

    def handler(request):
        captured["request"] = request
        return httpx.Response(
            200,
            json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 1800},
        )

    stale_obtained_at = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(_TS_FORMAT)
    p = _make_provider(
        tmp_path, handler,
        env_file=env_file,
        schwab_access_token="old-access",
        schwab_refresh_token="old-refresh",
        schwab_token_obtained_at=stale_obtained_at,
    )

    token = p._ensure_access_token()

    assert token == "new-access"
    assert captured["request"].method == "POST"
    assert p.settings.schwab_refresh_token == "new-refresh"

    updated = env_file.read_text()
    assert "SCHWAB_ACCESS_TOKEN=new-access" in updated
    assert "SCHWAB_REFRESH_TOKEN=new-refresh" in updated
    assert "FMP_API_KEY=unrelated" in updated  # untouched lines preserved
    assert "old-access" not in updated
    assert "old-refresh" not in updated


def test_refresh_failure_returns_none(tmp_path):
    p = _make_provider(
        tmp_path,
        lambda request: httpx.Response(400, json={"error": "invalid_grant"}),
    )
    assert p._ensure_access_token() is None


# --- accounts -----------------------------------------------------------


def test_accounts_sends_bearer_token_and_returns_payload(tmp_path):
    captured = {}
    fixture = [{"securitiesAccount": {"accountNumber": "1234", "type": "CASH"}}]

    def handler(request):
        captured["request"] = request
        return httpx.Response(200, json=fixture)

    obtained_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(_TS_FORMAT)
    p = _make_provider(
        tmp_path, handler,
        schwab_access_token="valid-token",
        schwab_token_obtained_at=obtained_at,
    )

    result = p.accounts()

    assert result == fixture
    assert captured["request"].headers["authorization"] == "Bearer valid-token"
    assert "accounts" in str(captured["request"].url)
