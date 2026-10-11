"""6.6 POST /plans/solve. OWNER: Agent OPT (thin delegation; change only if needed)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..domain.entities import PlanResult
from .auth import require_auth
from .deps import Container, get_container
from .schemas import SolveRequestIn

router = APIRouter(tags=["plans"], dependencies=[Depends(require_auth)])


@router.post("/plans/solve", response_model=PlanResult)
def solve_plan(body: SolveRequestIn, c: Container = Depends(get_container)) -> PlanResult:
    return c.plan_service.solve(body)
