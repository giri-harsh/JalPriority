"""Service container + FastAPI dependencies.

``Container`` is stored on ``app.state.container``. Tests may build their own Container with fakes
(scenario repo, prediction service, ...) and pass it to ``create_app(container=...)``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from ..config import Settings
from ..repositories.model_repo import ModelRepository
from ..repositories.scenario_repo import ScenarioRepository
from ..repositories.storage_local import make_storage
from ..services.plan_service import PlanService
from ..services.prediction_service import PredictionService
from ..services.scenario_service import ScenarioService


@dataclass
class Container:
    settings: Settings
    scenario_repo: Any  # ScenarioRepository (or a test fake with the same surface)
    model_repo: Any  # ModelRepository
    prediction_service: Any  # PredictionService
    scenario_service: Any  # ScenarioService
    plan_service: Any  # PlanService


def build_container(settings: Settings) -> Container:
    storage = make_storage(settings)
    scenario_repo = ScenarioRepository(settings, storage)
    model_repo = ModelRepository(settings)
    prediction_service = PredictionService(settings, scenario_repo, model_repo)
    scenario_service = ScenarioService(settings, scenario_repo)
    plan_service = PlanService(settings, scenario_repo, prediction_service)
    return Container(
        settings=settings,
        scenario_repo=scenario_repo,
        model_repo=model_repo,
        prediction_service=prediction_service,
        scenario_service=scenario_service,
        plan_service=plan_service,
    )


def get_container(request: Request) -> Container:
    return request.app.state.container
