"""Model artefact repository. OWNER: Agent ML.

INTERFACE (frozen by LEAD):

    ModelRepository(settings)
        load() -> None                      # idempotent; reads models/shortage_xgb_v1.json + model_meta.json
        is_available() -> bool
        model_version -> str                # "xgb-sa-v1"
        get_model_info() -> ModelInfo       # raises jp_a.api.errors.ModelUnavailableError (503)
        predict(features: pandas.DataFrame) -> tuple[numpy.ndarray, numpy.ndarray]
            # columns = jp_a.domain.entities.FEATURE_NAMES in that order;
            # returns (predictions shape (n,), SHAP contribs shape (n, 10) incl. bias col last)

A_MODEL_MODE: local (default) | s3 | sagemaker_endpoint (M12; must fall back to local cleanly).
"""
from __future__ import annotations

from ..domain.entities import ModelInfo


class ModelRepository:
    """STUB - Agent ML implements."""

    def __init__(self, settings):
        self.settings = settings

    def load(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def is_available(self) -> bool:  # pragma: no cover
        return False

    @property
    def model_version(self) -> str:
        return "xgb-sa-v1"

    def get_model_info(self) -> ModelInfo:  # pragma: no cover
        raise NotImplementedError

    def predict(self, features):  # pragma: no cover
        raise NotImplementedError
