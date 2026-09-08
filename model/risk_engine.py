"""
MPLADS AI Sentinel — Authoritative Risk Engine Module
Single Source of Truth for MPLADS Fraud, Anomaly, and Review Prioritization Logic.
"""

from pathlib import Path
import re
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors

try:
    import xgboost as xgb
except ImportError:
    xgb = None

# ==============================================================================
# Configuration & Constants
# ==============================================================================

CONFIG = {
    'cost_mad_threshold': 4.0,
    'min_cost_peer_count': 8,
    'vendor_share_threshold': 0.30,
    'vendor_mp_share_threshold': 0.60,      # one vendor >=60% of an MP's paid works
    'vendor_multi_mp_threshold': 3,         # same vendor billing >=3 distinct MPs
    'disbursement_mismatch_pct': 0.10,
    'overdue_grace_days': 180,
    'stuck_payment_days': 90,
    'rapid_completion_days': 15,            # 'completed' within 15 days of sanction
    'post_completion_payment_days': 30,     # payments >30d after completion
    'na_id_rate_threshold': 0.40,
    'duplicate_similarity_threshold': 0.85,
    'duplicate_date_window_days': 90,
    'duplicate_cross_mp_window_days': 365,  # ghost works: same desc, different MP
    'reference_date': pd.Timestamp('2026-09-06'),
    'isolation_contamination': 0.06,
}

TRUST_SOCIETY_CATEGORIES = ['Trust and Society', 'Bar and Associations']
STUCK_STATUSES = ['Physical Inspection', 'Vendor Identification', 'Time Estimation']

RENAME_MAP = {
    'Record Type': 'record_type',
    'Source File': 'source_file',
    'Sr. No. (Source)': 'source_sr_no',
    'State': 'state',
    'Constituency': 'constituency',
    "Hon'ble Member of Parliament": 'mp_name',
    'IDA (Implementing Agency)': 'ida',
    'Work ID': 'work_id',
    'Work Category': 'work_category',
    'Work Type': 'work_type',
    'Work Description': 'work_description',
    'Recommended Date': 'recommended_date',
    'Sanction Date': 'sanction_date',
    'Completion Date': 'completion_date',
    'Expenditure Date': 'expenditure_date',
    'Consent Date': 'consent_date',
    'Recommended Amount (₹)': 'recommended_amount',
    'Sanction Amount (₹)': 'sanction_amount',
    'Amount Disbursed (₹)': 'amount_disbursed',
    'Fund Disbursed Amount (₹)': 'fund_disbursed_amount',
    'Consent Amount (₹)': 'consent_amount',
    'Allocated Amount (₹)': 'allocated_amount',
    'Work Status': 'work_status',
    'Payment Status': 'payment_status',
    'Vendor Name': 'vendor_name',
    'Calamity Type': 'calamity_type',
    'Calamity Name': 'calamity_name',
    'Image': 'image_marker',
}

RECORD_TYPES = {
    'recommended': 'Works Recommended',
    'sanctioned': 'Works Sanctioned',
    'completed': 'Works Completed',
    'expenditure': 'Expenditure on Completed & On-going Works',
    'calamity': 'Calamity Consent Amount',
    'allocation': 'MP Allocated Limit',
}

RULE_SEVERITY_WEIGHTS = {
    'flag_disbursement_mismatch': 3,
    'flag_impossible_timeline': 3,
    'flag_over_allocation': 3,
    'flag_zero_sanction_with_payments': 3,
    'flag_duplicate_across_mp': 3,
    'flag_payment_after_completion': 2,
    'flag_rapid_completion': 2,
    'flag_trust_society_routing': 2,
    'flag_vendor_concentration': 2,
    'flag_vendor_dominates_mp': 2,
    'flag_vendor_multi_mp': 2,
    'flag_cost_outlier': 2,
    'flag_duplicate_description': 2,
    'flag_duplicate_work_id': 2,
    'flag_over_utilization': 2,
    'flag_completed_without_image': 1,
    'flag_stuck_status': 1,
    'flag_stuck_payment': 1,
    'flag_high_unlinked_recommendations': 1,
    'flag_missing_vendor': 1,
    'flag_negative_sanction': 3,
}
MAX_SEVERITY_SCORE = sum(RULE_SEVERITY_WEIGHTS.values())

RULE_DESCRIPTIONS = {
    'vendor_concentration': 'One vendor accounts for an unusually large share of paid works in the state.',
    "vendor_dominates_mp": "A single vendor handles most of this MP's paid works — favouritism risk.",
    'vendor_multi_mp': 'The same vendor bills works for several different MPs — organised capture risk.',
    'trust_society_routing': 'Work category (Trust & Society / Bar associations) requires enhanced compliance review.',
    'disbursement_mismatch': 'Completed amount and summed vendor payments do not reconcile within tolerance.',
    'cost_outlier': 'Sanctioned cost is a statistical outlier vs similar works in the same state and category.',
    'stuck_status': 'Work remains in an early workflow status well beyond the expected period.',
    'stuck_payment': 'In-progress payments have shown no movement for over 90 days.',
    'impossible_timeline': 'Completion date is recorded before the sanction date.',
    'rapid_completion': 'Work was marked completed within days of sanction — implausible delivery speed.',
    'payment_after_completion': 'Vendor payments continued well after the work was marked complete.',
    'duplicate_description': 'A near-identical work description appears in the same state and time window.',
    'duplicate_across_mp': 'Near-identical description submitted by a DIFFERENT MP — classic ghost-work signal.',
    'duplicate_work_id': 'The same Work ID appears more than once in the sanctioned source records.',
    "over_allocation": "MP's total sanctioned works exceed their allocated fund ceiling.",
    'high_unlinked_recommendations': 'MP has an unusually high share of recommendations without a usable Work ID.',
    'missing_vendor': 'Vendor information is missing from expenditure records.',
    'over_utilization': 'Summed expenditure exceeds the sanctioned amount beyond tolerance.',
    'negative_sanction': 'Sanction amount is negative and requires immediate data verification.',
    'zero_sanction_with_payments': 'Vendor payments exist against a work with no/zero sanctioned cost.',
    'completed_without_image': 'Work marked complete but the portal shows no evidence attachment.',
}

ANOMALY_FEATURES = [
    'cost_mad_score',
    'vendor_share_in_state',
    'vendor_share_per_mp',
    'vendor_mp_count',
    'utilization_ratio',
    'disbursement_mismatch_ratio',
    'days_since_sanction',
    'completion_speed_days',
    'duplicate_match_count',
    'n_vendor_payments',
]

# Global cache for loaded model artifacts
_LOADED_MODELS = {
    'isolation_forest': None,
    'xgb_model': None,
}

def load_models(model_dir=None):
    """Load pretrained Isolation Forest and XGBoost model artifacts without retraining."""
    global _LOADED_MODELS
    if _LOADED_MODELS['isolation_forest'] is not None:
        return _LOADED_MODELS

    if model_dir is None:
        model_dir = Path(__file__).parent

    model_dir = Path(model_dir)
    iforest_path = model_dir / 'trained_isolation_forest.pkl'
    xgb_path = model_dir / 'trained_xgb_model.json'

    if iforest_path.exists():
        try:
            _LOADED_MODELS['isolation_forest'] = joblib.load(iforest_path)
        except Exception as e:
            print(f"Warning: Could not load Isolation Forest from {iforest_path}: {e}")

    if xgb_path.exists() and xgb is not None:
        try:
            xgb_reg = xgb.XGBRegressor()
            xgb_reg.load_model(str(xgb_path))
            _LOADED_MODELS['xgb_model'] = xgb_reg
        except Exception as e:
            print(f"Warning: Could not load XGBoost model from {xgb_path}: {e}")

    return _LOADED_MODELS


# ==============================================================================
# Helper Functions
# ==============================================================================

def normalize_description(text):
    text = '' if pd.isna(text) else str(text).lower()
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def detect_duplicate_descriptions(df, similarity_threshold=0.85, date_window_days=90):
    df = df.copy()
    if 'work_description' not in df.columns:
        df['flag_duplicate_description'] = False
        df['duplicate_match_count'] = 0
        return df

    df['normalized_description'] = df['work_description'].map(normalize_description)
    df['flag_duplicate_description'] = False
    df['duplicate_match_count'] = 0

    # 1) Exact normalized-description duplicates
    exact_groups = df.groupby(['state', 'normalized_description'], dropna=False)
    exact_counts = exact_groups['work_id'].transform('count')
    df.loc[exact_counts > 1, 'flag_duplicate_description'] = True
    df.loc[exact_counts > 1, 'duplicate_match_count'] += (exact_counts[exact_counts > 1] - 1)

    # 2) Sparse nearest-neighbor approximate matches within state
    for state, group in df.groupby('state'):
        group = group[group['normalized_description'].str.len() > 0]
        if len(group) < 2:
            continue

        texts = group['normalized_description'].tolist()
        try:
            tfidf = TfidfVectorizer(stop_words='english', ngram_range=(1, 2), min_df=1).fit_transform(texts)
        except ValueError:
            continue

        nn = NearestNeighbors(
            metric='cosine',
            algorithm='brute',
            radius=max(0.0, 1 - similarity_threshold)
        )
        nn.fit(tfidf)
        distances, indices = nn.radius_neighbors(tfidf, return_distance=True)

        idx = group.index.to_list()
        for i, neighbours in enumerate(indices):
            for j, dist in zip(neighbours, distances[i]):
                if j <= i or dist > (1 - similarity_threshold):
                    continue

                d1 = group.iloc[i].get('sanction_date')
                d2 = group.iloc[j].get('sanction_date')
                if pd.isna(d1) or pd.isna(d2):
                    continue

                if abs((pd.to_datetime(d1) - pd.to_datetime(d2)).days) <= date_window_days:
                    df.loc[idx[i], 'flag_duplicate_description'] = True
                    df.loc[idx[j], 'flag_duplicate_description'] = True
                    df.loc[idx[i], 'duplicate_match_count'] += 1
                    df.loc[idx[j], 'duplicate_match_count'] += 1

    return df



def compute_cost_mad_scores(df, group_cols=('state', 'work_category'),
                            min_peers=None):
    """Robust cost outlier score: 0.6745 * (amount - group median) / group MAD.
    Groups with fewer than min_peers works get score 0 (not enough evidence)."""
    min_peers = min_peers or CONFIG['min_cost_peer_count']
    df = df.copy()
    amount = pd.to_numeric(df.get('sanction_amount'), errors='coerce')
    df['cost_mad_score'] = 0.0
    df['peer_count'] = 0
    keyed = amount.notna() & df[list(group_cols)].notna().all(axis=1)
    for _, group in df[keyed].groupby(list(group_cols)):
        if len(group) < min_peers:
            continue
        med = amount.loc[group.index].median()
        mad = (amount.loc[group.index] - med).abs().median()
        if not mad or mad == 0:
            continue
        df.loc[group.index, 'cost_mad_score'] = 0.6745 * (amount.loc[group.index] - med) / mad
        df.loc[group.index, 'peer_count'] = len(group)
    return df


def compute_vendor_features(df):
    """Vendor-integrity features from the payment aggregates:
    - vendor_share_in_state: paid-work share of the work's primary vendor within its state
    - vendor_share_per_mp:   paid-work share of the primary vendor within its MP
    - vendor_mp_count:       how many distinct MPs this vendor bills (whole dataset)"""
    df = df.copy()
    df['vendor_share_in_state'] = 0.0
    df['vendor_share_per_mp'] = 0.0
    df['vendor_mp_count'] = 0

    paid = df[df.get('total_fund_disbursed', pd.Series(0, index=df.index)).fillna(0) > 0]
    vendors = paid['primary_vendor'].dropna()
    vendors = vendors[vendors.str.strip() != '']
    paid = paid.loc[vendors.index]
    if paid.empty:
        return df

    # state-level concentration
    state_counts = paid.groupby(['state', 'primary_vendor']).size()
    state_totals = paid.groupby('state').size()
    share_state = (state_counts / state_totals).rename('share').reset_index()
    key_state = paid[['state', 'primary_vendor']].merge(share_state, on=['state', 'primary_vendor'], how='left')
    df.loc[paid.index, 'vendor_share_in_state'] = key_state['share'].fillna(0).values

    # MP-level concentration
    mp_counts = paid.groupby(['mp_name', 'primary_vendor']).size()
    mp_totals = paid.groupby('mp_name').size()
    share_mp = (mp_counts / mp_totals).rename('share').reset_index()
    key_mp = paid[['mp_name', 'primary_vendor']].merge(share_mp, on=['mp_name', 'primary_vendor'], how='left')
    df.loc[paid.index, 'vendor_share_per_mp'] = key_mp['share'].fillna(0).values

    # vendor spread across MPs
    vendor_mp = paid.groupby('primary_vendor')['mp_name'].nunique()
    df.loc[paid.index, 'vendor_mp_count'] = paid['primary_vendor'].map(vendor_mp).fillna(0).values
    return df


def detect_cross_mp_duplicates(df, similarity_threshold=None, window_days=None):
    """Ghost-work signal: near-identical descriptions submitted by DIFFERENT MPs
    in the same state within the look-back window."""
    similarity_threshold = similarity_threshold or CONFIG['duplicate_similarity_threshold']
    window_days = window_days or CONFIG['duplicate_cross_mp_window_days']
    df = df.copy()
    df['flag_duplicate_across_mp'] = False
    if 'normalized_description' not in df.columns:
        return df

    for state, group in df.groupby('state'):
        group = group[group['normalized_description'].str.len() > 0]
        if len(group) < 2:
            continue
        texts = group['normalized_description'].tolist()
        try:
            tfidf = TfidfVectorizer(stop_words='english', ngram_range=(1, 2), min_df=1).fit_transform(texts)
        except ValueError:
            continue
        nn = NearestNeighbors(metric='cosine', algorithm='brute', radius=max(0.0, 1 - similarity_threshold))
        nn.fit(tfidf)
        distances, indices = nn.radius_neighbors(tfidf, return_distance=True)
        idx = group.index.to_list()
        for i, neighbours in enumerate(indices):
            for j, dist in zip(neighbours, distances[i]):
                if j <= i or dist > (1 - similarity_threshold):
                    continue
                r1, r2 = group.iloc[i], group.iloc[j]
                if str(r1.get('mp_name')) == str(r2.get('mp_name')):
                    continue
                d1, d2 = r1.get('sanction_date'), r2.get('sanction_date')
                if pd.notna(d1) and pd.notna(d2):
                    if abs((pd.to_datetime(d1) - pd.to_datetime(d2)).days) > window_days:
                        continue
                df.loc[idx[i], 'flag_duplicate_across_mp'] = True
                df.loc[idx[j], 'flag_duplicate_across_mp'] = True
    return df


def apply_rule_engine(df, config=CONFIG, trust_categories=TRUST_SOCIETY_CATEGORIES,
                      stuck_statuses=STUCK_STATUSES, weights=RULE_SEVERITY_WEIGHTS):
    flags = pd.DataFrame(index=df.index)

    flags['flag_vendor_concentration'] = df.get('vendor_share_in_state', pd.Series(0, index=df.index)).fillna(0) > config['vendor_share_threshold']
    flags['flag_trust_society_routing'] = df['work_category'].isin(trust_categories)
    flags['flag_disbursement_mismatch'] = df.get('disbursement_mismatch_ratio', pd.Series(0, index=df.index)).fillna(0) > config['disbursement_mismatch_pct']
    flags['flag_cost_outlier'] = (
        (df.get('peer_count', pd.Series(0, index=df.index)) >= config['min_cost_peer_count']) &
        (df.get('cost_mad_score', pd.Series(0, index=df.index)).abs() > config['cost_mad_threshold'])
    )
    flags['flag_stuck_status'] = (
        df['work_status'].isin(stuck_statuses) &
        (df.get('days_since_sanction', pd.Series(0, index=df.index)) > config['overdue_grace_days'])
    )

    last_exp = pd.to_datetime(df.get('last_expenditure_date', pd.Series(pd.NaT, index=df.index)))
    flags['flag_stuck_payment'] = (
        (df.get('n_payments_in_progress', pd.Series(0, index=df.index)) > 0) &
        ((config['reference_date'] - last_exp).dt.days.fillna(0) > config['stuck_payment_days'])
    )

    comp_date = pd.to_datetime(df.get('completion_date', pd.Series(pd.NaT, index=df.index)))
    sanc_date = pd.to_datetime(df.get('sanction_date', pd.Series(pd.NaT, index=df.index)))
    flags['flag_impossible_timeline'] = (
        comp_date.notna() & sanc_date.notna() & (comp_date < sanc_date)
    )

    flags['flag_duplicate_description'] = df.get('flag_duplicate_description', pd.Series(False, index=df.index)).fillna(False)
    flags['flag_duplicate_work_id'] = df.get('flag_duplicate_work_id', pd.Series(False, index=df.index)).fillna(False)
    flags['flag_over_allocation'] = df.get('flag_over_allocation', pd.Series(False, index=df.index)).fillna(False)
    flags['flag_high_unlinked_recommendations'] = df.get('flag_high_unlinked_recommendations', pd.Series(False, index=df.index)).fillna(False)

    flags['flag_missing_vendor'] = (
        (df['primary_vendor'].isna() | (df['primary_vendor'].astype(str).str.strip() == '')) &
        (df.get('total_fund_disbursed', pd.Series(0, index=df.index)).fillna(0) > 0)
    )
    flags['flag_over_utilization'] = df.get('flag_over_utilization', df.get('utilization_ratio', pd.Series(0, index=df.index)) > 1.05).fillna(False)
    flags['flag_negative_sanction'] = df.get('flag_negative_sanction', df['sanction_amount'] < 0).fillna(False)

    # ---- new-generation signals ----
    disbursement = df.get('total_fund_disbursed', pd.Series(0, index=df.index)).fillna(0)

    # Billing without a sanction
    flags['flag_zero_sanction_with_payments'] = (df['sanction_amount'] <= 0) & (disbursement > 0)

    # Vendor payments continuing long after completion
    last_exp = pd.to_datetime(df.get('last_expenditure_date', pd.Series(pd.NaT, index=df.index)), errors='coerce')
    flags['flag_payment_after_completion'] = (
        (disbursement > 0) & comp_date.notna() & last_exp.notna() &
        ((last_exp - comp_date).dt.days > config['post_completion_payment_days'])
    )

    # Implausibly fast delivery
    flags['flag_rapid_completion'] = (
        comp_date.notna() & sanc_date.notna() &
        ((comp_date - sanc_date).dt.days >= 0) &
        ((comp_date - sanc_date).dt.days < config['rapid_completion_days'])
    )

    # Vendor dominance over a single MP's paid works
    flags['flag_vendor_dominates_mp'] = (
        df.get('vendor_share_per_mp', pd.Series(0, index=df.index)).fillna(0) >= config['vendor_mp_share_threshold']
    )

    # Same vendor capturing works across several MPs
    flags['flag_vendor_multi_mp'] = (
        (disbursement > 0) &
        (df.get('vendor_mp_count', pd.Series(0, index=df.index)).fillna(0) >= config['vendor_multi_mp_threshold'])
    )

    # Completed but no evidence attachment on the portal
    has_image = df.get('image_marker', pd.Series('', index=df.index)).astype(str).str.lower().isin(['true', 'yes', '1'])
    flags['flag_completed_without_image'] = comp_date.notna() & ~has_image

    # MP spent beyond their allocation ceiling (map supplied at score time)
    flags['flag_over_allocation'] = df.get('flag_over_allocation', pd.Series(False, index=df.index)).fillna(False)

    # Ghost works: same description, different MP, same state, within window
    flags['flag_duplicate_across_mp'] = df.get('flag_duplicate_across_mp', pd.Series(False, index=df.index)).fillna(False)

    flag_cols = list(flags.columns)
    for c in flag_cols:
        df[c] = flags[c].fillna(False).astype(bool)

    df['rule_flag_count'] = df[flag_cols].sum(axis=1)
    df['weighted_rule_score'] = (
        sum(df[c].astype(int) * weights.get(c, 1) for c in flag_cols) / MAX_SEVERITY_SCORE
    ).clip(0, 1)

    df['rule_flags_triggered'] = df[flag_cols].apply(
        lambda row: [c.replace('flag_', '') for c in flag_cols if row[c]],
        axis=1
    )
    return df, flag_cols


def assign_tier(likelihood_score, high_threshold=0.45, medium_threshold=0.25):
    if likelihood_score >= high_threshold:
        return 'High Risk - Review'
    if likelihood_score >= medium_threshold:
        return 'Medium Risk - Monitor'
    return 'Low Risk'


def recommended_action(row):
    flags = set(row.get('rule_flags_triggered', []))
    if isinstance(flags, str):
        import ast
        try:
            flags = set(ast.literal_eval(flags))
        except Exception:
            flags = set()

    if {'impossible_timeline', 'negative_sanction', 'zero_sanction_with_payments'} & flags:
        return 'Immediate data/document verification'
    if {'duplicate_across_mp'} & flags:
        return 'Ghost-work field verification'
    if {'disbursement_mismatch', 'over_allocation', 'over_utilization', 'payment_after_completion'} & flags:
        return 'Financial reconciliation'
    if {'duplicate_description', 'duplicate_work_id'} & flags:
        return 'Duplicate-work verification'
    if {'vendor_concentration', 'vendor_dominates_mp', 'vendor_multi_mp', 'missing_vendor'} & flags:
        return 'Vendor/procurement verification'
    if 'cost_outlier' in flags:
        return 'Cost estimate verification'
    if {'stuck_status', 'stuck_payment'} & flags:
        return 'Status/payment follow-up'
    if 'high_unlinked_recommendations' in flags:
        return 'Recommendation-to-sanction reconciliation'
    if 'trust_society_routing' in flags:
        return 'Compliance/document review'
    if row.get('is_anomaly', False):
        return 'Secondary anomaly review'
    return 'Routine monitoring'


# ==============================================================================
# Full Scoring Pipeline
# ==============================================================================

def score_dataset(df, model_dir=None, mp_allocations=None):
    """
    Score incoming MPLADS records using the authoritative unified formula:
        likelihood = 0.45 * weighted_rule_score + 0.30 * anomaly_percentile + 0.25 * model_risk_score
        priority = likelihood * impact
        final_risk_score = percentile_rank(priority) * 100

    mp_allocations: optional {mp_name: allocated_amount} map — enables the
    MP over-allocation ceiling rule.
    """
    models = load_models(model_dir)
    iso_forest = models['isolation_forest']
    xgb_model = models['xgb_model']

    work = df.copy()

    # Log scoring breakdown by house for observability
    if 'house' in work.columns:
        for house_val, cnt in work['house'].value_counts().items():
            print(f"[score_dataset] Scoring {cnt} works from: {house_val}")

    # If raw long format, normalize column names
    if 'Record Type' in work.columns:
        work = work.rename(columns=RENAME_MAP)

    # Convert numeric fields
    for num_col in ['sanction_amount', 'amount_disbursed', 'total_fund_disbursed', 'fund_disbursed_amount']:
        if num_col in work.columns:
            work[num_col] = pd.to_numeric(work[num_col], errors='coerce').fillna(0)

    # Ensure work-level backbone
    if 'work_id' not in work.columns:
        raise ValueError("Dataset must contain 'work_id' column.")


    # Feature engineering if needed
    if 'utilization_ratio' not in work.columns:
        work['utilization_ratio'] = (
            work['total_fund_disbursed'] / work['sanction_amount'].replace(0, np.nan)
        ).fillna(0)

    if 'disbursement_mismatch_ratio' not in work.columns:
        if 'amount_disbursed' in work.columns:
            work['disbursement_mismatch_ratio'] = (
                (work['amount_disbursed'] - work['total_fund_disbursed']).abs() /
                work['sanction_amount'].replace(0, np.nan)
            ).fillna(0)
        else:
            work['disbursement_mismatch_ratio'] = 0.0

    if 'days_since_sanction' not in work.columns:
        if 'sanction_date' in work.columns:
            s_date = pd.to_datetime(work['sanction_date'], errors='coerce')
            work['days_since_sanction'] = (CONFIG['reference_date'] - s_date).dt.days.clip(lower=0).fillna(0)
        else:
            work['days_since_sanction'] = 0

    if 'flag_duplicate_description' not in work.columns:
        work = detect_duplicate_descriptions(work)

    # ---- derive the signals earlier rules could only dream about ----
    work = compute_cost_mad_scores(work)
    work = compute_vendor_features(work)
    work = detect_cross_mp_duplicates(work)

    comp_speed = pd.to_datetime(work.get('completion_date'), errors='coerce') -                  pd.to_datetime(work.get('sanction_date'), errors='coerce')
    work['completion_speed_days'] = comp_speed.dt.days.clip(lower=0).fillna(0)

    # MP over-allocation: sanctioned total vs the portal's allocated ceiling
    if mp_allocations and 'mp_name' in work.columns:
        sanctioned_per_mp = work.groupby('mp_name')['sanction_amount'].sum()
        over = [
            mp for mp, total in sanctioned_per_mp.items()
            if mp in mp_allocations and total > float(mp_allocations[mp]) * 1.05
        ]
        over_set = set(over)
        work['flag_over_allocation'] = work['mp_name'].isin(over_set)
        if over:
            print(f"Over-allocation rule: {len(over)} MPs exceed their ceiling")

    # Deterministic rule engine
    work, flag_cols = apply_rule_engine(work)

    # Anomaly scoring
    X_anomaly = pd.DataFrame(index=work.index)
    for col in ANOMALY_FEATURES:
        if col in work.columns:
            X_anomaly[col] = work[col].copy()
        else:
            X_anomaly[col] = 0.0

    X_anomaly = X_anomaly.replace([np.inf, -np.inf], np.nan)
    X_anomaly = X_anomaly.fillna(X_anomaly.median(numeric_only=True)).fillna(0)

    if iso_forest is not None:
        try:
            work['anomaly_raw_score'] = -iso_forest.decision_function(X_anomaly)
            work['anomaly_percentile'] = work['anomaly_raw_score'].rank(pct=True, method='average')
            work['is_anomaly'] = iso_forest.predict(X_anomaly) == -1
        except Exception as e:
            print(f"Warning: Isolation Forest inference failed: {e}")
            work['anomaly_raw_score'] = 0.0
            work['anomaly_percentile'] = 0.5
            work['is_anomaly'] = False
    else:
        work['anomaly_raw_score'] = 0.0
        work['anomaly_percentile'] = 0.5
        work['is_anomaly'] = False

    # Supervised Model scoring (XGBoost)
    if xgb_model is not None:
        try:
            xgb_features = list(ANOMALY_FEATURES)
            X_xgb = pd.DataFrame(index=work.index)
            for c in xgb_features:
                X_xgb[c] = work[c] if c in work.columns else 0.0
            X_xgb = X_xgb.fillna(0)
            work['model_risk_score'] = np.clip(xgb_model.predict(X_xgb), 0, 1)
        except Exception as e:
            print(f"Warning: XGBoost inference failed: {e}")
            work['model_risk_score'] = np.nan
    else:
        work['model_risk_score'] = np.nan

    # Unified Formula Integration
    has_model_score = work['model_risk_score'].notna().all()
    if has_model_score:
        work['likelihood_score'] = (
            0.45 * work['weighted_rule_score'] +
            0.30 * work['anomaly_percentile'] +
            0.25 * work['model_risk_score']
        ).clip(0, 1)
    else:
        # Fallback weighting if model_risk_score is unavailable
        work['likelihood_score'] = (
            0.70 * work['weighted_rule_score'] +
            0.30 * work['anomaly_percentile']
        ).clip(0, 1)

    work['impact_score'] = work['sanction_amount'].rank(pct=True, method='average').clip(0, 1)
    raw_priority = work['likelihood_score'] * (0.5 + 0.5 * work['impact_score'])

    work['final_risk_score'] = (raw_priority.rank(pct=True, method='average') * 100).round(1)
    work['priority_rank'] = raw_priority.rank(ascending=False, method='min').astype(int)

    # Set risk tiers based on likelihood percentiles
    high_threshold = work['likelihood_score'].quantile(0.90) if len(work) > 10 else 0.45
    medium_threshold = work['likelihood_score'].quantile(0.70) if len(work) > 10 else 0.25
    work['risk_tier'] = work['likelihood_score'].apply(lambda s: assign_tier(s, high_threshold, medium_threshold))

    work['recommended_action'] = work.apply(recommended_action, axis=1)
    if 'human_review_outcome' not in work.columns:
        work['human_review_outcome'] = None

    return work


# ==============================================================================
# Case Packet & API Summaries
# ==============================================================================

def generate_case_packet(work_id, work_row=None, df=None):
    """
    Generate authoritative case packet dictionary for /works/{work_id}.
    """
    if work_row is None:
        if df is None:
            raise ValueError("Must provide either work_row or dataframe.")
        matches = df[df['work_id'].astype(str) == str(work_id)]
        if matches.empty:
            raise KeyError(f"Work ID not found: {work_id}")
        row = matches.iloc[0].to_dict()
    else:
        row = work_row if isinstance(work_row, dict) else work_row.to_dict()

    raw_flags = row.get('rule_flags_triggered', [])
    if isinstance(raw_flags, str):
        import ast
        try:
            raw_flags = ast.literal_eval(raw_flags)
        except Exception:
            raw_flags = [f.strip() for f in raw_flags.strip('[]').replace("'", "").split(',') if f.strip()]

    causes = [RULE_DESCRIPTIONS.get(r, f"Flag triggered: {r}") for r in raw_flags]
    if row.get('is_anomaly', False):
        causes.append('Isolation Forest also identifies this record as statistically unusual.')

    impact_pct = round(float(row.get('impact_score', 0.5)) * 100)

    return {
        'work_id': str(row.get('work_id')),
        'mp_name': row.get('mp_name'),
        'state': row.get('state'),
        'constituency': row.get('constituency'),
        'house': row.get('house'),
        'ida': row.get('ida'),
        'primary_vendor': row.get('primary_vendor'),
        'work_category': row.get('work_category'),
        'work_type': row.get('work_type'),
        'sanction_amount': float(row.get('sanction_amount', 0)),
        'total_fund_disbursed': float(row.get('total_fund_disbursed', 0)),
        'utilization_ratio': float(row.get('utilization_ratio', 0)),
        'work_status': row.get('work_status'),
        'completion_date': str(row.get('completion_date')) if pd.notna(row.get('completion_date')) else None,
        'final_risk_score': float(row.get('final_risk_score', 0)),
        'priority_rank': int(row.get('priority_rank', 0)),
        'risk_tier': row.get('risk_tier'),
        'recommended_action': row.get('recommended_action'),
        'rule_flag_count': int(row.get('rule_flag_count', len(raw_flags))),
        'rule_flags_triggered': raw_flags,
        'causes': causes if causes else ['No specific rule triggered; record prioritized for routine monitoring.'],
        'impact_note': f"Sanctioned value is around the {impact_pct}th percentile of this portfolio.",
        'likelihood_score': float(row.get('likelihood_score', 0)),
        'impact_score': float(row.get('impact_score', 0)),
        'weighted_rule_score': float(row.get('weighted_rule_score', 0)),
        'anomaly_percentile': float(row.get('anomaly_percentile', 0)),
        'is_anomaly': bool(row.get('is_anomaly', False)),
        'human_review_outcome': row.get('human_review_outcome'),
    }


def get_work_risk_summary(work_id, df=None):
    packet = generate_case_packet(work_id, df=df)
    return {
        'work_id': packet['work_id'],
        'risk_score': packet['final_risk_score'],
        'tier': packet['risk_tier'],
        'priority_rank': packet['priority_rank'],
        'action': packet['recommended_action'],
        'flags': packet['rule_flags_triggered'],
        'causes': packet['causes'],
    }
