from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, Boolean, Text, DateTime, ForeignKey, Index
from backend.database import Base

class Work(Base):
    __tablename__ = "works"

    work_id = Column(String(100), primary_key=True, index=True)
    mp_name = Column(String(200), index=True, nullable=True)
    state = Column(String(100), index=True, nullable=True)
    constituency = Column(String(200), nullable=True)
    house = Column(String(50), index=True, nullable=True)  # 'Lok Sabha' | 'Rajya Sabha'
    ida = Column(String(255), index=True, nullable=True)
    primary_vendor = Column(String(255), nullable=True)
    work_category = Column(String(100), index=True, nullable=True)
    work_type = Column(String(255), nullable=True)
    
    sanction_amount = Column(Float, default=0.0)
    total_fund_disbursed = Column(Float, default=0.0)
    utilization_ratio = Column(Float, default=0.0)
    work_status = Column(String(100), nullable=True)
    completion_date = Column(String(50), nullable=True)
    
    final_risk_score = Column(Float, index=True, default=0.0)
    priority_rank = Column(Integer, index=True, default=0)
    risk_tier = Column(String(50), index=True, default="Low Risk")
    recommended_action = Column(String(255), nullable=True)
    
    rule_flag_count = Column(Integer, default=0)
    rule_flags_triggered = Column(Text, default="[]")  # Serialized list of flags
    
    likelihood_score = Column(Float, default=0.0)
    impact_score = Column(Float, default=0.0)
    weighted_rule_score = Column(Float, default=0.0)
    anomaly_percentile = Column(Float, default=0.0)
    is_anomaly = Column(Boolean, default=False)
    
    cost_mad_score = Column(Float, default=0.0)
    peer_count = Column(Float, default=0.0)
    vendor_share_in_state = Column(Float, default=0.0)
    disbursement_mismatch_ratio = Column(Float, default=0.0)
    days_since_sanction = Column(Float, default=0.0)
    n_distinct_vendors = Column(Float, default=0.0)
    n_vendor_payments = Column(Float, default=0.0)
    
    human_review_outcome = Column(String(50), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_works_state_priority", "state", "priority_rank"),
        Index("ix_works_tier_priority", "risk_tier", "priority_rank"),
        Index("ix_works_mp_priority", "mp_name", "priority_rank"),
    )


class MPAllocation(Base):
    """Per-MP allotted funds from the portal's 'Allocated Limit' dataset
    (record_type='MP Allocated Limit' in the live feed)."""
    __tablename__ = "mp_allocations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mp_name = Column(String(200), index=True, nullable=False)
    state = Column(String(100), index=True, nullable=True)
    constituency = Column(String(200), nullable=True)
    house = Column(String(50), index=True, nullable=False)  # 'Lok Sabha' | 'Rajya Sabha'
    allocated_amount = Column(Float, default=0.0)
    tenure_start = Column(String(50), nullable=True)
    tenure_end = Column(String(50), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_mp_allocations_identity", "mp_name", "house", "constituency", "state", unique=True),
    )


class ReviewLog(Base):
    __tablename__ = "review_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    work_id = Column(String(100), ForeignKey("works.work_id"), index=True, nullable=False)
    reviewer_name = Column(String(100), default="MoSPI Reviewer")
    reviewer_role = Column(String(50), default="MoSPI Reviewer")
    outcome = Column(String(50), nullable=False)  # 'legitimate', 'data-quality issue', 'irregularity', 'confirmed fraud'
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, default=datetime.utcnow)
    status = Column(String(50), default="success")  # 'success', 'partial', 'failed'
    source = Column(String(255), default="MPLADS Master Feed")
    rows_fetched = Column(Integer, default=0)
    rows_processed = Column(Integer, default=0)
    rows_inserted = Column(Integer, default=0)
    rows_updated = Column(Integer, default=0)
    rows_rejected = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
