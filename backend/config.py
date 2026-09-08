import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Minimal .env loader — KEY=VALUE lines, no new dependency.
    Existing process env vars always win (setdefault)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")


def _cors_origins() -> list[str]:
    configured = os.getenv("CORS_ORIGINS")
    if not configured:
        return [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
    return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]

class Settings:
    PROJECT_NAME: str = "MPLADS AI Sentinel"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api"
    
    # Database URL: PostgreSQL supported; defaults to local SQLite file for development
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        f"sqlite:///{BASE_DIR / 'mplads_sentinel.db'}"
    )
    
    DATA_DIR: Path = BASE_DIR / "data"
    MODEL_DIR: Path = BASE_DIR / "model"
    # Bundled long-format sample feed, used only by tests / offline replays
    RAW_SAMPLE_PATH: Path = BASE_DIR / "data" / "mplads_raw_sample.csv"
    
    CORS_ORIGINS: list[str] = _cors_origins()
    
    # Live MPLADS dashboard API (mplads.mospi.gov.in /digigov) — the only
    # ingestion source. Houses: rajya_sabha | lok_sabha | lok_sabha_17 |
    # lok_sabha_18 | both. rajya_sabha keeps scheduled syncs fast; "both"
    # pulls the ~90 MB Lok Sabha payloads.
    MPLADS_BASE_URL: str = os.getenv("MPLADS_BASE_URL", "https://mplads.mospi.gov.in")
    MPLADS_LIVE_HOUSE: str = os.getenv("MPLADS_LIVE_HOUSE", "rajya_sabha")
    MPLADS_LIVE_TIMEOUT: int = int(os.getenv("MPLADS_LIVE_TIMEOUT", "300"))

settings = Settings()
