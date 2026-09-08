"""
Database bootstrap. The system's primary source is the live MPLADS dashboard
API. When the database is empty (first run):

- Serverless (SEED_FROM_SAMPLE=1): the bundled sample CSV is ingested
  synchronously so a fresh deployment shows data immediately; the scheduled
  live sync then replaces it with real portal data.
- Otherwise: an initial live sync is kicked off in a background thread so the
  API comes up immediately and data streams in.
"""
import threading

from backend.config import settings
from backend.database import works, ensure_indexes


def _initial_live_sync():
    from backend.services.ingestion import run_ingestion
    try:
        result = run_ingestion(mode="live")
        print(f"Initial live sync finished: {result.get('status')} — "
              f"{result.get('processed', 0)} records processed.")
    except Exception as e:
        print(f"Initial live sync failed: {e}. Use POST /api/sync/run?mode=live to retry.")


def _seed_from_sample() -> int:
    from backend.services.ingestion import run_ingestion
    if not settings.RAW_SAMPLE_PATH.exists():
        print(f"Sample feed not found at {settings.RAW_SAMPLE_PATH}; skipping sample seed.")
        return 0
    try:
        result = run_ingestion(mode="auto", source_file_path=settings.RAW_SAMPLE_PATH)
        print(f"Sample seed finished: {result.get('processed', 0)} records processed.")
        return int(result.get("processed", 0))
    except Exception as e:
        print(f"Sample seed failed: {e}. Live sync will retry via the scheduler/cron.")
        return 0


def seed_database(force: bool = False):
    """
    Ensure indexes exist and, when the works collection is empty, populate it —
    synchronously from the bundled sample on serverless, otherwise via a
    background live sync. Safe to run repeatedly; idempotent.
    """
    ensure_indexes()

    existing_count = works.count_documents({})
    if existing_count and existing_count > 0 and not force:
        print(f"Database already contains {existing_count} records. Bootstrap skipped.")
        return existing_count

    if settings.SEED_FROM_SAMPLE:
        print("Database empty — seeding from the bundled sample feed...")
        return _seed_from_sample()

    print("Database empty — starting initial live sync in the background...")
    threading.Thread(target=_initial_live_sync, daemon=True).start()
    return 0

if __name__ == "__main__":
    seed_database()
