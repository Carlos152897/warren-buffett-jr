from pathlib import Path
from wbj.config import load_settings


def test_loads_keys_from_env_file(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FMP_API_KEY=abc123\nFINNHUB_API_KEY=\n")
    s = load_settings(env_file=env)
    assert s.fmp_api_key == "abc123"
    assert s.finnhub_api_key is None  # empty string → None (key absent)


def test_missing_env_file_is_not_fatal(tmp_path: Path):
    s = load_settings(env_file=tmp_path / "nope.env")
    assert s.fmp_api_key is None


def test_settings_never_repr_keys(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FMP_API_KEY=SECRETVALUE\nSCHWAB_CLIENT_SECRET=SCHWABSECRET\n")
    s = load_settings(env_file=env)
    assert "SECRETVALUE" not in repr(s)
    assert "SCHWABSECRET" not in repr(s)


def test_loads_schwab_keys_and_env_file_path(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "SCHWAB_CLIENT_ID=cid\n"
        "SCHWAB_CLIENT_SECRET=secret\n"
        "SCHWAB_REDIRECT_URI=https://127.0.0.1\n"
        "SCHWAB_ACCESS_TOKEN=atok\n"
        "SCHWAB_REFRESH_TOKEN=rtok\n"
        "SCHWAB_TOKEN_OBTAINED_AT=2026-07-20T22:44:20Z\n"
    )
    s = load_settings(env_file=env)
    assert s.schwab_client_id == "cid"
    assert s.schwab_client_secret == "secret"
    assert s.schwab_redirect_uri == "https://127.0.0.1"
    assert s.schwab_access_token == "atok"
    assert s.schwab_refresh_token == "rtok"
    assert s.schwab_token_obtained_at == "2026-07-20T22:44:20Z"
    assert s.env_file == env
