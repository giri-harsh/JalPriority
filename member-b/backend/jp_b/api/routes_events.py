from typing import Optional
from fastapi import APIRouter, Depends, Query, status

from jp_b.api.deps import get_current_user, get_event_service
from jp_b.api.schemas import (
    EventTrackRequest,
    EventTrackResponse,
    EventsListResponse,
)
from jp_b.services.event_service import EventService

router = APIRouter(tags=["Events"])


@router.post("/b/v1/events", response_model=EventTrackResponse, status_code=status.HTTP_202_ACCEPTED)
async def track_event(
    req: EventTrackRequest,
    user: dict = Depends(get_current_user),
    event_service: EventService = Depends(get_event_service),
):
    officer_id = user.get("sub", "officer_01")
    event = event_service.emit(
        name=req.name,
        scenario_id=req.scenario_id,
        officer_id=officer_id,
        props=req.props,
    )
    return {"event_id": event.event_id}


@router.get("/b/v1/events", response_model=EventsListResponse)
async def list_events(
    scenario_id: Optional[str] = Query(default=None),
    name: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None),
    user: dict = Depends(get_current_user),
    event_service: EventService = Depends(get_event_service),
):
    events = event_service.list_events(
        scenario_id=scenario_id,
        name=name,
        limit=limit,
    )
    return {"events": events}
