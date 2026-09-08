from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from backend.config import settings

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False, "timeout": 30}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        # WAL lets readers and the sync writer work concurrently; busy_timeout
        # makes any residual lock contention wait instead of erroring.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def run_lightweight_migrations() -> None:
    """Schema additions that Base.metadata.create_all cannot apply to
    existing tables (it only creates missing tables, never new columns)."""
    inspector = inspect(engine)
    if "works" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("works")}

    if "house" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE works ADD COLUMN house VARCHAR(50)"))
            conn.execute(text("CREATE INDEX ix_works_house ON works (house)"))
        print("Migration: added works.house column")

    # Backfill house for rows ingested before the column existed. Live data is
    # tagged at ingestion; historical rows are inferred from the constituency
    # (Rajya Sabha MPs sit for 'Sitting Rajya Sabha', everything else is a
    # Lok Sabha constituency).
    with engine.begin() as conn:
        result = conn.execute(text(
            "UPDATE works SET house = CASE "
            "WHEN lower(constituency) LIKE '%rajya sabha%' THEN 'Rajya Sabha' "
            "ELSE 'Lok Sabha' END WHERE house IS NULL"
        ))
        if result.rowcount:
            print(f"Migration: backfilled house on {result.rowcount} works rows")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
