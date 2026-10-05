"""
backend/app/database.py
Database connection and session management for AgriGuard.

Dialect-agnostic via settings.database_url (see core/config.py) — defaults
to a local SQLite file for dev, override with DATABASE_URL for Postgres/
MySQL/etc. (This docstring previously said "connects SQLAlchemy to MySQL"
specifically; that hasn't been true since database_url became
settings-driven, and both prices.py and weather.py rely on the SQLite
default working out of the box.)
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

from backend.app.core.config import settings


# Create database engine
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,           # Helps detect dead connections
    pool_size=10,
    max_overflow=20,
    echo=settings.debug           # Set to True to see raw SQL queries (development only)
)


# Create session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


# Base class for all models to inherit from
Base = declarative_base()


# Dependency to get database session
def get_db():
    """
    FastAPI dependency that provides a database session.
    Automatically closes the session after use.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Optional: Function to create all tables (use during initial setup)
def create_tables():
    """
    Creates all database tables defined in the models.
    Call this once during initial project setup.
    """
    Base.metadata.create_all(bind=engine)
    print("All database tables created successfully.")


# Optional: Function to drop all tables (use with caution)
def drop_tables():
    """
    Drops all database tables.
    Useful during heavy development but dangerous in production.
    """
    Base.metadata.drop_all(bind=engine)
    print("All database tables dropped.")