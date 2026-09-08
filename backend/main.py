from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, List
import threading
import pandas as pd
from fastapi import FastAPI, Depends, Query, HTTPException, status, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, desc, asc, case
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db, engine, Base, run_lightweight_migrations
from backend.models import Work, ReviewLog, SyncLog, MPAllocation
from backend.schemas import (
    WorkListItem, WorkPaginationResponse, CasePacketResponse,
    ReviewCreateRequest, ReviewResponse, StatsOverviewResponse,
    EntityRiskStat, SyncLogResponse, MPDirectoryItem,
    EntityDirectoryResponse, MPProfileResponse, StateProfileResponse,
    BreakdownStat, CategoryStat, StatusStat, HealthResponse
)
from backend.auth import (
    get_current_role, require_reviewer_role, require_mospi_admin_role,
    ROLE_MOSPI_REVIEWER, ROLE_PUBLIC_TIER
)
from backend.seeder import seed_database
from backend.services.ingestion import run_ingestion, get_sync_status, VALID_MODES
from backend.services import analytics
from model.risk_engine import generate_case_packet, load_models, RULE_DESCRIPTIONS
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BackgroundScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure tables exist, seed database if empty, and start scheduler
    Base.metadata.create_all(bind=engine)
    run_lightweight_migrations()
    load_models(settings.MODEL_DIR)
    seed_database()

    # Nightly live sync at 03:00 Indian Standard Time
    scheduler.add_job(
        run_ingestion,
        CronTrigger(hour=3, minute=0, timezone="Asia/Kolkata"),
        kwargs={"mode": "live"},
        id="nightly_mplads_sync",
        replace_existing=True,
    )
    scheduler.start()
    print("APScheduler started: nightly MPLADS sync at 03:00 IST.")

    yield

    # Shutdown
    scheduler.shutdown()
    print("APScheduler shut down.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="MoSPI (SIH26102) MPLADS AI Sentinel — Audit & Anomaly Prioritization Platform",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


# ==============================================================================
# 1. GET /works — Priority Queue & Filtered List
# ==============================================================================
@app.get("/works", response_model=WorkPaginationResponse)
@app.get("/api/works", response_model=WorkPaginationResponse)
def get_works(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    state: Optional[str] = Query(None, description="Filter by State"),
    mp_name: Optional[str] = Query(None, description="Filter by MP Name"),
    house: Optional[str] = Query(None, description="Filter by House: 'Lok Sabha' or 'Rajya Sabha'"),
    ida: Optional[str] = Query(None, description="Filter by Implementing Agency"),
    risk_tier: Optional[str] = Query(None, description="Filter by Risk Tier"),
    work_category: Optional[str] = Query(None, description="Filter by Work Category"),
    work_status: Optional[str] = Query(None, description="Filter by Execution Status"),
    search: Optional[str] = Query(None, description="Search by Work ID, vendor or description"),
    sort_by: str = Query("priority_rank", description="Sort field"),
    order: str = Query("asc", description="Sort direction: 'asc' or 'desc'"),
    db: Session = Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """
    Returns a paginated list of works ordered by priority_rank (default ascending = highest priority first).
    Supports database-level filtering and sorting over 79k+ records.
    """
    if sort_by not in analytics.WORK_SORTABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort field '{sort_by}'. Allowed: {sorted(analytics.WORK_SORTABLE_FIELDS)}"
        )
    if order.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'.")

    query = analytics.apply_work_filters(
        db.query(Work),
        state=state, mp_name=mp_name, house=house, ida=ida, risk_tier=risk_tier,
        work_category=work_category, work_status=work_status, search=search,
    )

    total = query.count()

    sort_col = getattr(Work, sort_by)
    if order.lower() == "desc":
        query = query.order_by(desc(sort_col), Work.work_id.asc())
    else:
        query = query.order_by(asc(sort_col), Work.work_id.asc())

    offset = (page - 1) * page_size
    items_raw = query.offset(offset).limit(page_size).all()

    items = []
    for w in items_raw:
        item = analytics.work_to_list_item(w)
        flags = item["rule_flags_triggered"]
        causes = [RULE_DESCRIPTIONS.get(f, f"Flag triggered: {f}") for f in flags]
        if w.is_anomaly:
            causes.append("Statistical outlier detected by Isolation Forest.")
        item["causes"] = causes
        items.append(WorkListItem(**item))

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return WorkPaginationResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=items
    )


# ==============================================================================
# 1b. GET /api/export/works — Open-data CSV export (transparency feature)
# ==============================================================================
@app.get("/api/export/works")
def export_works_csv(
    state: Optional[str] = Query(None),
    mp_name: Optional[str] = Query(None),
    house: Optional[str] = Query(None),
    ida: Optional[str] = Query(None),
    risk_tier: Optional[str] = Query(None),
    work_category: Optional[str] = Query(None),
    work_status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    row_limit: int = Query(50000, ge=1, le=100000),
    db: Session = Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """
    Streams the filtered works list as a downloadable CSV (up to row_limit rows).
    Open-data companion to the /works endpoint — same filters, machine-readable.
    """
    query = analytics.apply_work_filters(
        db.query(Work).order_by(Work.priority_rank.asc()),
        state=state, mp_name=mp_name, house=house, ida=ida, risk_tier=risk_tier,
        work_category=work_category, work_status=work_status, search=search,
    )
    filename = f"mplads_works_export_{datetime.now(timezone.utc):%Y%m%d}.csv"
    return StreamingResponse(
        analytics.stream_works_csv(query, row_limit=row_limit),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Row-Limit": str(row_limit),
        }
    )


# ==============================================================================
# 2. GET /works/{work_id} — Case Packet Detail View
# ==============================================================================
@app.get("/works/{work_id:path}", response_model=CasePacketResponse)
@app.get("/api/works/{work_id:path}", response_model=CasePacketResponse)
def get_work_case_packet(
    work_id: str,
    db: Session = Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """
    Returns the complete case packet for the requested Work ID using risk_engine.generate_case_packet().
    """
    work = db.query(Work).filter(Work.work_id == work_id.strip()).first()
    if not work:
        raise HTTPException(status_code=404, detail=f"Work ID '{work_id}' not found.")

    # Convert row to dict for authoritative risk_engine function
    work_dict = {
        'work_id': work.work_id,
        'mp_name': work.mp_name,
        'state': work.state,
        'constituency': work.constituency,
        'ida': work.ida,
        'primary_vendor': work.primary_vendor,
        'work_category': work.work_category,
        'work_type': work.work_type,
        'sanction_amount': work.sanction_amount,
        'total_fund_disbursed': work.total_fund_disbursed,
        'utilization_ratio': work.utilization_ratio,
        'work_status': work.work_status,
        'completion_date': work.completion_date,
        'final_risk_score': work.final_risk_score,
        'priority_rank': work.priority_rank,
        'risk_tier': work.risk_tier,
        'recommended_action': work.recommended_action,
        'rule_flag_count': work.rule_flag_count,
        'rule_flags_triggered': work.rule_flags_triggered,
        'likelihood_score': work.likelihood_score,
        'impact_score': work.impact_score,
        'weighted_rule_score': work.weighted_rule_score,
        'anomaly_percentile': work.anomaly_percentile,
        'is_anomaly': work.is_anomaly,
        'cost_mad_score': work.cost_mad_score,
        'vendor_share_in_state': work.vendor_share_in_state,
        'disbursement_mismatch_ratio': work.disbursement_mismatch_ratio,
        'days_since_sanction': work.days_since_sanction,
        'n_distinct_vendors': work.n_distinct_vendors,
        'n_vendor_payments': work.n_vendor_payments,
        'human_review_outcome': work.human_review_outcome,
    }

    packet = generate_case_packet(work_id, work_row=work_dict)

    # Fetch prior reviews for this work
    prior_reviews = db.query(ReviewLog).filter(ReviewLog.work_id == work_id).order_by(ReviewLog.created_at.desc()).all()
    packet['prior_reviews'] = [
        {
            'id': r.id,
            'reviewer_name': r.reviewer_name,
            'reviewer_role': r.reviewer_role,
            'outcome': r.outcome,
            'notes': r.notes if user_role != ROLE_PUBLIC_TIER else None,
            'created_at': r.created_at.isoformat()
        }
        for r in prior_reviews
    ]

    return CasePacketResponse(**packet)


# ==============================================================================
# 3. MP & State Transparency Directories (citizen-facing aggregate views)
# ==============================================================================
@app.get("/api/mps", response_model=EntityDirectoryResponse)
def get_mp_directory(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    state: Optional[str] = Query(None, description="Filter MPs by State"),
    house: Optional[str] = Query(None, description="Filter by House: 'Lok Sabha' or 'Rajya Sabha'"),
    search: Optional[str] = Query(None, description="Search by MP or constituency name"),
    sort_by: str = Query("total_sanctioned", description="Aggregate sort key"),
    order: str = Query("desc"),
    db: Session = Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """MP-wise directory: sanctioned/disbursed totals, utilization, risk profile."""
    if sort_by not in analytics.DIRECTORY_SORTABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort field '{sort_by}'. Allowed: {sorted(analytics.DIRECTORY_SORTABLE_FIELDS)}"
        )
    if order.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'.")
    return analytics.get_mp_directory(
        db, page=page, page_size=page_size, search=search, state=state,
        house=house, sort_by=sort_by, order=order,
    )


@app.get("/api/mps/{mp_name}", response_model=MPProfileResponse)
def get_mp_profile(
    mp_name: str,
    house: Optional[str] = Query(None, description="Filter by House: 'Lok Sabha' or 'Rajya Sabha'"),
    db: Session = Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """Full public dossier for one MP: funds, risk tiers, categories, vendors, top works."""
    profile = analytics.get_mp_profile(db, mp_name, house=house)
    if not profile:
        raise HTTPException(status_code=404, detail=f"No works found for MP '{mp_name}'.")
    return profile


@app.get("/api/states", response_model=EntityDirectoryResponse)
def get_state_directory(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    house: Optional[str] = Query(None, description="Filter by House"),
    sort_by: str = Query("total_sanctioned"),
    order: str = Query("desc"),
    db: Session = Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """State-wise directory: funds, MP coverage and risk concentration."""
    if sort_by not in analytics.DIRECTORY_SORTABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort field '{sort_by}'. Allowed: {sorted(analytics.DIRECTORY_SORTABLE_FIELDS)}"
        )
    if order.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'.")
    return analytics.get_state_directory(db, page=page, page_size=page_size, house=house, sort_by=sort_by, order=order)


@app.get("/api/states/{state}", response_model=StateProfileResponse)
def get_state_profile(
    state: str,
    db: Session = Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """State dossier: tier spread, top MPs, agencies and category splits."""
    profile = analytics.get_state_profile(db, state)
    if not profile:
        raise HTTPException(status_code=404, detail=f"No works found for state '{state}'.")
    return profile


# ==============================================================================
# 3b. Chart analytics — category & execution-status aggregations
# ==============================================================================
@app.get("/api/analytics/categories", response_model=List[CategoryStat])
def get_category_analytics(
    house: Optional[str] = Query(None, description="Filter by House"),
    db: Session = Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """Fund share, disbursed value and risk per work category."""
    return analytics.get_category_analytics(db, house=house)


@app.get("/api/analytics/status", response_model=List[StatusStat])
def get_status_analytics(
    house: Optional[str] = Query(None, description="Filter by House"),
    db: Session = Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """Execution status distribution with average risk per status."""
    return analytics.get_status_analytics(db, house=house)


# ==============================================================================
# 4. GET /stats/overview — Portfolio Statistics & Sync Health
# ==============================================================================
@app.get("/stats/overview", response_model=StatsOverviewResponse)
@app.get("/api/stats/overview", response_model=StatsOverviewResponse)
def get_stats_overview(
    house: Optional[str] = Query(None, description="Filter by House: 'Lok Sabha' or 'Rajya Sabha'"),
    db: Session = Depends(get_db)
):
    """
    Returns portfolio-level statistics including risk tier counts, top-risk MPs,
    top-risk states, top-risk vendors, and sync health / staleness status.
    """
    def scoped(query):
        return analytics.apply_house(query, house) if house else query

    total_works = scoped(db.query(func.count(Work.work_id))).scalar() or 0
    high_risk_count = scoped(db.query(func.count(Work.work_id))).filter(Work.risk_tier == 'High Risk - Review').scalar() or 0
    medium_risk_count = scoped(db.query(func.count(Work.work_id))).filter(Work.risk_tier == 'Medium Risk - Monitor').scalar() or 0
    low_risk_count = scoped(db.query(func.count(Work.work_id))).filter(Work.risk_tier == 'Low Risk').scalar() or 0

    total_sanctioned = scoped(db.query(func.sum(Work.sanction_amount))).scalar() or 0.0
    total_disbursed = scoped(db.query(func.sum(Work.total_fund_disbursed))).scalar() or 0.0
    avg_utilization = scoped(db.query(func.avg(Work.utilization_ratio))).scalar() or 0.0
    avg_risk = scoped(db.query(func.avg(Work.final_risk_score))).scalar() or 0.0
    reviewed_count = scoped(db.query(func.count(Work.work_id))).filter(Work.human_review_outcome.isnot(None)).scalar() or 0

    # Portal allocation ledger (Total Allocated / utilization / ongoing payments).
    # Filter MPAllocation.house directly — applying Work.house here would add
    # `works` to FROM with no join and cartesian-inflate the ceiling.
    alloc_q = db.query(func.sum(MPAllocation.allocated_amount))
    if house:
        alloc_q = alloc_q.filter(MPAllocation.house == house.strip())
    total_allocated = alloc_q.scalar() or 0.0
    completed_q = scoped(db.query(func.count(Work.work_id))).filter(
        Work.completion_date.isnot(None), Work.completion_date != "")
    works_completed = completed_q.scalar() or 0
    works_pending = max(0, total_works - works_completed)

    # Ongoing work payments: sum of disbursed funds for works that are NOT yet completed
    ongoing_payments = scoped(db.query(func.sum(Work.total_fund_disbursed))).filter(
        (Work.completion_date.is_(None) | (Work.completion_date == "")),
        Work.total_fund_disbursed > 0
    ).scalar() or 0.0

    tier_dist = {
        'High Risk - Review': high_risk_count,
        'Medium Risk - Monitor': medium_risk_count,
        'Low Risk': low_risk_count,
    }

    # Top risk states
    top_states_raw = (
        scoped(db.query(
            Work.state,
            func.count(Work.work_id).label("count"),
            func.avg(Work.final_risk_score).label("avg_score"),
            func.sum(case((Work.risk_tier == 'High Risk - Review', 1), else_=0)).label("high_count"),
            func.sum(Work.sanction_amount).label("total_sanctioned")
        ))
        .filter(Work.state.isnot(None))
        .group_by(Work.state)
        .order_by(desc("high_count"), desc("avg_score"))
        .limit(8)
        .all()
    )
    top_states = [
        EntityRiskStat(
            name=s[0],
            count=s[1],
            avg_risk_score=round(float(s[2] or 0), 1),
            high_risk_count=int(s[3] or 0),
            total_sanctioned=round(float(s[4] or 0), 2)
        )
        for s in top_states_raw
    ]

    # Top risk MPs
    top_mps_raw = (
        scoped(db.query(
            Work.mp_name,
            func.count(Work.work_id).label("count"),
            func.avg(Work.final_risk_score).label("avg_score"),
            func.sum(case((Work.risk_tier == 'High Risk - Review', 1), else_=0)).label("high_count"),
            func.sum(Work.sanction_amount).label("total_sanctioned")
        ))
        .filter(Work.mp_name.isnot(None))
        .group_by(Work.mp_name)
        .order_by(desc("high_count"), desc("avg_score"))
        .limit(8)
        .all()
    )
    top_mps = [
        EntityRiskStat(
            name=m[0],
            count=m[1],
            avg_risk_score=round(float(m[2] or 0), 1),
            high_risk_count=int(m[3] or 0),
            total_sanctioned=round(float(m[4] or 0), 2)
        )
        for m in top_mps_raw
    ]

    # Top risk vendors
    top_vendors_raw = (
        scoped(db.query(
            Work.primary_vendor,
            func.count(Work.work_id).label("count"),
            func.avg(Work.final_risk_score).label("avg_score"),
            func.sum(case((Work.risk_tier == 'High Risk - Review', 1), else_=0)).label("high_count"),
            func.sum(Work.sanction_amount).label("total_sanctioned")
        ))
        .filter(Work.primary_vendor.isnot(None))
        .group_by(Work.primary_vendor)
        .order_by(desc("high_count"), desc("avg_score"))
        .limit(8)
    )
    top_vendors = [
        EntityRiskStat(
            name=v[0],
            count=v[1],
            avg_risk_score=round(float(v[2] or 0), 1),
            high_risk_count=int(v[3] or 0),
            total_sanctioned=round(float(v[4] or 0), 2)
        )
        for v in top_vendors_raw
    ]

    # Sync freshness
    sync_info = get_sync_status()

    return StatsOverviewResponse(
        total_works=total_works,
        high_risk_count=high_risk_count,
        medium_risk_count=medium_risk_count,
        low_risk_count=low_risk_count,
        tier_distribution=tier_dist,
        total_sanctioned_amount=round(float(total_sanctioned), 2),
        total_disbursed_amount=round(float(total_disbursed), 2),
        total_allocated_amount=round(float(total_allocated), 2),
        fund_utilization_pct=round(float(total_sanctioned) / float(total_allocated) * 100, 1) if total_allocated else 0.0,
        expenditure_rate_pct=round(float(total_disbursed) / float(total_allocated) * 100, 1) if total_allocated else 0.0,
        works_completed=int(works_completed),
        works_pending=int(works_pending),
        ongoing_work_payments=round(float(ongoing_payments), 2),
        avg_utilization_pct=round(float(avg_utilization) * 100, 1),
        avg_risk_score=round(float(avg_risk), 1),
        reviewed_works=int(reviewed_count),
        top_risk_mps=top_mps,
        top_risk_states=top_states,
        top_risk_vendors=top_vendors,
        latest_sync_timestamp=sync_info["latest_sync_timestamp"],
        latest_sync_status=sync_info["latest_sync_status"],
        is_data_stale=sync_info["is_data_stale"],
        staleness_message=sync_info["staleness_message"]
    )


# ==============================================================================
# 5. POST /works/{work_id}/review — Human Review Feedback Loop
# ==============================================================================
@app.post("/works/{work_id:path}/review", response_model=ReviewResponse)
@app.post("/api/works/{work_id:path}/review", response_model=ReviewResponse)
def record_human_review(
    work_id: str,
    payload: ReviewCreateRequest,
    db: Session = Depends(get_db),
    user_role: str = Depends(require_reviewer_role)
):
    """
    Phase 5 feedback loop hook: Record a formal human review outcome.
    Allowed outcomes: 'legitimate', 'data-quality issue', 'irregularity', 'confirmed fraud'.
    """
    valid_outcomes = {'legitimate', 'data-quality issue', 'irregularity', 'confirmed fraud'}
    norm_outcome = payload.outcome.strip().lower()
    if norm_outcome not in valid_outcomes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid review outcome '{payload.outcome}'. Must be one of: {list(valid_outcomes)}"
        )

    work = db.query(Work).filter(Work.work_id == work_id.strip()).first()
    if not work:
        raise HTTPException(status_code=404, detail=f"Work ID '{work_id}' not found.")

    reviewer_name = payload.reviewer_name or user_role
    reviewer_role = payload.reviewer_role or user_role

    # Update work record
    work.human_review_outcome = norm_outcome
    work.updated_at = datetime.now(timezone.utc)

    # Add audit log entry
    review_log = ReviewLog(
        work_id=work_id.strip(),
        reviewer_name=reviewer_name,
        reviewer_role=reviewer_role,
        outcome=norm_outcome,
        notes=payload.notes,
        created_at=datetime.now(timezone.utc)
    )
    db.add(review_log)
    db.commit()
    db.refresh(review_log)

    return ReviewResponse(
        success=True,
        work_id=work_id,
        outcome=norm_outcome,
        reviewer_name=reviewer_name,
        reviewer_role=reviewer_role,
        notes=payload.notes,
        created_at=review_log.created_at
    )


# ==============================================================================
# 6. Filter options, Sync Health, Manual Trigger & Health
# ==============================================================================
@app.get("/api/filter-options")
def get_filter_options(
    house: Optional[str] = Query(None, description="Filter options by House"),
    db: Session = Depends(get_db)
):
    """Returns unique filter values for the frontend dropdowns."""
    scoped = lambda q: analytics.apply_house(q, house) if house else q
    states = [s[0] for s in scoped(db.query(Work.state)).distinct().order_by(Work.state).all() if s[0]]
    categories = [c[0] for c in scoped(db.query(Work.work_category)).distinct().order_by(Work.work_category).all() if c[0]]
    statuses = [s[0] for s in scoped(db.query(Work.work_status)).distinct().order_by(Work.work_status).all() if s[0]]
    mps = [
        m[0] for m in scoped(db.query(Work.mp_name)).distinct().order_by(Work.mp_name).all()
        if m[0]
    ]
    return {
        "states": states,
        "categories": categories,
        "statuses": statuses,
        "mps": mps,
        "risk_tiers": ["High Risk - Review", "Medium Risk - Monitor", "Low Risk"]
    }


@app.get("/sync/status")
@app.get("/api/sync/status")
def sync_status():
    return get_sync_status()


@app.get("/sync/logs", response_model=List[SyncLogResponse])
@app.get("/api/sync/logs", response_model=List[SyncLogResponse])
def get_sync_logs(
    limit: int = 20,
    db: Session = Depends(get_db)
):
    logs = db.query(SyncLog).order_by(SyncLog.run_timestamp.desc()).limit(limit).all()
    return logs


_sync_in_flight = threading.Lock()


@app.post("/sync/run")
@app.post("/api/sync/run")
def trigger_manual_sync(
    mode: str = Query("auto", description="Ingestion mode: auto | live"),
    user_role: str = Depends(require_mospi_admin_role)
):
    """
    Manually trigger the ingestion pipeline. Restricted to MoSPI Reviewers.

    Runs in the background — the full portal fetch + risk-score of the Lok
    Sabha dataset takes several minutes, so the request returns immediately.
    Progress lands in sync_logs and the /api/sync/status endpoint; the UI
    polls that banner.

    Modes:
      auto / live — pull directly from the live MPLADS dashboard API
                    (mplads.mospi.gov.in /digigov), risk-score, and upsert.
    """
    if mode not in VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ingestion mode '{mode}'. Must be one of {sorted(VALID_MODES)}"
        )
    if not _sync_in_flight.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="A sync is already running. Watch the status banner — the "
                   "new run will appear in the audit log when it finishes."
        )

    def _run():
        try:
            result = run_ingestion(mode=mode)
            print(f"Background sync finished: {result.get('status')} — "
                  f"{result.get('processed', 0)} records processed.")
        except Exception as e:
            print(f"Background sync crashed: {e}")
        finally:
            _sync_in_flight.release()

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "mode": mode,
            "message": "Live sync started in the background. "
                       "Progress appears in the audit log when it finishes."}


@app.get("/health")
@app.get("/api/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)):
    """Liveness probe: verifies API + database connectivity."""
    try:
        works_count = db.query(func.count(Work.work_id)).scalar() or 0
        db_status = "connected"
    except Exception:
        works_count = 0
        db_status = "unavailable"
    return HealthResponse(
        status="ok" if db_status == "connected" else "degraded",
        version=settings.VERSION,
        database=db_status,
        works_count=int(works_count),
        timestamp=datetime.now(timezone.utc).isoformat()
    )


# In the Hugging Face container, FastAPI serves the production React bundle so
# the API and dashboard share one public origin.
_frontend_dist = settings.DATA_DIR.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
