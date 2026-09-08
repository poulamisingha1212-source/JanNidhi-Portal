"""
Database bootstrap. There is no offline seed data: the system's only source
is the live MPLADS dashboard API. When the database is empty (first run),
an initial live sync is kicked off in a background thread so the API comes
up immediately and data streams in.
"""
import threading

from sqlalchemy import func

from backend.database import SessionLocal, engine, Base
from backend.models import Work


def _initial_live_sync():
    from backend.services.ingestion import run_ingestion
    try:
        result = run_ingestion(mode="live")
        print(f"Initial live sync finished: {result.get('status')} — "
              f"{result.get('processed', 0)} records processed.")
    except Exception as e:
        print(f"Initial live sync failed: {e}. Use POST /api/sync/run?mode=live to retry.")


def seed_database(force: bool = False):
    """
    Ensure tables exist and, when the works table is empty, start an initial
    live sync in the background. Safe to run repeatedly; idempotent.
    """
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing_count = db.query(func.count(Work.work_id)).scalar()
    finally:
        db.close()

    if existing_count and existing_count > 0 and not force:
        print(f"Database already contains {existing_count} records. Live bootstrap skipped.")
        return existing_count

    print("Database empty — starting initial live sync in the background...")
    threading.Thread(target=_initial_live_sync, daemon=True).start()
    return 0

if __name__ == "__main__":
    seed_database()
