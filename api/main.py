"""Agent Garden API server."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import agents, deploys, registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Agent Garden API starting up")
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
app.include_router(agents.router)
app.include_router(deploys.router)
app.include_router(registry.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "agent-garden-api", "version": "0.1.0"}
