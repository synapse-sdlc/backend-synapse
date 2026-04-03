import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

from app.db import engine, Base
# Import all models so Base.metadata.create_all() creates their tables
import app.models.org  # noqa: F401
import app.models.user  # noqa: F401
import app.models.project  # noqa: F401
import app.models.feature  # noqa: F401
import app.models.artifact  # noqa: F401
import app.models.message  # noqa: F401
import app.models.repository  # noqa: F401
import app.models.jira_config  # noqa: F401
import app.models.jira_issue_link  # noqa: F401
import app.models.pr_link  # noqa: F401
import app.models.knowledge_entry  # noqa: F401
import app.models.api_contract  # noqa: F401
import app.models.shared_model  # noqa: F401

from app.api import auth, projects, features, artifacts, stream, health, repositories
from app.api import jira, pull_requests, knowledge, skills


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate required settings at startup
    from app.config import settings
    if settings.jwt_secret == "CHANGE-ME-IN-ENV-FILE":
        logging.getLogger(__name__).critical("JWT_SECRET not set! Set it in .env or environment.")
    if settings.encryption_key == "CHANGE-ME-IN-ENV-FILE":
        logging.getLogger(__name__).critical("ENCRYPTION_KEY not set! Set it in .env or environment.")

    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()


app = FastAPI(title="Synapse API", version="0.1.0", lifespan=lifespan)

# CORS origins from config (comma-separated string → list)
from app.config import settings as _settings
_cors_origins = [o.strip() for o in _settings.cors_allowed_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(projects.router, prefix="/api", tags=["projects"])
app.include_router(features.router, prefix="/api", tags=["features"])
app.include_router(artifacts.router, prefix="/api", tags=["artifacts"])
app.include_router(repositories.router, prefix="/api", tags=["repositories"])
app.include_router(jira.router, prefix="/api", tags=["jira"])
app.include_router(pull_requests.router, prefix="/api", tags=["pull-requests"])
app.include_router(knowledge.router, prefix="/api", tags=["knowledge"])
app.include_router(skills.router, prefix="/api", tags=["skills"])
app.include_router(stream.router, prefix="/api", tags=["stream"])
