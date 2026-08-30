"""Source discovery and ingest triggering."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from orynx.api.deps import get_session
from orynx.db.base import session_scope
from orynx.db.models import CrawlRun
from orynx.pipeline.run import run_pipeline
from orynx.pipeline.score import PROFILES
from orynx.sources.registry import get_registry

router = APIRouter(tags=["sources"])


class RunRequest(BaseModel):
    sources: list[str] = Field(default_factory=list)
    query: str | None = None
    limit: int = Field(200, ge=1, le=10000)
    profile: str = "services"
    with_contacts: bool = False


@router.get("/sources")
def list_sources() -> dict:
    return {
        "sources": [
            {
                "id": meta.id,
                "name": meta.name,
                "kind": meta.kind,
                "homepage": meta.homepage,
                "trust": meta.trust,
                "notes": meta.notes,
            }
            for meta in get_registry().describe()
        ]
    }


@router.get("/profiles")
def list_profiles() -> dict:
    return {
        name: {
            "weights": profile.weights,
            "recency_horizon_months": profile.recency_horizon_months,
        }
        for name, profile in PROFILES.items()
    }


async def _ingest(request: RunRequest) -> None:
    with session_scope() as session:
        await run_pipeline(
            session,
            request.sources,
            query=request.query,
            limit=request.limit,
            profile=request.profile,
            with_contacts=request.with_contacts,
        )


@router.post("/runs", status_code=202)
def start_run(request: RunRequest, background: BackgroundTasks) -> dict:
    registry = get_registry()
    source_ids = request.sources or registry.ids()
    unknown = [s for s in source_ids if s not in registry.ids(enabled_only=False)]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown sources: {unknown}")
    if request.profile not in PROFILES:
        raise HTTPException(status_code=422, detail=f"unknown profile {request.profile!r}")

    request.sources = source_ids
    background.add_task(_ingest, request)
    return {"status": "accepted", "sources": source_ids}


@router.get("/runs")
def list_runs(limit: int = 20, session: Session = Depends(get_session)) -> dict:
    runs = session.scalars(
        select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(limit)
    ).all()
    return {
        "runs": [
            {
                "id": run.id,
                "source_id": run.source_id,
                "status": run.status,
                "params": run.params,
                "stats": run.stats,
                "error": run.error,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
            for run in runs
        ]
    }
