"""Agent Garden API server."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import (
    a2a,
    agents,
    audit,
    auth,
    builders,
    costs,
    deploys,
    evals,
    git,
    marketplace,
    mcp_servers,
    memory,
    orchestrations,
    playground,
    prompts,
    providers,
    rag,
    registry,
    sandbox,
    teams,
    templates,
    tracing,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _seed_default_admin() -> None:
    """Create a default admin user (admin@garden.local / admin) if no users exist."""
    from api.database import async_session
    from api.models.enums import UserRole
    from api.services.auth import create_user, get_user_by_email

    async with async_session() as db:
        try:
            existing = await get_user_by_email(db, "admin@agent-garden.local")
            if existing:
                return
            await create_user(
                db,
                email="admin@agent-garden.local",
                name="Gardner",
                password="plant",
                team="Agent Garden Platform",
                role=UserRole.admin,
            )
            await db.commit()
            logger.info("Default admin user created (admin@agent-garden.local / plant)")
        except Exception as e:
            logger.debug("Skipping admin seed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Agent Garden API starting up")
    await _seed_default_admin()
    yield
    logger.info("Agent Garden API shutting down")


app = FastAPI(
    title="Agent Garden API",
    description="Define Once. Deploy Anywhere. Govern Automatically.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(builders.router)
app.include_router(deploys.router)
app.include_router(prompts.router)
app.include_router(providers.router)
app.include_router(mcp_servers.router)
app.include_router(registry.router)
app.include_router(sandbox.router)
app.include_router(git.router)
app.include_router(memory.router)
app.include_router(rag.router)
app.include_router(playground.router)
app.include_router(tracing.router)
app.include_router(teams.router)
app.include_router(costs.router)
app.include_router(audit.router)
app.include_router(evals.router)
app.include_router(orchestrations.router)
app.include_router(a2a.router)
app.include_router(templates.router)
app.include_router(marketplace.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "agent-garden-api", "version": "0.1.0"}
