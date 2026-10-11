"""scripts/simulate_image.py: the Docker-less stand-in for ``docker build`` + ``docker run``.

The fast tests pin the parsing / mapping logic against the real Dockerfiles. The full simulation (fresh venv, pip
install, server start, smoke test; about 30 s with a warm uv cache) runs only when ``JP_A_RUN_IMAGE_SIM=1``.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import jwt
import pytest

from jp_a.config import Settings
from jp_a.data.debt import debt_by_area as reference_debt_by_area

MEMBER_A = Path(__file__).resolve().parents[2]
SCRIPT = MEMBER_A / "scripts" / "simulate_image.py"
WRAPPER = MEMBER_A / "scripts" / "simulate_image.sh"


@pytest.fixture(scope="module")
def sim():
    spec = importlib.util.spec_from_file_location("jp_a_simulate_image", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture(scope="module")
def api_model(sim):
    return sim.model_dockerfile(MEMBER_A / "backend" / "Dockerfile")


def test_the_api_dockerfile_is_modelled_completely(api_model):
    assert api_model.base == "python:3.12-slim" and api_model.workdir == "/app"
    assert api_model.env["PYTHONPATH"] == "/app" and api_model.env["A_PORT"] == "8001"
    assert api_model.env["A_DATA_DIR"] == "/app/data" and api_model.env["SHARED_ENV"] == "docker"
    assert dict((tuple(src), dest) for src, dest in api_model.copies) == {
        ("backend/requirements.txt",): "/app/requirements.txt",
        ("backend/jp_a",): "/app/jp_a",
        ("data",): "/app/data",
        ("models",): "/app/models",
    }
    assert api_model.requirements == ["/app/requirements.txt"]
    assert api_model.cmd[:2] == ["sh", "-c"] and "${A_PORT:-8001}" in api_model.cmd[2]
    assert api_model.healthcheck and api_model.healthcheck[0] == "python" and api_model.user == "jpa"


def test_the_lambda_dockerfile_is_modelled_with_the_base_image_task_root(sim):
    model = sim.model_dockerfile(MEMBER_A / "infra" / "lambda.Dockerfile")
    assert model.env["LAMBDA_TASK_ROOT"] == "/var/task" and model.workdir == "/var/task"
    assert {dest for _, dest in model.copies} == {
        "/var/task/requirements.txt", "/var/task/jp_a", "/var/task/data", "/var/task/models",
    }  # fmt: skip
    assert model.requirements == ["/var/task/requirements.txt"] and model.cmd == ["jp_a.lambda_handler.handler"]
    assert model.env["A_DATA_DIR"] == "/var/task/data"


def test_expand_handles_defaults_and_unset_variables(sim):
    assert sim._expand("${A_PORT:-8001}", {}) == "8001"
    assert sim._expand("${A_PORT:-8001}", {"A_PORT": "9"}) == "9"
    assert sim._expand("$X/${Y}/z", {"X": "a", "Y": "b"}) == "a/b/z"
    assert sim._expand("${NOPE}", {}) == ""


def test_image_paths_are_mapped_onto_the_temporary_root(sim, tmp_path):
    root = tmp_path / "app"
    assert sim.map_path("/app", root, "/app") == str(root)
    assert sim.map_path("/app/data", root, "/app") == str(root / "data")
    assert sim.map_path("/application", root, "/app") == "/application", "only whole path segments are mapped"
    assert sim.map_path("docker", root, "/app") == "docker"


def test_container_env_is_minimal_and_mapped(sim, api_model, tmp_path):
    env = sim.container_env(api_model, tmp_path / "app", "/app", 12345, tmp_path / "venv")
    assert env["A_PORT"] == "12345" and env["SHARED_ENV"] == "docker"
    assert env["PYTHONPATH"] == str(tmp_path / "app") and env["A_MODELS_DIR"] == str(tmp_path / "app" / "models")
    assert str(sim.venv_bin(tmp_path / "venv")) in env["PATH"].split(os.pathsep)[0]
    assert not any(k.startswith(("SHARED_JWT", "VIRTUAL_ENV", "PYTHONHOME")) for k in env), "nothing leaks in from the host"


def test_dockerignore_follows_docker_semantics(sim):
    ignore = sim.DockerIgnore(MEMBER_A / ".dockerignore")
    for path in ("tests/unit/test_x.py", "data/derived/x.json", "dashboard/src/a.ts", "MEMBER_A.md", ".venv/Scripts/python.exe",
                 "backend/jp_a/__pycache__/main.cpython-312.pyc", "backend/jp_a/x.pyc", "infra/template.yaml"):  # fmt: skip
        assert ignore.ignored(path), path
    for path in ("README.md", "backend/jp_a/main.py", "models/model_meta.json", "data/scenarios/scn_sa01_w06.json",
                 "data/DATA_GENERATION.md", "backend/requirements.txt"):  # fmt: skip
        assert not ignore.ignored(path), path


def test_build_context_copies_exactly_the_runtime_files(sim, api_model, tmp_path):
    report = sim.Report()
    root = tmp_path / "app"
    copied = sim.build_context(api_model, MEMBER_A / "backend" / "Dockerfile", sim.DockerIgnore(MEMBER_A / ".dockerignore"),
                               "/app", root, report)  # fmt: skip
    assert report.ok, report.rows
    assert (root / "requirements.txt").is_file() and (root / "jp_a" / "main.py").is_file()
    assert (root / "models" / "shortage_xgb_v1.json").is_file() and (root / "data" / "labels.json").is_file()
    assert not (root / "tests").exists() and not (root / "dashboard").exists() and not (root / ".env").exists()
    assert not any(p.startswith("data/derived/") or "__pycache__" in p for p in copied)
    on_disk = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert len(on_disk) == len(copied)


def test_build_context_reports_a_missing_copy_source(sim, tmp_path):
    model = sim.DockerfileModel("x", {}, "/app", [(["does/not/exist"], "/app/x")], [], [], None, None)
    report = sim.Report()
    sim.build_context(model, MEMBER_A / "backend" / "Dockerfile", sim.DockerIgnore(tmp_path / "none"), "/app", tmp_path / "a", report)
    assert not report.ok and "does/not/exist" in report.rows[0][1]


def test_minted_token_is_accepted_by_the_apps_verifier_settings(sim):
    settings = Settings()
    assert (sim.DEV_SECRET, sim.DEV_ISSUER, sim.DEV_AUDIENCE) == (
        settings.shared_jwt_secret, settings.shared_jwt_issuer, settings.shared_jwt_audience,
    )  # fmt: skip
    claims = jwt.decode(sim.mint_token(), sim.DEV_SECRET, algorithms=["HS256"], audience=sim.DEV_AUDIENCE, issuer=sim.DEV_ISSUER)
    assert set(claims) == {"iss", "aud", "sub", "role", "iat", "exp"} and claims["role"] == "dispatcher"
    assert jwt.get_unverified_header(sim.mint_token())["alg"] == "HS256"


def test_debt_helper_equals_the_apps_f10(sim):
    import json

    scenario = json.loads((MEMBER_A / "data" / "scenarios" / "scn_sa01_w06.json").read_text(encoding="utf-8"))
    assert sim.debt_by_area(scenario) == reference_debt_by_area(scenario)


def test_shell_wrapper_exists_and_is_lf_only():
    text = WRAPPER.read_bytes()
    assert text.startswith(b"#!/usr/bin/env bash") and b"\r" not in text and b"simulate_image.py" in text
    assert b"\r" not in SCRIPT.read_bytes()


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("JP_A_RUN_IMAGE_SIM") != "1", reason="set JP_A_RUN_IMAGE_SIM=1 (needs uv + network/cache)")
@pytest.mark.parametrize("dockerfile", ["backend/Dockerfile", "infra/lambda.Dockerfile"])
def test_full_image_simulation_passes(dockerfile):
    done = subprocess.run([sys.executable, str(SCRIPT), "--dockerfile", dockerfile], capture_output=True, text=True,
                          check=False, timeout=900)  # fmt: skip
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-1000:]
    assert done.stdout.strip().endswith("docker build not run, image simulated: PASS")
