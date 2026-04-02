from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# Use sync engine (simpler, works with TestClient, Celery, and FastAPI sync endpoints)
sync_url = settings.database_url.replace("+asyncpg", "")
engine = create_engine(sync_url, echo=False)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
