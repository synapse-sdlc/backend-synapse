from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.config import settings

# Use sync engine (simpler, works with TestClient, Celery, and FastAPI sync endpoints)
sync_url = settings.database_url.replace("+asyncpg", "")
engine = create_engine(
    sync_url,
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # Verify connections before use (catches stale connections)
    pool_recycle=300,  # Recycle connections after 5 min
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
