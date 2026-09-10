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


def _clean_db_name(val: str | None) -> str:
    if not val:
        return "mplads_sentinel"
    # Strip spaces, single/double quotes, and trailing/leading invalid chars
    cleaned = val.strip().strip('"').strip("'").replace(" ", "")
    # Remove any character not allowed in Mongo database names: /\. "$*<>:|?
    for char in ['/', '\\', '.', ' ', '"', '$', '*', '<', '>', ':', '|', '?']:
        cleaned = cleaned.replace(char, '')
    return cleaned if cleaned else "mplads_sentinel"


class Settings:
    PROJECT_NAME: str = "MPLADS AI Sentinel"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api"

    # MongoDB: the only persistence layer
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017").strip().strip('"').strip("'")
    MONGO_DB_NAME: str = _clean_db_name(os.getenv("MONGO_DB_NAME"))

    DATA_DIR: Path = BASE_DIR / "data"
    MODEL_DIR: Path = BASE_DIR / "model"
    # Bundled long-format sample feed, used only by tests / offline replays
    RAW_SAMPLE_PATH: Path = BASE_DIR / "data" / "mplads_raw_sample.csv"

    # Serverless platforms freeze the process between requests, so the
    # in-process scheduler and the startup live-sync thread must not run.
    IS_SERVERLESS: bool = os.getenv("VERCEL") == "1" or os.getenv("IS_SERVERLESS") == "1"

    # When empty and SEED_FROM_SAMPLE=1, first boot ingests the bundled
    # sample CSV synchronously so a fresh deployment shows data immediately;
    # scheduled live syncs then replace it with real portal data.
    SEED_FROM_SAMPLE: bool = os.getenv("SEED_FROM_SAMPLE", "").lower() in {"1", "true", "yes"}

    # Vercel Cron authenticates with `Authorization: Bearer $CRON_SECRET`
    # (the env var of this exact name). Also accepted as X-Cron-Secret.
    CRON_SECRET: str = os.getenv("CRON_SECRET", "")
    
    CORS_ORIGINS: list[str] = _cors_origins()
    
    # Live MPLADS dashboard API (mplads.mospi.gov.in /digigov) — the only
    # ingestion source. Houses: rajya_sabha | lok_sabha | lok_sabha_17 |
    # lok_sabha_18 | both. rajya_sabha keeps scheduled syncs fast; "both"
    # pulls the ~90 MB Lok Sabha payloads.
    MPLADS_BASE_URL: str = os.getenv("MPLADS_BASE_URL", "https://mplads.mospi.gov.in")
    MPLADS_LIVE_HOUSE: str = os.getenv("MPLADS_LIVE_HOUSE", "both")
    MPLADS_LIVE_TIMEOUT: int = int(os.getenv("MPLADS_LIVE_TIMEOUT", "300"))

settings = Settings()
