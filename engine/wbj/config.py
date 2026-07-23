"""Configuration loader for wbj compute engine."""

from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values


def _find_repo_root() -> Path:
    """Derive repo_root: two parents up from wbj/ directory."""
    # wbj package is at engine/wbj/
    wbj_dir = Path(__file__).parent  # engine/wbj/
    engine_dir = wbj_dir.parent  # engine/
    repo_root = engine_dir.parent  # repo_root
    return repo_root


@dataclass(repr=False)
class Settings:
    """Warren Buffett Jr settings, never repr keys."""

    fmp_api_key: str | None = None
    finnhub_api_key: str | None = None
    fred_api_key: str | None = None
    anthropic_api_key: str | None = None
    # Charles Schwab Trader API (OAuth2) — brokerage accounts/positions.
    schwab_client_id: str | None = None
    schwab_client_secret: str | None = None
    schwab_redirect_uri: str | None = None
    schwab_access_token: str | None = None
    schwab_refresh_token: str | None = None
    schwab_token_obtained_at: str | None = None
    # Model for the qualitative judgment agent; override to cut cost (e.g.
    # "claude-haiku-4-5"). Default per the Anthropic SDK guidance.
    judge_model: str = "claude-opus-4-8"
    repo_root: Path = field(default_factory=_find_repo_root)
    cache_dir: Path = field(default_factory=lambda: _find_repo_root() / "engine" / "cache")
    reports_dir: Path = field(default_factory=lambda: _find_repo_root() / "Reportes")
    # Where these settings were loaded from; SchwabProvider writes refreshed
    # tokens back here (None if Settings was built without load_settings).
    env_file: Path | None = None

    def __repr__(self) -> str:
        """Custom repr that never includes secret keys."""
        return (
            f"Settings(fmp_api_key={'*' * 8 if self.fmp_api_key else None}, "
            f"finnhub_api_key={'*' * 8 if self.finnhub_api_key else None}, "
            f"fred_api_key={'*' * 8 if self.fred_api_key else None}, "
            f"schwab_client_id={'*' * 8 if self.schwab_client_id else None}, "
            f"schwab_client_secret={'*' * 8 if self.schwab_client_secret else None}, "
            f"schwab_access_token={'*' * 8 if self.schwab_access_token else None}, "
            f"schwab_refresh_token={'*' * 8 if self.schwab_refresh_token else None}, "
            f"repo_root={self.repo_root}, "
            f"cache_dir={self.cache_dir}, "
            f"reports_dir={self.reports_dir})"
        )


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from env file, with defaults.

    Args:
        env_file: Path to .env file. Defaults to <repo_root>/API/.env.

    Returns:
        Settings instance with keys from env file (or None if missing/empty),
        falling back to real process environment variables (e.g. secrets set
        in a cloud host's dashboard, where there is no committed .env file).
    """
    import os

    repo_root = _find_repo_root()

    if env_file is None:
        env_file = repo_root / "API" / ".env"

    # Load env file if it exists; otherwise return defaults
    env_vars = {}
    if env_file.exists():
        env_vars = dotenv_values(env_file)

    def _get(key: str) -> str | None:
        return env_vars.get(key) or os.environ.get(key) or None

    fmp_api_key = _get("FMP_API_KEY")
    finnhub_api_key = _get("FINNHUB_API_KEY")
    fred_api_key = _get("FRED_API_KEY")
    anthropic_api_key = _get("ANTHROPIC_API_KEY")
    schwab_client_id = _get("SCHWAB_CLIENT_ID")
    schwab_client_secret = _get("SCHWAB_CLIENT_SECRET")
    schwab_redirect_uri = _get("SCHWAB_REDIRECT_URI")
    schwab_access_token = _get("SCHWAB_ACCESS_TOKEN")
    schwab_refresh_token = _get("SCHWAB_REFRESH_TOKEN")
    schwab_token_obtained_at = _get("SCHWAB_TOKEN_OBTAINED_AT")
    judge_model = _get("JUDGE_MODEL") or "claude-opus-4-8"

    # WBJ_DATA_DIR: set this on a cloud host (pointed at a persistent
    # disk/volume) so the watchlist, portfolio, cache and reports survive
    # redeploys. Unset locally, so Windows behavior is unchanged.
    data_dir_env = os.environ.get("WBJ_DATA_DIR")
    if data_dir_env:
        data_dir = Path(data_dir_env)
        cache_dir = data_dir / "cache"
        reports_dir = data_dir / "Reportes"
    else:
        cache_dir = repo_root / "engine" / "cache"
        reports_dir = repo_root / "Reportes"

    return Settings(
        fmp_api_key=fmp_api_key,
        finnhub_api_key=finnhub_api_key,
        fred_api_key=fred_api_key,
        anthropic_api_key=anthropic_api_key,
        schwab_client_id=schwab_client_id,
        schwab_client_secret=schwab_client_secret,
        schwab_redirect_uri=schwab_redirect_uri,
        schwab_access_token=schwab_access_token,
        schwab_refresh_token=schwab_refresh_token,
        schwab_token_obtained_at=schwab_token_obtained_at,
        judge_model=judge_model,
        repo_root=repo_root,
        cache_dir=cache_dir,
        reports_dir=reports_dir,
        env_file=env_file,
    )
