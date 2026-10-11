import httpx
from jp_b.request_context import request_id_var
import logging
import secrets
from typing import Dict, List, Any, Optional, Union

from jp_b.config import settings
from jp_b.services.auth_service import auth_service
from jp_b.api.errors import APIError
from jp_b.api.schemas import (
    Scenario,
    ScenarioSummary,
    PredictionSet,
    ModelInfo,
    PlanResult,
    SolveRequest,
    Disruption
)

logger = logging.getLogger(__name__)

class AHttpClient:
    """HTTP client communicating with upstream Member A per CONTRACT §6."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None
    ):
        self._base_url = (base_url or settings.b_upstream_a_url).rstrip("/")
        self._timeout = float(timeout if timeout is not None else settings.b_a_timeout_s)

    def _headers(self, request_id: Optional[str] = None) -> Dict[str, str]:
        token = auth_service.get_service_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if request_id:
            headers["X-Request-ID"] = request_id
        return headers

    def _handle_response_error(self, resp: httpx.Response):
        """Map upstream error response per CONTRACT §4.5 and MEMBER_B B2/B3."""
        try:
            body = resp.json()
            err_data = body.get("error", {})
        except Exception:
            err_data = {"status_code": resp.status_code, "text": resp.text}

        code = err_data.get("code")
        message = err_data.get("message", f"Upstream error {resp.status_code}")
        details = err_data.get("details", {})

        # Exception: SCENARIO_NOT_FOUND (404) is passed through as 404
        if resp.status_code == 404 or code == "SCENARIO_NOT_FOUND":
            raise APIError(
                code="SCENARIO_NOT_FOUND",
                status_code=404,
                message=message,
                details=details
            )

        # All other upstream errors become 502 UPSTREAM_A_ERROR with details.upstream
        raise APIError(
            code="UPSTREAM_A_ERROR",
            status_code=502,
            message="Upstream service A returned an error.",
            details={"upstream": err_data}
        )

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        require_auth: bool = True,
        request_id: Optional[str] = None
    ) -> Any:
        url = f"{self._base_url}{path}"
        request_id = request_id or request_id_var.get()
        headers = self._headers(request_id) if require_auth else {"Accept": "application/json"}
        if request_id:
            headers["X-Request-ID"] = request_id

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data,
                    headers=headers
                )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            logger.warning(f"Upstream A call failed: {exc}")
            raise APIError(
                code="UPSTREAM_A_UNAVAILABLE",
                status_code=502,
                message="Upstream service A is unavailable or timed out."
            )

        if resp.is_error:
            self._handle_response_error(resp)

        return resp.json()

    async def health(self) -> Dict[str, Any]:
        """Check upstream Member A health per §6.1."""
        return await self._request("GET", "/a/v1/health", require_auth=False)

    async def list_scenarios(self, include_derived: bool = False) -> List[ScenarioSummary]:
        """List scenarios per §6.2."""
        data = await self._request(
            "GET",
            "/a/v1/scenarios",
            params={"include_derived": str(include_derived).lower()}
        )
        scenarios_raw = data.get("scenarios", [])
        return [ScenarioSummary.model_validate(s) for s in scenarios_raw]

    async def get_scenario(self, scenario_id: str) -> Scenario:
        """Fetch Scenario by ID per §6.3."""
        data = await self._request("GET", f"/a/v1/scenarios/{scenario_id}")
        return Scenario.model_validate(data)

    async def apply_disruption(
        self,
        scenario_id: str,
        disruption: Union[Disruption, Dict[str, Any]]
    ) -> Scenario:
        """Apply disruption per §6.4 and §5.12."""
        payload = disruption.model_dump() if isinstance(disruption, Disruption) else disruption
        data = await self._request(
            "POST",
            f"/a/v1/scenarios/{scenario_id}/disruptions",
            json_data=payload
        )
        return Scenario.model_validate(data)

    async def get_predictions(self, scenario_id: str) -> PredictionSet:
        """Fetch predictions per §6.5."""
        data = await self._request("GET", f"/a/v1/scenarios/{scenario_id}/predictions")
        return PredictionSet.model_validate(data)

    async def solve(
        self,
        solve_request: Union[SolveRequest, Dict[str, Any]]
    ) -> PlanResult:
        """Solve plan per §6.6."""
        payload = solve_request.model_dump() if isinstance(solve_request, SolveRequest) else solve_request
        data = await self._request("POST", "/a/v1/plans/solve", json_data=payload)
        return PlanResult.model_validate(data)

    async def get_model_info(self) -> ModelInfo:
        """Fetch model metadata per §6.7."""
        data = await self._request("GET", "/a/v1/model")
        return ModelInfo.model_validate(data)
