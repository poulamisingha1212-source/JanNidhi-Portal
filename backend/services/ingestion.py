"""
MPLADS data ingestion pipeline — live portal only.

The single data source is the live MPLADS dashboard API
(mplads.mospi.gov.in /digigov internal REST endpoint, implemented in
backend/services/mplads_live.py). Every sync fetches fresh per-work
records for the houses selected via MPLADS_LIVE_HOUSE, scores them with
the risk engine, and upserts. `mode="auto"` is an alias for "live".

An explicit `source_file_path` may be passed for tests / offline replays;
it follows the same long-format contract and bypasses the network.

Every run appends exactly one entry to sync_logs. On failure the
last-known-good database is preserved and the failure is logged.
"""
import time
from datetime import datetime, timezone
from pathlib import Path
import logging

import pandas as pd

from backend.database import SessionLocal
from backend.models import Work, SyncLog, MPAllocation
from backend.config import settings
from model.risk_engine import score_dataset

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Canonical source label used across the sync log UI
SOURCE_LIVE = "MPLADS Live Dashboard API (mplads.mospi.gov.in)"

VALID_MODES = {"auto", "live"}

_UPSERT_CHUNK = 900  # stay under SQLite's 999 bind-parameter limit



# ------------------------------------------------------------------------------
# Normalization & scoring (shared with file-based offline replays)
# ------------------------------------------------------------------------------

def _validate_house(df: pd.DataFrame) -> None:
    """Log warnings for missing or unknown house values in the dataframe.
    The ingestion pipeline expects a 'house' column indicating Lok Sabha or Rajya Sabha.
    """
    if "house" not in df.columns:
        logger.warning("Dataframe missing 'house' column — house will not be tracked.")
        return
    missing = df["house"].isna() | (df["house"].astype(str).str.strip() == "")
    if missing.any():
        logger.warning("%d rows have missing house information.", missing.sum())


def _reshape_long_format(df: pd.DataFrame) -> pd.DataFrame:
    """Convert the portal's long format (record_type rows) into work-level facts.

    Validates and preserves the 'house' column (Lok Sabha / Rajya Sabha)
    throughout the reshape so downstream consumers receive it correctly.
    """
    _validate_house(df)

    if "record_type" not in df.columns:
        if "total_fund_disbursed" not in df.columns:
            df["total_fund_disbursed"] = 0.0
        return df

    sanctioned = df[df["record_type"] == "Works Sanctioned"].copy()
    if len(sanctioned) == 0:
        sanctioned = df.dropna(subset=["work_id"]).drop_duplicates("work_id").copy()
    else:
        sanctioned = sanctioned.drop_duplicates("work_id")

    completed = df[df["record_type"] == "Works Completed"].copy()
    expenditure = df[df["record_type"] == "Expenditure on Completed & On-going Works"].copy()

    if len(expenditure) > 0 and "fund_disbursed_amount" in expenditure.columns:
        expenditure["fund_disbursed_amount"] = pd.to_numeric(
            expenditure["fund_disbursed_amount"], errors="coerce"
        ).fillna(0)
        # Rich per-work payment aggregates power the risk engine's vendor,
        # stall and post-completion billing signals.
        exp_agg = expenditure.groupby("work_id").agg(
            total_fund_disbursed=("fund_disbursed_amount", "sum"),
            n_vendor_payments=("fund_disbursed_amount", "count"),
            primary_vendor=("vendor_name", lambda s: s.dropna().mode().iat[0] if not s.dropna().mode().empty else None),
            last_expenditure_date=("expenditure_date", lambda s: s.dropna().max() if s.notna().any() else None),
            payment_statuses=("payment_status", lambda s: "|".join(sorted({str(x) for x in s.dropna()}))),
        ).reset_index()
        exp_agg["n_distinct_vendors"] = expenditure.dropna(subset=["vendor_name"]).groupby("work_id")["vendor_name"].nunique()
        sanctioned = sanctioned.merge(exp_agg, on="work_id", how="left")

    if len(completed) > 0 and "amount_disbursed" in completed.columns:
        comp_slim = completed[["work_id", "completion_date", "amount_disbursed"]].dropna(
            subset=["work_id"]
        ).drop_duplicates("work_id")
        sanctioned = sanctioned.merge(comp_slim, on="work_id", how="left", suffixes=("", "_comp"))
        # Sanctioned rows carry empty completion_date/amount_disbursed columns, so
        # the merged values land in *_comp — coalesce them back into the real ones.
        for col in ("completion_date", "amount_disbursed"):
            comp_col = f"{col}_comp"
            if comp_col in sanctioned.columns:
                sanctioned[col] = sanctioned[col].where(sanctioned[col].notna(), sanctioned[comp_col])
                sanctioned.drop(columns=[comp_col], inplace=True)

    if "total_fund_disbursed" not in sanctioned.columns:
        sanctioned["total_fund_disbursed"] = 0.0
    sanctioned["total_fund_disbursed"] = sanctioned["total_fund_disbursed"].fillna(0.0)

    # Preserve the 'house' column from the source dataframe.
    # Use index alignment so house labels survive any merge reindexing.
    if "house" in df.columns and "house" not in sanctioned.columns:
        house_map = df.drop_duplicates("work_id").set_index("work_id")["house"]
        if "work_id" in sanctioned.columns:
            sanctioned["house"] = sanctioned["work_id"].map(house_map)
        else:
            sanctioned["house"] = df.loc[sanctioned.index, "house"].values

    return sanctioned



def _normalize_work(row: pd.Series) -> dict:
    """Map any scored row into the Work table schema (used for bulk mappings)."""
    def _s(key, default=None):
        val = row.get(key)
        return None if pd.isna(val) else str(val)

    def _f(key, default=0.0):
        val = row.get(key, default)
        try:
            return float(default if pd.isna(val) else val)
        except (TypeError, ValueError):
            return float(default)

    flags = row.get("rule_flags_triggered", "[]")
    if isinstance(flags, (list, tuple)):
        flags = str(list(flags))

    # House must be propagated by the ingestion pipeline via the 'house' column.
    # We no longer infer it from constituency to avoid silently misclassifying MPs.
    house = _s("house")
    if not house:
        logger.warning(
            "Row work_id=%s is missing 'house' value; defaulting to 'Lok Sabha'. "
            "Check the fetch/reshape pipeline.",
            row.get("work_id"),
        )
        house = "Lok Sabha"

    # CSV round-trips can turn numeric work IDs into '1000.0' — strip it
    work_id = str(row["work_id"])
    if work_id.endswith(".0"):
        work_id = work_id[:-2]

    return {
        "work_id": work_id,
        "mp_name": _s("mp_name"),
        "state": _s("state"),
        "constituency": _s("constituency"),
        "house": house,
        "ida": _s("ida"),
        "primary_vendor": _s("primary_vendor"),
        "work_category": _s("work_category"),
        "work_type": _s("work_type"),
        "sanction_amount": _f("sanction_amount"),
        "total_fund_disbursed": _f("total_fund_disbursed"),
        "utilization_ratio": _f("utilization_ratio"),
        "work_status": _s("work_status"),
        "completion_date": _s("completion_date"),
        "final_risk_score": _f("final_risk_score"),
        "priority_rank": int(_f("priority_rank", 999999)),
        "risk_tier": _s("risk_tier") or "Low Risk",
        "recommended_action": _s("recommended_action") or "Routine monitoring",
        "rule_flag_count": int(_f("rule_flag_count")),
        "rule_flags_triggered": flags,
        "likelihood_score": _f("likelihood_score"),
        "impact_score": _f("impact_score"),
        "weighted_rule_score": _f("weighted_rule_score"),
        "anomaly_percentile": _f("anomaly_percentile"),
        "is_anomaly": bool(row.get("is_anomaly", False)),
    }


def _upsert_dataframe(db, df: pd.DataFrame) -> dict:
    """Chunked bulk upsert — one round-trip per ~900 rows, not per row.
    Each chunk commits immediately so the SQLite write lock is released
    continuously and concurrent API reads stay live during long syncs."""
    inserted = updated = 0
    now = datetime.now(timezone.utc)
    rows = [r for _, r in df.iterrows() if not pd.isna(r.get("work_id"))]

    for start in range(0, len(rows), _UPSERT_CHUNK):
        chunk = rows[start:start + _UPSERT_CHUNK]
        mappings = [_normalize_work(r) for r in chunk]
        ids = [m["work_id"] for m in mappings]

        existing_ids = {
            w[0]
            for w in db.query(Work.work_id).filter(Work.work_id.in_(ids)).all()
        }

        new_mappings, upd_mappings = [], []
        for m in mappings:
            if m["work_id"] in existing_ids:
                upd_mappings.append(m)
            else:
                m["created_at"] = now
                new_mappings.append(m)

        if new_mappings:
            db.bulk_insert_mappings(Work, new_mappings)
            inserted += len(new_mappings)
        if upd_mappings:
            for m in upd_mappings:
                m["updated_at"] = now
            db.bulk_update_mappings(Work, upd_mappings)
            updated += len(upd_mappings)
        db.commit()

    return {"inserted": inserted, "updated": updated, "processed": len(rows)}


def _upsert_allocations(db, long_df: pd.DataFrame) -> int:
    """Upsert the per-MP allocated funds from the portal's Allocated Limit
    dataset into mp_allocations, keyed on (mp_name, house, constituency, state)."""
    alloc = long_df[long_df.get("record_type") == "MP Allocated Limit"]
    if alloc.empty:
        return 0
    count = 0
    for _, r in alloc.iterrows():
        if pd.isna(r.get("mp_name")):
            continue
        raw_house = r.get("house")
        if raw_house and not pd.isna(raw_house):
            house_val = str(raw_house)
        else:
            logger.warning(
                "Allocation row for mp_name=%s missing 'house'; defaulting to 'Lok Sabha'. "
                "Check the fetch pipeline.",
                r.get("mp_name"),
            )
            house_val = "Lok Sabha"

        key = dict(
            mp_name=str(r["mp_name"]),
            house=house_val,
            constituency=str(r.get("constituency") or ""),
            state=str(r.get("state") or ""),
        )
        values = dict(
            allocated_amount=float(r.get("allocated_amount") or 0),
            tenure_start=str(r.get("recommended_date")) if pd.notna(r.get("recommended_date")) else None,
            updated_at=datetime.now(timezone.utc),
        )
        row = db.query(MPAllocation).filter_by(**key).one_or_none()
        if row:
            for k, v in values.items():
                setattr(row, k, v)
        else:
            db.add(MPAllocation(**key, **values))
        count += 1
    db.commit()
    return count


def _log_sync(db, *, source, status, start_dt, counts=None, note=None):
    end_dt = datetime.now(timezone.utc)
    counts = counts or {}
    db.add(SyncLog(
        run_timestamp=start_dt,
        start_time=start_dt,
        end_time=end_dt,
        status=status,
        source=source,
        rows_fetched=counts.get("fetched", 0),
        rows_processed=counts.get("processed", 0),
        rows_inserted=counts.get("inserted", 0),
        rows_updated=counts.get("updated", 0),
        rows_rejected=counts.get("rejected", 0),
        error_message=note,
    ))
    db.commit()


# ------------------------------------------------------------------------------
# Public entry points
# ------------------------------------------------------------------------------

def run_ingestion(mode: str = "auto", source_file_path: Path = None) -> dict:
    """
    Run the ingestion pipeline.

    Modes: "auto" / "live" — fetch the live MPLADS dashboard API, risk-score,
    and upsert. Each run writes exactly one sync_logs entry.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid ingestion mode '{mode}'. Must be one of {sorted(VALID_MODES)}")

    start_dt = datetime.now(timezone.utc)
    t0 = time.time()
    db = SessionLocal()

    try:
        if source_file_path is not None:
            # Explicit file override (tests / offline replays)
            df = pd.read_csv(source_file_path)
            scored = score_dataset(_reshape_long_format(df), model_dir=settings.MODEL_DIR)
            counts = _upsert_dataframe(db, scored)
            counts["fetched"] = len(df)
            db.commit()
            label = f"Ingestion Feed (file: {Path(source_file_path).name})"
            _log_sync(db, source=label, status="success", start_dt=start_dt, counts=counts)
            return {"status": "success", "mode": "file", "source": label,
                    "duration_seconds": round(time.time() - t0, 2), **counts}

        # ---- Live MPLADS dashboard API ------------------------------------
        try:
            from backend.services.mplads_live import fetch_live_long_dataframe
            raw = fetch_live_long_dataframe(houses=settings.MPLADS_LIVE_HOUSE)

            # Cache the raw long feed so models can be retrained and the
            # dataset re-scored offline without re-fetching from the portal.
            try:
                cache_path = settings.DATA_DIR / "last_live_feed.csv"
                raw.to_csv(cache_path, index=False)
                print(f"Raw feed cached to {cache_path.name} ({len(raw)} rows)")
            except Exception as ce:
                print(f"Feed caching skipped: {ce}")

            alloc_map = {a.mp_name: a.allocated_amount for a in db.query(MPAllocation).all()}
            scored = score_dataset(_reshape_long_format(raw.copy()), model_dir=settings.MODEL_DIR,
                                   mp_allocations=alloc_map)
            counts = _upsert_dataframe(db, scored)
            counts["allocations"] = _upsert_allocations(db, raw)
            counts["fetched"] = len(raw)
            db.commit()
            _log_sync(db, source=SOURCE_LIVE, status="success",
                      start_dt=start_dt, counts=counts)
            return {"status": "success", "mode": "live", "source": SOURCE_LIVE,
                    "duration_seconds": round(time.time() - t0, 2), **counts}
        except Exception as e:
            db.rollback()
            note = f"Live dashboard API unavailable ({e})"
            _log_sync(db, source=SOURCE_LIVE, status="failed",
                      start_dt=start_dt, note=note)
            return {"status": "failed", "mode": "live",
                    "error": note, "duration_seconds": round(time.time() - t0, 2)}

    finally:
        db.close()


def get_sync_status() -> dict:
    """Returns the latest sync status and checks if data is stale (> 24 hours)."""
    db = SessionLocal()
    try:
        last_sync = db.query(SyncLog).order_by(SyncLog.run_timestamp.desc()).first()
        if not last_sync:
            return {
                "latest_sync_timestamp": None,
                "latest_sync_status": "none",
                "is_data_stale": True,
                "staleness_message": "No sync records found. Please trigger an initial sync.",
                "rows_processed": 0
            }

        now = datetime.now(timezone.utc)
        sync_time = last_sync.run_timestamp
        if sync_time.tzinfo is None:
            sync_time = sync_time.replace(tzinfo=timezone.utc)

        age_hours = (now - sync_time).total_seconds() / 3600.0
        is_stale = age_hours > 24.0 or last_sync.status == "failed"

        if last_sync.status == "failed":
            msg = f"Data sync failed ({last_sync.error_message}). Displaying last-known-good dataset."
        elif is_stale:
            msg = f"Data is stale (last synced {round(age_hours, 1)} hours ago)."
        else:
            msg = f"Data is fresh and synchronized ({round(age_hours, 1)}h ago)."

        return {
            "latest_sync_timestamp": last_sync.run_timestamp.isoformat(),
            "latest_sync_status": last_sync.status,
            "latest_sync_source": last_sync.source,
            "is_data_stale": is_stale,
            "staleness_message": msg,
            "rows_processed": last_sync.rows_processed,
            "source": last_sync.source,
            "error_message": last_sync.error_message,
        }
    finally:
        db.close()
