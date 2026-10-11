from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
import logging

from jp_b.api.deps import get_current_user, get_a_client, get_ledger_service
from jp_b.api.schemas import LedgerResponse
from jp_b.api.errors import APIError
from jp_b.clients.a_client import AClient
from jp_b.services.ledger_service import LedgerService
from jp_b.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/b/v1", tags=["ledger"])


@router.get("/ledger", response_model=LedgerResponse)
async def get_ledger(
    scenario_id: Optional[str] = Query(None, description="Scenario ID to resolve and seed ledger if needed"),
    service_area_id: Optional[str] = Query(None, description="Service area ID to retrieve ledger for"),
    current_user: dict = Depends(get_current_user),
    a_client: AClient = Depends(get_a_client),
    ledger_service: LedgerService = Depends(get_ledger_service),
) -> LedgerResponse:
    """
    Expose deprivation debt ledger data per CONTRACT §5.11 and MEMBER_B.md F5.
    Seeding historical windows 1..(current - 1) on first encounter.
    """
    if not scenario_id and not service_area_id:
        raise APIError("VALIDATION_ERROR", status_code=422, message="Either scenario_id or service_area_id must be provided")

    scenario_dict = None
    target_service_area_id = service_area_id

    if scenario_id:
        try:
            scenario = await a_client.get_scenario(scenario_id)
            scenario_dict = scenario.model_dump()
            target_service_area_id = scenario.service_area_id
        except APIError:
            raise
        except Exception as e:
            logger.error("Failed to fetch scenario %s: %s", scenario_id, str(e))
            raise APIError("SCENARIO_NOT_FOUND", status_code=404, message=f"Scenario {scenario_id} not found")

    assert target_service_area_id is not None

    return ledger_service.get_ledger(
        service_area_id=target_service_area_id,
        scenario_data=scenario_dict,
        decay=settings.b_debt_decay,
        cap_litres=settings.b_debt_cap_litres,
    )
