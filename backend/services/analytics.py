"""
Aggregate analytics queries shared by the REST endpoints.

Every transparency-facing aggregation (MP directory, MP profile, state
directory/profile, category & status analytics, CSV export) lives here so the
route handlers in backend/main.py stay thin and the SQL is testable.
"""
import ast
import csv
import io
from typing import Optional, Generator

from sqlalchemy import func, case, desc, asc, distinct
from sqlalchemy.orm import Query, Session

from backend.models import Work, ReviewLog, MPAllocation

HIGH_RISK_TIER = "High Risk - Review"
MEDIUM_RISK_TIER = "Medium Risk - Monitor"
LOW_RISK_TIER = "Low Risk"

# Fields a client may sort the works list by. Anything else is rejected.
WORK_SORTABLE_FIELDS = {
    "priority_rank", "final_risk_score", "sanction_amount",
    "total_fund_disbursed", "utilization_ratio", "mp_name", "state",
    "work_category", "work_status", "rule_flag_count", "updated_at",
    "created_at",
}

# Aggregate sort keys accepted by the MP / state directories.
DIRECTORY_SORTABLE_FIELDS = {
    "name", "works_count", "total_sanctioned", "total_disbursed",
    "avg_utilization", "avg_risk_score", "high_risk_count", "mp_count",
}


# ------------------------------------------------------------------------------
# Shared expressions & helpers
# ------------------------------------------------------------------------------

def high_risk_expr():
    return func.sum(case((Work.risk_tier == HIGH_RISK_TIER, 1), else_=0))


def medium_risk_expr():
    return func.sum(case((Work.risk_tier == MEDIUM_RISK_TIER, 1), else_=0))


def reviewed_expr():
    return func.sum(case((Work.human_review_outcome.isnot(None), 1), else_=0))


def parse_rule_flags(raw: Optional[str]) -> list:
    """Deserialize the rule_flags_triggered column into a clean list."""
    if not raw:
        return []
    try:
        parsed = ast.literal_eval(raw)
        return [str(f) for f in parsed] if isinstance(parsed, (list, tuple)) else []
    except (ValueError, SyntaxError):
        return [f.strip() for f in raw.strip("[]").replace("'", "").split(",") if f.strip()]


def work_to_list_item(w: Work) -> dict:
    """Serialize a Work ORM row into the WorkListItem payload shape."""
    flags = parse_rule_flags(w.rule_flags_triggered)
    return {
        "work_id": w.work_id,
        "mp_name": w.mp_name,
        "state": w.state,
        "constituency": w.constituency,
        "ida": w.ida,
        "primary_vendor": w.primary_vendor,
        "work_category": w.work_category,
        "work_type": w.work_type,
        "sanction_amount": w.sanction_amount,
        "total_fund_disbursed": w.total_fund_disbursed,
        "utilization_ratio": w.utilization_ratio,
        "work_status": w.work_status,
        "completion_date": w.completion_date,
        "final_risk_score": w.final_risk_score,
        "priority_rank": w.priority_rank,
        "risk_tier": w.risk_tier,
        "recommended_action": w.recommended_action,
        "rule_flag_count": w.rule_flag_count,
        "rule_flags_triggered": flags,
        "human_review_outcome": w.human_review_outcome,
    }


def apply_house(query: Query, house: Optional[str]) -> Query:
    """Constrain a query to a single house when one is selected."""
    if house:
        query = query.filter(Work.house == house.strip())
    return query


def apply_work_filters(
    query: Query,
    state: Optional[str] = None,
    mp_name: Optional[str] = None,
    house: Optional[str] = None,
    ida: Optional[str] = None,
    risk_tier: Optional[str] = None,
    work_category: Optional[str] = None,
    work_status: Optional[str] = None,
    search: Optional[str] = None,
) -> Query:
    """Apply the standard works-list filters consistently across list & export."""
    if state:
        query = query.filter(Work.state.ilike(f"%{state.strip()}%"))
    if mp_name:
        query = query.filter(Work.mp_name.ilike(f"%{mp_name.strip()}%"))
    if house:
        query = query.filter(Work.house == house.strip())
    if ida:
        query = query.filter(Work.ida.ilike(f"%{ida.strip()}%"))
    if risk_tier:
        query = query.filter(Work.risk_tier == risk_tier.strip())
    if work_category:
        query = query.filter(Work.work_category == work_category.strip())
    if work_status:
        query = query.filter(Work.work_status == work_status.strip())
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(
            (Work.work_id.ilike(s))
            | (Work.primary_vendor.ilike(s))
            | (Work.work_type.ilike(s))
        )
    return query


def apply_directory_sort(query: Query, expr_map: dict, sort_by: str, order: str) -> Query:
    """Order a grouped directory query by a whitelisted aggregate expression."""
    sort_expr = expr_map.get(sort_by, expr_map["total_sanctioned"])
    return query.order_by(desc(sort_expr) if order.lower() == "desc" else asc(sort_expr))


# ------------------------------------------------------------------------------
# MP directory & profile
# ------------------------------------------------------------------------------

def get_mp_directory(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    state: Optional[str] = None,
    house: Optional[str] = None,
    sort_by: str = "total_sanctioned",
    order: str = "desc",
) -> dict:
    """Paginated MP-wise fund & risk aggregation for the public MP directory."""
    select_cols = [
        Work.mp_name.label("name"),
        func.max(Work.constituency).label("constituency"),
        func.max(Work.state).label("state"),
        func.count(Work.work_id).label("works_count"),
        func.coalesce(func.sum(Work.sanction_amount), 0.0).label("total_sanctioned"),
        func.coalesce(func.sum(Work.total_fund_disbursed), 0.0).label("total_disbursed"),
        func.coalesce(func.avg(Work.utilization_ratio), 0.0).label("avg_utilization"),
        func.coalesce(func.avg(Work.final_risk_score), 0.0).label("avg_risk_score"),
        func.coalesce(func.max(Work.final_risk_score), 0.0).label("max_risk_score"),
        func.coalesce(high_risk_expr(), 0).label("high_risk_count"),
        func.coalesce(medium_risk_expr(), 0).label("medium_risk_count"),
        func.coalesce(reviewed_expr(), 0).label("reviewed_count"),
    ]
    alloc_q = db.query(
        MPAllocation.mp_name.label("amp"),
        func.sum(MPAllocation.allocated_amount).label("allocated"),
    )
    if house:
        alloc_q = alloc_q.filter(MPAllocation.house == house.strip())
    alloc_sq = alloc_q.group_by(MPAllocation.mp_name).subquery()

    query = db.query(*select_cols,
                     func.coalesce(alloc_sq.c.allocated, 0.0).label("allocated_amount")
                     ).outerjoin(alloc_sq, alloc_sq.c.amp == Work.mp_name).filter(
        Work.mp_name.isnot(None), Work.mp_name != ""
    ).group_by(Work.mp_name)

    if state:
        query = query.filter(Work.state.ilike(f"%{state.strip()}%"))
    if house:
        query = query.filter(Work.house == house.strip())
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(
            (Work.mp_name.ilike(s))
            | (Work.constituency.ilike(s))
        )

    total = query.count()
    query = apply_directory_sort(query, {
        "name": Work.mp_name,
        "works_count": func.count(Work.work_id),
        "total_sanctioned": func.sum(Work.sanction_amount),
        "total_disbursed": func.sum(Work.total_fund_disbursed),
        "avg_utilization": func.avg(Work.utilization_ratio),
        "avg_risk_score": func.avg(Work.final_risk_score),
        "high_risk_count": high_risk_expr(),
    }, sort_by, order)

    offset = (page - 1) * page_size
    rows = query.offset(offset).limit(page_size).all()

    items = []
    for rank, r in enumerate(rows, start=offset + 1):
        items.append({
            "rank": rank,
            "mp_name": r.name,
            "constituency": r.constituency,
            "state": r.state,
            "works_count": int(r.works_count or 0),
            "total_sanctioned": round(float(r.total_sanctioned or 0), 2),
            "total_disbursed": round(float(r.total_disbursed or 0), 2),
            "avg_utilization": round(float(r.avg_utilization or 0), 4),
            "avg_risk_score": round(float(r.avg_risk_score or 0), 1),
            "max_risk_score": round(float(r.max_risk_score or 0), 1),
            "high_risk_count": int(r.high_risk_count or 0),
            "medium_risk_count": int(r.medium_risk_count or 0),
            "reviewed_count": int(r.reviewed_count or 0),
            "allocated_amount": round(float(getattr(r, "allocated_amount", 0) or 0), 2),
            "total_allocated": round(float(getattr(r, "allocated_amount", 0) or 0), 2),
        })

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    return {
        "total": total, "page": page, "page_size": page_size,
        "total_pages": total_pages, "items": items,
    }


def get_mp_profile(db: Session, mp_name: str, house: Optional[str] = None) -> Optional[dict]:
    """Full transparency dossier for one MP: funds, risk tiers, breakdowns, works."""
    match = func.lower(Work.mp_name) == mp_name.strip().lower()
    scoped_match = (match, Work.house == house.strip()) if house else (match,)

    agg = db.query(
        func.count(Work.work_id).label("works_count"),
        func.coalesce(func.sum(Work.sanction_amount), 0.0).label("total_sanctioned"),
        func.coalesce(func.sum(Work.total_fund_disbursed), 0.0).label("total_disbursed"),
        func.coalesce(func.avg(Work.utilization_ratio), 0.0).label("avg_utilization"),
        func.coalesce(func.avg(Work.final_risk_score), 0.0).label("avg_risk_score"),
        func.coalesce(func.max(Work.final_risk_score), 0.0).label("max_risk_score"),
        func.coalesce(high_risk_expr(), 0).label("high_risk_count"),
        func.coalesce(medium_risk_expr(), 0).label("medium_risk_count"),
        func.coalesce(reviewed_expr(), 0).label("reviewed_count"),
        func.max(Work.constituency).label("constituency"),
        func.max(Work.state).label("state"),
        func.max(Work.house).label("house"),
    ).filter(*scoped_match).first()

    if not agg or int(agg.works_count or 0) == 0:
        return None

    # Fetch total allocated amount from mp_allocations table
    allocation = db.query(
        func.coalesce(func.sum(MPAllocation.allocated_amount), 0.0).label("total_allocated")
    ).filter(func.lower(MPAllocation.mp_name) == mp_name.strip().lower())
    if house:
        allocation = allocation.filter(MPAllocation.house == house.strip())
    allocation = allocation.first()

    # The portal's allocated limit is an MP-level ledger value. It must never
    # fall back to the sum of work sanctions, which is a different source field.
    total_allocated = round(float(allocation.total_allocated or 0), 2) if allocation else 0.0

    tier_rows = (
        db.query(Work.risk_tier, func.count(Work.work_id))
        .filter(*scoped_match).group_by(Work.risk_tier).all()
    )
    tier_distribution = {HIGH_RISK_TIER: 0, MEDIUM_RISK_TIER: 0, LOW_RISK_TIER: 0}
    for tier, cnt in tier_rows:
        if tier in tier_distribution:
            tier_distribution[tier] = int(cnt)

    def _breakdown(column, limit):
        rows = (
            db.query(
                column.label("name"),
                func.count(Work.work_id).label("count"),
                func.coalesce(func.sum(Work.sanction_amount), 0.0).label("total_sanctioned"),
                func.coalesce(func.sum(Work.total_fund_disbursed), 0.0).label("total_disbursed"),
                func.coalesce(func.avg(Work.final_risk_score), 0.0).label("avg_risk_score"),
                func.coalesce(high_risk_expr(), 0).label("high_risk_count"),
            )
            .filter(*scoped_match, column.isnot(None), column != "")
            .group_by(column)
            .order_by(desc(func.sum(Work.sanction_amount)))
            .limit(limit)
            .all()
        )
        return [
            {
                "name": r.name,
                "count": int(r.count),
                "total_sanctioned": round(float(r.total_sanctioned), 2),
                "total_disbursed": round(float(r.total_disbursed), 2),
                "avg_risk_score": round(float(r.avg_risk_score), 1),
                "high_risk_count": int(r.high_risk_count),
            }
            for r in rows
        ]

    top_works = (
        db.query(Work).filter(*scoped_match)
        .order_by(desc(Work.final_risk_score), asc(Work.work_id))
        .limit(10).all()
    )

    recent_reviews = (
        db.query(ReviewLog)
        .join(Work, Work.work_id == ReviewLog.work_id)
        .filter(*scoped_match)
        .order_by(desc(ReviewLog.created_at))
        .limit(10).all()
    )

    # Count unique vendors for this MP
    vendor_count = db.query(func.count(distinct(Work.primary_vendor))).filter(
        *scoped_match, Work.primary_vendor.isnot(None), Work.primary_vendor != ""
    ).scalar() or 0

    return {
        "mp_name": mp_name.strip(),
        "constituency": agg.constituency,
        "state": agg.state,
        "works_count": int(agg.works_count),
        "total_allocated": total_allocated,
        "allocated_amount": total_allocated,
        "total_sanctioned": round(float(agg.total_sanctioned), 2),
        "sanction_amount": round(float(agg.total_sanctioned), 2),
        "total_disbursed": round(float(agg.total_disbursed), 2),
        "fund_disbursed_amount": round(float(agg.total_disbursed), 2),
        "avg_utilization": round(float(agg.avg_utilization), 4),
        "avg_risk_score": round(float(agg.avg_risk_score), 1),
        "max_risk_score": round(float(agg.max_risk_score), 1),
        "high_risk_count": int(agg.high_risk_count),
        "medium_risk_count": int(agg.medium_risk_count),
        "low_risk_count": tier_distribution[LOW_RISK_TIER],
        "reviewed_count": int(agg.reviewed_count),
        "vendor_count": int(vendor_count),
        "tier_distribution": tier_distribution,
        "category_breakdown": _breakdown(Work.work_category, 12),
        "status_breakdown": _breakdown(Work.work_status, 12),
        "agency_breakdown": _breakdown(Work.ida, 8),
        "top_vendors": _breakdown(Work.primary_vendor, 8),
        "top_risk_works": [work_to_list_item(w) for w in top_works],
        "recent_reviews": [
            {
                "id": r.id,
                "work_id": r.work_id,
                "reviewer_name": r.reviewer_name,
                "reviewer_role": r.reviewer_role,
                "outcome": r.outcome,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recent_reviews
        ],
    }


# ------------------------------------------------------------------------------
# State directory & profile
# ------------------------------------------------------------------------------

def get_state_directory(
    db: Session,
    house: Optional[str] = None,
    page: int = 1,
    page_size: int = 40,
    sort_by: str = "total_sanctioned",
    order: str = "desc",
) -> dict:
    """State-wise aggregation: funds, MPs covered, risk concentration."""
    select_cols = [
        Work.state.label("name"),
        func.count(distinct(Work.mp_name)).label("mp_count"),
        func.count(Work.work_id).label("works_count"),
        func.coalesce(func.sum(Work.sanction_amount), 0.0).label("total_sanctioned"),
        func.coalesce(func.sum(Work.total_fund_disbursed), 0.0).label("total_disbursed"),
        func.coalesce(func.avg(Work.utilization_ratio), 0.0).label("avg_utilization"),
        func.coalesce(func.avg(Work.final_risk_score), 0.0).label("avg_risk_score"),
        func.coalesce(high_risk_expr(), 0).label("high_risk_count"),
        func.coalesce(medium_risk_expr(), 0).label("medium_risk_count"),
        func.coalesce(reviewed_expr(), 0).label("reviewed_count"),
    ]
    query = db.query(*select_cols).filter(
        Work.state.isnot(None), Work.state != ""
    ).group_by(Work.state)
    if house:
        query = query.filter(Work.house == house.strip())
    total = query.count()

    query = apply_directory_sort(query, {
        "name": Work.state,
        "mp_count": func.count(distinct(Work.mp_name)),
        "works_count": func.count(Work.work_id),
        "total_sanctioned": func.sum(Work.sanction_amount),
        "total_disbursed": func.sum(Work.total_fund_disbursed),
        "avg_utilization": func.avg(Work.utilization_ratio),
        "avg_risk_score": func.avg(Work.final_risk_score),
        "high_risk_count": high_risk_expr(),
    }, sort_by, order)

    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "rank": (page - 1) * page_size + idx,
            "state": r.name,
            "mp_count": int(r.mp_count or 0),
            "works_count": int(r.works_count or 0),
            "total_sanctioned": round(float(r.total_sanctioned or 0), 2),
            "total_disbursed": round(float(r.total_disbursed or 0), 2),
            "allocated_amount": round(float(getattr(r, "allocated_amount", 0) or 0), 2),
            "avg_utilization": round(float(r.avg_utilization or 0), 4),
            "avg_risk_score": round(float(r.avg_risk_score or 0), 1),
            "high_risk_count": int(r.high_risk_count or 0),
            "medium_risk_count": int(r.medium_risk_count or 0),
            "reviewed_count": int(r.reviewed_count or 0),
        }
        for idx, r in enumerate(rows, start=1)
    ]

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    return {
        "total": total, "page": page, "page_size": page_size,
        "total_pages": total_pages, "items": items,
    }


def get_state_profile(db: Session, state: str) -> Optional[dict]:
    """State dossier: funds, tier spread, top MPs, agencies and categories."""
    match = func.lower(Work.state) == state.strip().lower()

    agg = db.query(
        func.count(Work.work_id).label("works_count"),
        func.count(distinct(Work.mp_name)).label("mp_count"),
        func.coalesce(func.sum(Work.sanction_amount), 0.0).label("total_sanctioned"),
        func.coalesce(func.sum(Work.total_fund_disbursed), 0.0).label("total_disbursed"),
        func.coalesce(func.avg(Work.utilization_ratio), 0.0).label("avg_utilization"),
        func.coalesce(func.avg(Work.final_risk_score), 0.0).label("avg_risk_score"),
        func.coalesce(high_risk_expr(), 0).label("high_risk_count"),
        func.coalesce(medium_risk_expr(), 0).label("medium_risk_count"),
        func.coalesce(reviewed_expr(), 0).label("reviewed_count"),
    ).filter(match).first()

    if not agg or int(agg.works_count or 0) == 0:
        return None

    tier_rows = (
        db.query(Work.risk_tier, func.count(Work.work_id))
        .filter(match).group_by(Work.risk_tier).all()
    )
    tier_distribution = {HIGH_RISK_TIER: 0, MEDIUM_RISK_TIER: 0, LOW_RISK_TIER: 0}
    for tier, cnt in tier_rows:
        if tier in tier_distribution:
            tier_distribution[tier] = int(cnt)

    mp_rows = (
        db.query(
            Work.mp_name.label("name"),
            func.count(Work.work_id).label("works_count"),
            func.coalesce(func.sum(Work.sanction_amount), 0.0).label("total_sanctioned"),
            func.coalesce(func.sum(Work.total_fund_disbursed), 0.0).label("total_disbursed"),
            func.coalesce(func.avg(Work.utilization_ratio), 0.0).label("avg_utilization"),
            func.coalesce(func.avg(Work.final_risk_score), 0.0).label("avg_risk_score"),
            func.coalesce(func.max(Work.final_risk_score), 0.0).label("max_risk_score"),
            func.coalesce(high_risk_expr(), 0).label("high_risk_count"),
            func.coalesce(medium_risk_expr(), 0).label("medium_risk_count"),
            func.coalesce(reviewed_expr(), 0).label("reviewed_count"),
            func.max(Work.constituency).label("constituency"),
        )
        .filter(match, Work.mp_name.isnot(None), Work.mp_name != "")
        .group_by(Work.mp_name)
        .order_by(desc(high_risk_expr()), desc(func.sum(Work.sanction_amount)))
        .limit(12)
        .all()
    )

    def _breakdown(column, limit):
        rows = (
            db.query(
                column.label("name"),
                func.count(Work.work_id).label("count"),
                func.coalesce(func.sum(Work.sanction_amount), 0.0).label("total_sanctioned"),
                func.coalesce(func.sum(Work.total_fund_disbursed), 0.0).label("total_disbursed"),
                func.coalesce(func.avg(Work.final_risk_score), 0.0).label("avg_risk_score"),
                func.coalesce(high_risk_expr(), 0).label("high_risk_count"),
            )
            .filter(match, column.isnot(None), column != "")
            .group_by(column)
            .order_by(desc(func.sum(Work.sanction_amount)))
            .limit(limit)
            .all()
        )
        return [
            {
                "name": r.name,
                "count": int(r.count),
                "total_sanctioned": round(float(r.total_sanctioned), 2),
                "total_disbursed": round(float(r.total_disbursed), 2),
                "avg_risk_score": round(float(r.avg_risk_score), 1),
                "high_risk_count": int(r.high_risk_count),
            }
            for r in rows
        ]

    return {
        "state": state.strip(),
        "works_count": int(agg.works_count),
        "mp_count": int(agg.mp_count or 0),
        "total_sanctioned": round(float(agg.total_sanctioned), 2),
        "total_disbursed": round(float(agg.total_disbursed), 2),
        "avg_utilization": round(float(agg.avg_utilization), 4),
        "avg_risk_score": round(float(agg.avg_risk_score), 1),
        "high_risk_count": int(agg.high_risk_count),
        "medium_risk_count": int(agg.medium_risk_count),
        "low_risk_count": tier_distribution[LOW_RISK_TIER],
        "reviewed_count": int(agg.reviewed_count),
        "tier_distribution": tier_distribution,
        "top_mps": [
            {
                "mp_name": r.name,
                "constituency": r.constituency,
                "state": state.strip(),
                "works_count": int(r.works_count),
                "total_sanctioned": round(float(r.total_sanctioned), 2),
                "total_disbursed": round(float(r.total_disbursed), 2),
                "avg_utilization": round(float(r.avg_utilization), 4),
                "avg_risk_score": round(float(r.avg_risk_score), 1),
                "max_risk_score": round(float(r.max_risk_score), 1),
                "high_risk_count": int(r.high_risk_count),
                "medium_risk_count": int(r.medium_risk_count),
                "reviewed_count": int(r.reviewed_count),
            }
            for r in mp_rows
        ],
        "category_breakdown": _breakdown(Work.work_category, 12),
        "agency_breakdown": _breakdown(Work.ida, 8),
    }


# ------------------------------------------------------------------------------
# Portfolio-wide analytics for chart widgets
# ------------------------------------------------------------------------------

def get_category_analytics(db: Session, house: Optional[str] = None) -> list:
    """Fund share & risk per work category (for the overview charts)."""
    rows = apply_house(db.query(
            Work.work_category.label("name"),
            func.count(Work.work_id).label("count"),
            func.coalesce(func.sum(Work.sanction_amount), 0.0).label("total_sanctioned"),
            func.coalesce(func.sum(Work.total_fund_disbursed), 0.0).label("total_disbursed"),
            func.coalesce(func.avg(Work.final_risk_score), 0.0).label("avg_risk_score"),
            func.coalesce(high_risk_expr(), 0).label("high_risk_count"),
        ), house) \
        .filter(Work.work_category.isnot(None), Work.work_category != "") \
        .group_by(Work.work_category) \
        .order_by(desc(func.sum(Work.sanction_amount))) \
        .all()
    total_sanctioned = sum(float(r.total_sanctioned) for r in rows) or 1.0
    return [
        {
            "name": r.name,
            "count": int(r.count),
            "total_sanctioned": round(float(r.total_sanctioned), 2),
            "total_disbursed": round(float(r.total_disbursed), 2),
            "avg_risk_score": round(float(r.avg_risk_score), 1),
            "high_risk_count": int(r.high_risk_count),
            "sanctioned_share": round(float(r.total_sanctioned) / total_sanctioned, 4),
        }
        for r in rows
    ]


def get_status_analytics(db: Session, house: Optional[str] = None) -> list:
    """Execution status distribution (completed / ongoing / etc.)."""
    rows = apply_house(db.query(
            Work.work_status.label("name"),
            func.count(Work.work_id).label("count"),
            func.coalesce(func.sum(Work.sanction_amount), 0.0).label("total_sanctioned"),
            func.coalesce(func.avg(Work.final_risk_score), 0.0).label("avg_risk_score"),
        ), house) \
        .filter(Work.work_status.isnot(None), Work.work_status != "") \
        .group_by(Work.work_status) \
        .order_by(desc(func.count(Work.work_id))) \
        .all()
    total = sum(int(r.count) for r in rows) or 1
    return [
        {
            "name": r.name,
            "count": int(r.count),
            "share": round(int(r.count) / total, 4),
            "total_sanctioned": round(float(r.total_sanctioned), 2),
            "avg_risk_score": round(float(r.avg_risk_score), 1),
        }
        for r in rows
    ]


# ------------------------------------------------------------------------------
# Open-data CSV export
# ------------------------------------------------------------------------------

EXPORT_COLUMNS = [
    "work_id", "mp_name", "state", "constituency", "ida", "primary_vendor",
    "work_category", "work_type", "sanction_amount", "total_fund_disbursed",
    "utilization_ratio", "work_status", "completion_date", "final_risk_score",
    "priority_rank", "risk_tier", "recommended_action", "rule_flag_count",
    "human_review_outcome",
]


def stream_works_csv(query: Query, row_limit: int = 50000) -> Generator[str, None, None]:
    """Stream filtered works as CSV rows; never materializes the full dataset."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    yield buf.getvalue()

    yielded = 0
    for w in query.yield_per(500):
        buf.seek(0)
        buf.truncate(0)
        writer.writerow([
            getattr(w, col) if getattr(w, col) is not None else ""
            for col in EXPORT_COLUMNS
        ])
        yield buf.getvalue()
        yielded += 1
        if yielded >= row_limit:
            break
