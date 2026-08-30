"""FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from orynx import __version__
from orynx.api.routers import leads, sources
from orynx.db.base import init_db
from orynx.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    yield


app = FastAPI(
    title="Orynx BookLeads",
    version=__version__,
    description="Book and author lead extraction across publishing platforms.",
    lifespan=lifespan,
)
app.include_router(leads.router)
app.include_router(sources.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": __version__}
