"""
Re-score the database from the cached live feed — no network needed.

Runs the upgraded engine over data/last_live_feed.csv and upserts every work
into the database with fresh risk scores, tiers, and rule flags. Use this
after retraining the models; the nightly sync keeps the underlying data fresh.

Usage:
    python -m model.rescore_from_cache [--feed data/last_live_feed.csv]
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import SessionLocal  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.models import MPAllocation  # noqa: E402
from backend.services.ingestion import _reshape_long_format, _upsert_dataframe  # noqa: E402
from model.risk_engine import score_dataset  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed", default=str(settings.DATA_DIR / "last_live_feed.csv"))
    args = parser.parse_args()

    feed_path = Path(args.feed)
    if not feed_path.exists():
        sys.exit(f"Feed cache not found at {feed_path}. Run a live sync first.")

    print(f"Loading cached feed: {feed_path}")
    raw = pd.read_csv(feed_path)
    if "work_id" in raw.columns:
        raw["work_id"] = raw["work_id"].astype(str).str.replace(r"\.0$", "", regex=True)

    db = SessionLocal()
    try:
        alloc_map = {a.mp_name: a.allocated_amount for a in db.query(MPAllocation).all()}
        print(f"Scoring {len(raw)} long-format rows "
              f"({len(alloc_map)} MP allocation ceilings loaded)...")
        t0 = time.time()
        scored = score_dataset(_reshape_long_format(raw.copy()),
                               model_dir=settings.MODEL_DIR,
                               mp_allocations=alloc_map)
        print(f"Scoring done in {round(time.time() - t0, 1)}s")

        counts = _upsert_dataframe(db, scored)
        print(f"Upserted: {counts['inserted']} inserted, {counts['updated']} updated, "
              f"{counts['processed']} processed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
