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

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd
import xgboost as xgb
from pydantic import ValidationError

from .. import MODEL_VERSION
from ..api.errors import ModelUnavailableError
from ..domain.entities import FEATURE_NAMES, ModelInfo
from ..logging_setup import log_event
from ..ml.artefacts import artefact_path, meta_path
from ..ml.features import FEATURE_COLUMNS, to_model_matrix

N_CONTRIB_COLUMNS = len(FEATURE_NAMES) + 1  # nine features + bias (last)


@dataclass(frozen=True)
class LoadedModel:
    booster: xgb.Booster
    meta: dict[str, Any]
    info: ModelInfo


class ModelLoader(Protocol):
    """Fetches the raw artefacts. Local files today; an S3 loader plugs in here for M12."""

    source: str

    def read_meta(self) -> bytes: ...

    def read_booster(self) -> bytearray: ...


class LocalModelLoader:
    def __init__(self, models_dir: Path | str):
        self.models_dir = Path(models_dir)
        self.source = str(self.models_dir)

    def read_meta(self) -> bytes:
        return meta_path(self.models_dir).read_bytes()

    def read_booster(self) -> bytearray:
        return bytearray(artefact_path(self.models_dir).read_bytes())


def _unavailable(reason: str, **details: Any) -> ModelUnavailableError:
    return ModelUnavailableError("The shortage model is not available.", details={"reason": reason, **details})


def _parse_artefacts(loader: ModelLoader) -> LoadedModel:
    try:
        raw_meta = loader.read_meta()
        raw_booster = loader.read_booster()
    except FileNotFoundError as exc:
        raise _unavailable("artefact_missing", file=Path(exc.filename or "").name) from exc
    except OSError as exc:
        raise _unavailable("artefact_unreadable") from exc

    try:
        meta = json.loads(raw_meta.decode("utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("model_meta.json must hold a JSON object")
        info = ModelInfo.model_validate({k: meta[k] for k in ModelInfo.model_fields})
    except (KeyError, ValueError, ValidationError) as exc:
        raise _unavailable("meta_invalid") from exc
    if list(meta.get("feature_names", [])) != list(FEATURE_NAMES):
        raise _unavailable("meta_feature_mismatch")

    booster = xgb.Booster()
    try:
        booster.load_model(raw_booster)
    except xgb.core.XGBoostError as exc:
        raise _unavailable("artefact_corrupt") from exc
    if booster.feature_names is not None and list(booster.feature_names) != list(FEATURE_NAMES):
        raise _unavailable("artefact_feature_mismatch")
    if booster.num_features() != len(FEATURE_NAMES):
        raise _unavailable("artefact_feature_mismatch")
    booster.feature_names = list(FEATURE_COLUMNS)
    return LoadedModel(booster=booster, meta=meta, info=info)


class ModelRepository:
    """Loads the trained booster once (thread-safe) and serves ModelInfo and predictions with SHAP contributions."""

    def __init__(self, settings, loader: ModelLoader | None = None):
        self.settings = settings
        self._loader = loader or self._default_loader()
        self._loaded: LoadedModel | None = None
        self._load_lock = threading.Lock()
        self._predict_lock = threading.Lock()

    def _default_loader(self) -> ModelLoader:
        mode = getattr(self.settings, "a_model_mode", "local")
        if mode != "local":
            log_event(
                "model_mode_fallback",
                f"A_MODEL_MODE={mode} is not enabled in this build; using local model artefacts",
                level=logging.WARNING,
                requested_mode=mode,
            )
        return LocalModelLoader(self.settings.a_models_dir)

    @property
    def active_mode(self) -> str:
        return "local"

    def load(self) -> None:
        """Load artefacts once. Raises ModelUnavailableError if missing or corrupt; a later call retries."""
        if self._loaded is not None:
            return
        with self._load_lock:
            if self._loaded is not None:
                return
            loaded = _parse_artefacts(self._loader)
            self._loaded = loaded
            log_event(
                "model_loaded",
                f"loaded {loaded.info.model_version} from {self._loader.source}",
                model_version=loaded.info.model_version,
                trained_at=loaded.info.trained_at,
            )

    def _require(self) -> LoadedModel:
        self.load()
        assert self._loaded is not None
        return self._loaded

    def is_available(self) -> bool:
        try:
            self.load()
        except ModelUnavailableError:
            return False
        return True

    @property
    def model_version(self) -> str:
        return self._loaded.info.model_version if self._loaded is not None else MODEL_VERSION

    def get_model_info(self) -> ModelInfo:
        return self._require().info.model_copy(deep=True)

    def predict(self, features: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Return (predicted need per row, SHAP contributions (n, 10) with the bias column last), in litres."""
        loaded = self._require()
        matrix = to_model_matrix(features)  # FeatureError (a ValueError) on missing columns / non-finite values
        dmatrix = xgb.DMatrix(matrix, feature_names=list(FEATURE_COLUMNS))
        with self._predict_lock:
            predictions = loaded.booster.predict(dmatrix)
            contribs = loaded.booster.predict(dmatrix, pred_contribs=True)
        predictions = np.asarray(predictions, dtype=np.float64).reshape(-1)
        contribs = np.asarray(contribs, dtype=np.float64).reshape(len(matrix), N_CONTRIB_COLUMNS)
        return predictions, contribs
