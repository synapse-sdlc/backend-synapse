"""Test fixtures using the Docker PostgreSQL instance."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.db import Base, get_db
from app.main import app

# Use the Docker postgres (same as dev, but we could use a test DB)
TEST_DB_URL = "postgresql://synapse:synapse@localhost:5433/synapse"

sync_engine = create_engine(TEST_DB_URL)


@pytest.fixture(autouse=True)
def setup_db():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=sync_engine)
    yield
    Base.metadata.drop_all(bind=sync_engine)


@pytest.fixture
def db_session():
    """Provide a sync DB session for tests."""
    session = Session(sync_engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db_session):
    """FastAPI test client with overridden DB dependency."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
