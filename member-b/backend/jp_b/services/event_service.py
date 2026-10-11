import secrets
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from jp_b.api.schemas import Event
from jp_b.api.errors import APIError
from jp_b.repositories.base import EventsRepository

ALLOWED_EVENT_NAMES = {
    "scenario_loaded",
    "explanation_viewed",
    "prediction_generated",
    "plan_generated",
    "weights_changed",
    "disruption_applied",
    "replan_completed",
    "comparison_viewed",
    "plan_decision",
}


class EventService:
    def __init__(self, events_repo: EventsRepository):
        self.events_repo = events_repo

    def emit(
        self,
        name: str,
        scenario_id: Optional[str],
        officer_id: str,
        props: Dict[str, Any],
    ) -> Event:
        if name not in ALLOWED_EVENT_NAMES:
            raise APIError(
                code="EVENT_INVALID",
                message=f"Event name '{name}' is not in the CONTRACT §7 event catalogue.",
                status_code=422,
                details={"allowed_names": sorted(list(ALLOWED_EVENT_NAMES))},
            )

        event = Event(
            schema_version="1.0",
            event_id=f"evt_{secrets.token_hex(6)}",
            name=name,
            ts=datetime.now(timezone.utc),
            officer_id=officer_id,
            scenario_id=scenario_id,
            props=props,
        )

        self.events_repo.save(event.model_dump(mode="json"))
        return event

    def list_events(
        self,
        scenario_id: Optional[str] = None,
        name: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Event]:
        raw_events = self.events_repo.list_events(
            scenario_id=scenario_id,
            name=name,
            limit=limit,
        )
        return [Event.model_validate(e) for e in raw_events]
