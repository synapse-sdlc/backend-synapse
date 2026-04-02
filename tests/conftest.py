"""Test fixtures using the Docker PostgreSQL instance."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.db import Base, get_db
from app.deps import get_current_user, CurrentUser
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


# Default test user context (used when tests don't need real auth)
import uuid
TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
TEST_USER = CurrentUser(id=TEST_USER_ID, org_id=TEST_ORG_ID, role="admin")


@pytest.fixture
def client(db_session):
    """FastAPI test client with overridden DB and auth dependencies.

    All protected endpoints get a fake admin user by default.
    Tests that need real auth (signup/login) should use `unauthed_client`.
    """
    from app.models.org import Org

    # Create the test org in DB (needed for FK constraints)
    org = Org(id=TEST_ORG_ID, name="Test Org")
    db_session.merge(org)
    db_session.commit()

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    def override_auth():
        return TEST_USER

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_auth
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def unauthed_client(db_session):
    """FastAPI test client WITHOUT auth override. For testing signup/login."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
