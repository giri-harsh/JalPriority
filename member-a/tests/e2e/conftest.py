"""Session fixtures for the e2e suite: one real app (committed model, a temporary copy of ``data/``, a real token).

Derived scenarios created by the disruption tests land in the temporary data dir, never in the repository.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jp_a.main import create_app

from .e2e_helpers import REAL_MODELS_DIR, Client, copy_real_data, write_wide_scenario


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    path = copy_real_data(tmp_path_factory.mktemp("e2e_data"))
    write_wide_scenario(path)
    return path


@pytest.fixture(scope="session")
def e2e_settings(settings, data_dir):
    return settings.model_copy(update={"a_data_dir": data_dir, "a_models_dir": REAL_MODELS_DIR})


@pytest.fixture(scope="session")
def api(e2e_settings):
    with TestClient(create_app(e2e_settings)) as client:
        yield client


@pytest.fixture(scope="session")
def headers(make_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture(scope="session")
def client_a(api, headers) -> Client:
    return Client(api, headers)
