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
from backend.database import works, mp_allocations, users, ensure_indexes
from backend.models import now_utc, lower_or_none


def _initial_live_sync():
    from backend.services.ingestion import run_ingestion
    try:
        result = run_ingestion(mode="live")
        print(f"Initial live sync finished: {result.get('status')} — "
              f"{result.get('processed', 0)} records processed.")
    except Exception as e:
        print(f"Initial live sync failed: {e}. Use POST /api/sync/run?mode=live to retry.")


def _ensure_default_allocations() -> int:
    """
    Fallback seeder: if mp_allocations is empty or missing entries for MPs in works,
    upsert default statutory allocation entries (₹12 Cr per MP per term).
    """
    from pymongo import ReplaceOne

    now = now_utc()
    pipeline = [
        {"$match": {"mp_name": {"$ne": None, "$exists": True}}},
        {"$group": {
            "_id": "$mp_name",
            "house": {"$first": "$house"},
            "constituency": {"$first": "$constituency"},
            "state": {"$first": "$state"},
        }}
    ]

    distinct_mps = list(works.aggregate(pipeline))
    if not distinct_mps:
        return 0

    ops = []
    for mp in distinct_mps:
        mp_name = mp["_id"]
        house_val = mp.get("house") or "Lok Sabha"
        constituency_val = mp.get("constituency") or ""
        state_val = mp.get("state") or ""

        key = dict(
            mp_name=mp_name,
            house=house_val,
            constituency=constituency_val,
            state=state_val,
        )
        doc = {
            **key,
            "allocated_amount": 120000000.0,
            "tenure_start": None,
            "updated_at": now,
            "_mp_name_lower": lower_or_none(mp_name),
        }
        ops.append(ReplaceOne(key, doc, upsert=True))

    if ops:
        mp_allocations.bulk_write(ops, ordered=False)
        print(f"Ensured {len(ops)} MP allocation records in mp_allocations.")
    return len(ops)


def _seed_from_sample() -> int:
    from backend.services.ingestion import run_ingestion
    if not settings.RAW_SAMPLE_PATH.exists():
        print(f"Sample feed not found at {settings.RAW_SAMPLE_PATH}; skipping sample seed.")
        _ensure_default_allocations()
        return 0
    try:
        result = run_ingestion(mode="auto", source_file_path=settings.RAW_SAMPLE_PATH)
        print(f"Sample seed finished: {result.get('processed', 0)} records processed.")
        _ensure_default_allocations()
        return int(result.get("processed", 0))
    except Exception as e:
        print(f"Sample seed failed: {e}. Live sync will retry via the scheduler/cron.")
        _ensure_default_allocations()
        return 0


def seed_users():
    """
    Ensure default administrative user exists in MongoDB `users` collection.
    User: Netai / Password: 713502
    """
    default_user = {
        "username": "Netai",
        "password": "713502",
        "role": "MoSPI Reviewer",
        "created_at": now_utc(),
    }
    users.update_one(
        {"username": "Netai"},
        {"$setOnInsert": default_user},
        upsert=True
    )
    print("User 'Netai' seeded into users collection.")


def seed_database(force: bool = False):
    """
    Ensure indexes exist and populate/update works, mp_allocations and users collections —
    synchronously from the bundled sample feed on startup so all features and
    charts render full data. Safe to run repeatedly; idempotent.
    """
    ensure_indexes()
    seed_users()

    # Ensure all existing mp_allocations records carry 12 Cr (120,000,000.0)
    mp_allocations.update_many({}, {"$set": {"allocated_amount": 120000000.0}})

    existing_count = works.count_documents({})
    alloc_count = mp_allocations.count_documents({})

    # If both works (>= 1000) and mp_allocations (> 0) contain data and force is False, skip
    if existing_count >= 1000 and alloc_count > 0 and not force:
        print(f"Database already contains {existing_count} works and {alloc_count} allocations. Bootstrap skipped.")
        return existing_count

    if settings.SEED_FROM_SAMPLE or existing_count < 1000 or alloc_count == 0 or force:
        print(f"Seeding database (works: {existing_count}, allocations: {alloc_count})...")
        return _seed_from_sample()

    print("Starting initial live sync in the background...")
    threading.Thread(target=_initial_live_sync, daemon=True).start()
    return 0

if __name__ == "__main__":
    seed_database()
