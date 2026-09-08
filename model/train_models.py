"""
Train the risk models (Isolation Forest + XGBoost) on the cached live feed.

Weak-supervision setup, mirroring the original methodology:
- Features: the engineered risk signals (ANOMALY_FEATURES).
- Isolation Forest: unsupervised — learns the joint distribution of normal
  works and flags the statistical outskirts.
- XGBoost: regresses the deterministic rule score (weighted_rule_score) from
  the features, so the model generalises rule patterns to unseen combinations.

Usage:
    python -m model.train_models [--feed data/last_live_feed.csv]
"""

import argparse
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.risk_engine import (  # noqa: E402
    ANOMALY_FEATURES,
    CONFIG,
    apply_rule_engine,
    compute_cost_mad_scores,
    compute_vendor_features,
    detect_cross_mp_duplicates,
    detect_duplicate_descriptions,
)

MODEL_DIR = Path(__file__).resolve().parent


def build_training_frame(feed_path: Path) -> pd.DataFrame:
    """Reshape the cached long feed and derive every risk feature (no models).
    Mirrors the exact feature order of score_dataset so inference matches."""
    from backend.services.ingestion import _reshape_long_format

    raw = pd.read_csv(feed_path)
    work = _reshape_long_format(raw.copy())

    # basic work-level features (same definitions as score_dataset)
    work["utilization_ratio"] = (
        work["total_fund_disbursed"] / work["sanction_amount"].replace(0, np.nan)
    ).fillna(0)
    if "amount_disbursed" in work.columns:
        work["disbursement_mismatch_ratio"] = (
            (work["amount_disbursed"] - work["total_fund_disbursed"]).abs()
            / work["sanction_amount"].replace(0, np.nan)
        ).fillna(0)
    else:
        work["disbursement_mismatch_ratio"] = 0.0
    s_date = pd.to_datetime(work.get("sanction_date"), errors="coerce")
    work["days_since_sanction"] = (CONFIG["reference_date"] - s_date).dt.days.clip(lower=0).fillna(0)

    work = compute_cost_mad_scores(work)
    work = compute_vendor_features(work)
    work = detect_duplicate_descriptions(work)
    work = detect_cross_mp_duplicates(work)

    comp_speed = pd.to_datetime(work.get("completion_date"), errors="coerce") - \
                 pd.to_datetime(work.get("sanction_date"), errors="coerce")
    work["completion_speed_days"] = comp_speed.dt.days.clip(lower=0).fillna(0)

    work, _ = apply_rule_engine(work)
    return work


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed", default=str(MODEL_DIR.parent / "data" / "last_live_feed.csv"))
    args = parser.parse_args()

    feed_path = Path(args.feed)
    if not feed_path.exists():
        sys.exit(f"Feed cache not found at {feed_path}. Run a live sync first.")

    print(f"Loading feed: {feed_path}")
    work = build_training_frame(feed_path)
    print(f"Training corpus: {len(work)} scored works, "
          f"{int(work['rule_flag_count'].sum())} total rule triggers")

    X = work[ANOMALY_FEATURES].replace([np.inf, -np.inf], np.nan)
    X = X.fillna(X.median(numeric_only=True)).fillna(0)

    # ---- Isolation Forest ----
    print(f"Training Isolation Forest (contamination={CONFIG['isolation_contamination']})...")
    iso = IsolationForest(
        n_estimators=300,
        max_samples="auto",
        contamination=CONFIG["isolation_contamination"],
        random_state=42,
        n_jobs=-1,
    )
    iso.fit(X)
    joblib.dump(iso, MODEL_DIR / "trained_isolation_forest.pkl")
    print("  saved trained_isolation_forest.pkl")

    # ---- XGBoost (features -> weighted rule score, weak supervision) ----
    try:
        import xgboost as xgb
    except ImportError:
        print("XGBoost unavailable — trained only the Isolation Forest.")
        return

    print("Training XGBoost regressor (features -> weighted rule score)...")
    y = work["weighted_rule_score"].astype(float)
    model = xgb.XGBRegressor(
        n_estimators=400,
        max_depth=6,
        learning_rate=0.06,
        subsample=0.85,
        colsample_bytree=0.85,
        objective="reg:squarederror",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X, y)
    model.save_model(str(MODEL_DIR / "trained_xgb_model.json"))
    print("  saved trained_xgb_model.json")
    print("Model training complete.")


if __name__ == "__main__":
    main()
