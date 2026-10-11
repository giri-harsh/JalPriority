from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jp_b.services.auth_service import auth_service
from jp_b.api.errors import APIError

bearer_scheme = HTTPBearer(auto_error=False)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    if not credentials or not credentials.credentials:
        raise APIError("UNAUTHORIZED", status_code=401, message="Missing authentication token")
    
    payload = auth_service.verify_token(credentials.credentials)
    
    # Ensure role is one of dispatcher or admin
    role = payload.get("role")
    if role not in ["dispatcher", "admin"]:
        raise APIError("FORBIDDEN", status_code=403, message="Insufficient permissions")
    
    return payload

from typing import Optional
from jp_b.config import settings
from jp_b.clients.a_client import AClient
from jp_b.clients.a_mock import AMockClient
from jp_b.clients.a_http import AHttpClient

_mock_client: Optional[AMockClient] = None

def get_a_client() -> AClient:
    global _mock_client
    if settings.b_use_mock_a:
        if _mock_client is None:
            _mock_client = AMockClient()
        return _mock_client
    return AHttpClient()

from jp_b.repositories.base import (
    LedgerRepository,
    PlansRepository,
    EventsRepository,
    AuditRepository,
    WeightsRepository,
)
from jp_b.repositories.sqlite_repo import (
    SQLiteLedgerRepository,
    SQLitePlansRepository,
    SQLiteEventsRepository,
    SQLiteAuditRepository,
    SQLiteWeightsRepository,
)
from jp_b.repositories.dynamodb_repo import (
    DynamoDBLedgerRepository,
    DynamoDBPlansRepository,
    DynamoDBEventsRepository,
    DynamoDBAuditRepository,
    DynamoDBWeightsRepository,
)
from jp_b.services.ledger_service import LedgerService
from jp_b.services.event_service import EventService
from jp_b.services.audit_service import AuditService
from jp_b.services.weights_service import WeightsService
from jp_b.services.plan_service import PlanService

def get_ledger_repo() -> LedgerRepository:
    if settings.b_store_mode == "dynamodb":
        return DynamoDBLedgerRepository()
    return SQLiteLedgerRepository()

def get_ledger_service(repo: LedgerRepository = Depends(get_ledger_repo)) -> LedgerService:
    return LedgerService(repo)

def get_plans_repo() -> PlansRepository:
    if settings.b_store_mode == "dynamodb":
        return DynamoDBPlansRepository()
    return SQLitePlansRepository()

def get_events_repo() -> EventsRepository:
    if settings.b_store_mode == "dynamodb":
        return DynamoDBEventsRepository()
    return SQLiteEventsRepository()

def get_event_service(repo: EventsRepository = Depends(get_events_repo)) -> EventService:
    return EventService(repo)

def get_audit_repo() -> AuditRepository:
    if settings.b_store_mode == "dynamodb":
        return DynamoDBAuditRepository()
    return SQLiteAuditRepository()

def get_audit_service(repo: AuditRepository = Depends(get_audit_repo)) -> AuditService:
    return AuditService(repo)

def get_weights_repo() -> WeightsRepository:
    if settings.b_store_mode == "dynamodb":
        return DynamoDBWeightsRepository()
    return SQLiteWeightsRepository()

def get_weights_service(
    repo: WeightsRepository = Depends(get_weights_repo),
    event_service: EventService = Depends(get_event_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> WeightsService:
    return WeightsService(repo, event_service=event_service, audit_service=audit_service)

def get_plan_service(
    a_client: AClient = Depends(get_a_client),
    plans_repo: PlansRepository = Depends(get_plans_repo),
    ledger_service: LedgerService = Depends(get_ledger_service),
    weights_repo: WeightsRepository = Depends(get_weights_repo),
    event_service: EventService = Depends(get_event_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> PlanService:
    return PlanService(
        a_client=a_client,
        plans_repo=plans_repo,
        ledger_service=ledger_service,
        weights_repo=weights_repo,
        event_service=event_service,
        audit_service=audit_service,
    )

from jp_b.services.decision_service import DecisionService

def get_decision_service(
    plans_repo: PlansRepository = Depends(get_plans_repo),
    plan_service: PlanService = Depends(get_plan_service),
    ledger_service: LedgerService = Depends(get_ledger_service),
    event_service: EventService = Depends(get_event_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> DecisionService:
    return DecisionService(
        plans_repo=plans_repo,
        plan_service=plan_service,
        ledger_service=ledger_service,
        event_service=event_service,
        audit_service=audit_service,
    )

from jp_b.services.comparison_service import ComparisonService

def get_comparison_service(
    a_client: AClient = Depends(get_a_client),
    plan_service: PlanService = Depends(get_plan_service),
    ledger_service: LedgerService = Depends(get_ledger_service),
    event_service: EventService = Depends(get_event_service),
) -> ComparisonService:
    return ComparisonService(
        a_client=a_client,
        plan_service=plan_service,
        ledger_service=ledger_service,
        event_service=event_service,
    )

from jp_b.services.replan_service import ReplanService

def get_replan_service(
    a_client: AClient = Depends(get_a_client),
    plans_repo: PlansRepository = Depends(get_plans_repo),
    event_service: EventService = Depends(get_event_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> ReplanService:
    return ReplanService(
        a_client=a_client,
        plans_repo=plans_repo,
        event_service=event_service,
        audit_service=audit_service,
    )





