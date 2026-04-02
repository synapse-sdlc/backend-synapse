from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import engine, Base
from app.api import projects, features, artifacts, stream, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()


app = FastAPI(title="Synapse API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(projects.router, prefix="/api", tags=["projects"])
app.include_router(features.router, prefix="/api", tags=["features"])
app.include_router(artifacts.router, prefix="/api", tags=["artifacts"])
app.include_router(stream.router, prefix="/api", tags=["stream"])
