"""Lead browsing and export endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orynx.api.deps import get_session
from orynx.config import get_settings
from orynx.db.models import LEAD_NEW, Lead
from orynx.export.builder import build_rows
from orynx.export.csv_export import write_csv
from orynx.export.xlsx_export import write_xlsx

router = APIRouter(prefix="/leads", tags=["leads"])

VALID_STATUSES = {"new", "qualified", "contacted", "rejected", "suppressed"}


@router.get("")
def list_leads(
    min_score: float = Query(0.0, ge=0, le=100),
    tier: list[str] | None = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    require_contact: bool = False,
    session: Session = Depends(get_session),
) -> dict:
    rows, suppressed = build_rows(
        session,
        min_score=min_score,
        tiers=tier,
        limit=limit,
        require_contact=require_contact,
    )
    return {
        "count": len(rows),
        "suppressed": suppressed,
        "leads": [row.as_dict() for row in rows],
    }


@router.get("/summary")
def summary(session: Session = Depends(get_session)) -> dict:
    tiers = dict(
        session.execute(select(Lead.tier, func.count(Lead.id)).group_by(Lead.tier)).all()
    )
    statuses = dict(
        session.execute(select(Lead.status, func.count(Lead.id)).group_by(Lead.status)).all()
    )
    return {
        "total": session.scalar(select(func.count()).select_from(Lead)) or 0,
        "by_tier": tiers,
        "by_status": statuses,
        "average_score": round(session.scalar(select(func.avg(Lead.score))) or 0.0, 2),
    }


@router.patch("/{lead_id}")
def update_lead(
    lead_id: int,
    status: str | None = None,
    notes: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    if status is not None:
        if status not in VALID_STATUSES:
            raise HTTPException(
                status_code=422, detail=f"status must be one of {sorted(VALID_STATUSES)}"
            )
        lead.status = status
    if notes is not None:
        lead.notes = notes
    session.flush()
    return {"id": lead.id, "status": lead.status, "notes": lead.notes}


@router.post("/export")
def export_leads(
    fmt: str = Query("xlsx", pattern="^(csv|xlsx)$"),
    min_score: float = Query(0.0, ge=0, le=100),
    tier: list[str] | None = Query(None),
    require_contact: bool = False,
    session: Session = Depends(get_session),
) -> FileResponse:
    rows, suppressed = build_rows(
        session, min_score=min_score, tiers=tier, require_contact=require_contact
    )
    if not rows:
        raise HTTPException(status_code=404, detail="no leads matched the filters")

    path = get_settings().export_dir / f"leads.{fmt}"
    if fmt == "csv":
        write_csv(rows, path)
        media = "text/csv"
    else:
        write_xlsx(rows, path, suppressed=suppressed)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(path, media_type=media, filename=path.name)


@router.get("/statuses")
def statuses() -> dict:
    return {"statuses": sorted(VALID_STATUSES), "default": LEAD_NEW}
