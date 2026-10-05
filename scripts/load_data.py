"""
database.py — AgriGuard Database Session Management
=====================================================
Creates the SQLAlchemy engine and session factory.
Provides the get_db() dependency used by every FastAPI route
that needs database access.

This file is the single place where the DB connection is configured.
Everything else just calls: db: Session = Depends(get_db)

Connection lifecycle per request:
  1. FastAPI calls get_db() before the route handler runs
  2. A new Session is yielded to the route handler
  3. Route handler does its work (reads/writes via the session)
  4. After the response is sent, the finally block closes the session
  5. The connection returns to the pool for the next request

Author: AgriGuard Team
"""

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import OperationalError
from typing import Generator

from app.config import settings


# =============================================================================
# ENGINE — the core connection pool
# =============================================================================

engine = create_engine(
    settings.database_url,

    # Connection pool settings from config
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,

    # Log every SQL statement when debug mode is on
    # Useful for catching N+1 queries during development
    echo=settings.db_echo_sql,

    # Recycle connections older than 30 minutes
    # Prevents "server closed the connection unexpectedly" errors
    # after periods of inactivity (common on cloud DBs)
    pool_recycle=1800,

    # Test connection health before using from pool
    # Returns a broken connection to the pool rather than crashing a request
    pool_pre_ping=True,
)


# =============================================================================
# SESSION FACTORY
# =============================================================================

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,   # We manage transactions manually (commit/rollback)
    autoflush=False,    # Don't flush to DB until we explicitly call commit()
                        # autoflush=False prevents surprise partial writes
    expire_on_commit=False,  # Keep ORM objects usable after commit()
                             # Without this, accessing price.crop after
                             # commit() would trigger a new DB query
)


# =============================================================================
# FastAPI DEPENDENCY — injected into every route that needs the DB
# =============================================================================

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides a database session per request.

    Usage in any router:
        from app.database import get_db

        @router.get("/something")
        def my_endpoint(db: Session = Depends(get_db)):
            results = db.query(MyModel).all()
            return results

    The try/finally ensures the session is always closed, even if
    the route handler raises an exception. This prevents connection leaks.

    The session is NOT committed here — that's the service layer's job.
    If a route raises an exception, the session is rolled back automatically
    when it closes (since we never called commit()).
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        # Roll back any partial writes if something went wrong
        db.rollback()
        raise
    finally:
        # Always close — returns the connection to the pool
        db.close()


# =============================================================================
# HEALTH CHECK HELPER
# =============================================================================

def check_db_connection() -> bool:
    """
    Tests whether the database is reachable.
    Called at startup and by GET /health.

    Returns True if connected, False if not.
    Never raises — the caller decides what to do with False.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False