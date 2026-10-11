"""M11/M12: both Dockerfiles parse, reference real files, honour .dockerignore and expose the documented contract.

Docker is not installed on the build host, so these checks stand in for ``docker build``.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest

MEMBER_A = Path(__file__).resolve().parents[2]
BACKEND_DOCKERFILE = MEMBER_A / "backend" / "Dockerfile"
LAMBDA_DOCKERFILE = MEMBER_A / "infra" / "lambda.Dockerfile"
DOCKERIGNORE = MEMBER_A / ".dockerignore"


@dataclass(frozen=True)
class Instruction:
    name: str
    args: str
    line: int


def parse_dockerfile(path: Path) -> list[Instruction]:
    """Instructions with backslash continuations joined; comments and blank lines dropped."""
    out: list[Instruction] = []
    pending, start = "", 0
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if stripped.startswith("#"):  # a comment inside a continued instruction
            continue
        if not pending:
            start = number
        if stripped.endswith("\\"):
            pending += stripped[:-1].rstrip() + " "
            continue
        text = (pending + stripped).strip()
        pending = ""
        name, _, args = text.partition(" ")
        out.append(Instruction(name.upper(), args.strip(), start))
    assert not pending, "dangling line continuation"
    return out


def instructions(path: Path, name: str) -> list[Instruction]:
    return [i for i in parse_dockerfile(path) if i.name == name]


def env_map(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for ins in instructions(path, "ENV"):
        for token in shlex.split(ins.args):
            key, _, value = token.partition("=")
            values[key] = value
    return values


def copy_pairs(path: Path) -> list[tuple[str, str]]:
    """(source, destination) for every COPY of the build context (no --from)."""
    pairs = []
    for ins in instructions(path, "COPY"):
        tokens = shlex.split(ins.args)
        assert not any(t.startswith("--from") for t in tokens), "multi-stage copies are not used"
        tokens = [t for t in tokens if not t.startswith("--")]
        *sources, dest = tokens
        pairs += [(src, dest) for src in sources]
    return pairs


# ----------------------------------------------------------------------------------- .dockerignore model

def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
            continue
        out += {"*": "[^/]*", "?": "[^/]"}.get(ch, re.escape(ch))
        i += 1
    return re.compile(f"^{out}$")


def dockerignore_rules() -> list[tuple[bool, re.Pattern[str]]]:
    rules = []
    for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        line = line[1:] if negate else line
        rules.append((negate, _pattern_to_regex(line.strip("/"))))
    return rules


def is_ignored(relpath: str) -> bool:
    """Docker semantics: a path is excluded when it, or any parent directory, matches; ``!`` re-includes; last wins."""
    parts = relpath.strip("/").split("/")
    candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    ignored = False
    for negate, regex in dockerignore_rules():
        if any(regex.match(c) for c in candidates):
            ignored = not negate
    return ignored


def test_dockerignore_model_behaves_like_docker():
    assert is_ignored("tests/unit/test_x.py") and is_ignored("data/derived/x.json") and is_ignored("dashboard/src/a.ts")
    assert is_ignored("MEMBER_A.md") and not is_ignored("README.md")
    assert not is_ignored("data/DATA_GENERATION.md")  # *.md only matches at the context root
    assert not is_ignored("backend/jp_a/main.py") and not is_ignored("models/model_meta.json")


# ----------------------------------------------------------------------------------- shared expectations

RUNTIME_FILES = [
    "backend/requirements.txt",
    "backend/jp_a/main.py",
    "backend/jp_a/lambda_handler.py",
    "data/labels.json",
    "data/scenarios/scn_sa01_w06.json",
    "data/scenarios/scn_sa01_w06_mild.json",
    "data/scenarios/scn_sa01_w06_severe.json",
    "data/truth/scn_sa01_w06.json",
    "models/shortage_xgb_v1.json",
    "models/model_meta.json",
]


@pytest.mark.parametrize("dockerfile", [BACKEND_DOCKERFILE, LAMBDA_DOCKERFILE], ids=["api", "lambda"])
def test_copy_sources_exist_and_are_not_ignored(dockerfile):
    pairs = copy_pairs(dockerfile)
    assert pairs, "the Dockerfile copies nothing"
    for source, _dest in pairs:
        assert not source.startswith("/") and ".." not in source.split("/"), f"{source}: outside the build context"
        assert (MEMBER_A / source).exists(), f"COPY source {source} does not exist in the build context"
        assert not is_ignored(source), f"COPY source {source} is excluded by .dockerignore"


@pytest.mark.parametrize("dockerfile", [BACKEND_DOCKERFILE, LAMBDA_DOCKERFILE], ids=["api", "lambda"])
def test_everything_the_image_needs_survives_dockerignore(dockerfile):
    copied = [src.rstrip("/") for src, _ in copy_pairs(dockerfile)]
    for needed in RUNTIME_FILES:
        assert (MEMBER_A / needed).is_file(), f"{needed} is missing from the repository"
        assert not is_ignored(needed), f"{needed} is excluded by .dockerignore"
        assert any(needed == c or needed.startswith(c + "/") for c in copied), f"{needed} is not COPYed into the image"


def test_requirements_are_installed_from_the_copied_file():
    for dockerfile in (BACKEND_DOCKERFILE, LAMBDA_DOCKERFILE):
        sources = [src for src, _ in copy_pairs(dockerfile)]
        assert "backend/requirements.txt" in sources
        runs = " ".join(i.args for i in instructions(dockerfile, "RUN"))
        assert re.search(r"pip install .*-r \S*requirements\.txt", runs)
        assert instructions(dockerfile, "RUN")[0].line > instructions(dockerfile, "FROM")[0].line


def test_derived_data_is_not_baked_into_the_images():
    assert is_ignored("data/derived/anything.json")
    for dockerfile in (BACKEND_DOCKERFILE, LAMBDA_DOCKERFILE):
        sources = [src for src, _ in copy_pairs(dockerfile)]
        assert "data/derived" not in sources


def test_no_secrets_or_dev_files_are_copied():
    for dockerfile in (BACKEND_DOCKERFILE, LAMBDA_DOCKERFILE):
        for source, _ in copy_pairs(dockerfile):
            assert not source.startswith((".env", "tests", "dashboard", ".git", ".venv"))


# ------------------------------------------------------------------------------------------- API image

def test_api_image_base_and_env():
    dockerfile = BACKEND_DOCKERFILE
    assert [i.args for i in instructions(dockerfile, "FROM")] == ["python:3.12-slim"]
    env = env_map(dockerfile)
    assert env["A_DATA_DIR"] == "/app/data" and env["A_MODELS_DIR"] == "/app/models" and env["A_PORT"] == "8001"
    assert env["PYTHONPATH"] == "/app"


def test_api_image_copy_layout_matches_env():
    pairs = dict(copy_pairs(BACKEND_DOCKERFILE))
    assert pairs["backend/jp_a"] == "/app/jp_a"
    assert pairs["data"] == "/app/data" and pairs["models"] == "/app/models"
    assert instructions(BACKEND_DOCKERFILE, "WORKDIR")[-1].args == "/app"


def test_api_image_runs_as_a_non_root_user_who_can_write_derived_data():
    users = instructions(BACKEND_DOCKERFILE, "USER")
    assert users and users[-1].args not in ("root", "0", "0:0")
    user = users[-1].args
    runs = " ".join(i.args for i in instructions(BACKEND_DOCKERFILE, "RUN"))
    assert "useradd" in runs and user in runs
    assert "mkdir -p /app/data/derived" in runs and f"chown -R {user}:{user} /app/data" in runs
    last_run = max(i.line for i in instructions(BACKEND_DOCKERFILE, "RUN"))
    assert users[-1].line > last_run, "USER must come after the last RUN that needs root"


def test_api_image_installs_the_openmp_runtime_xgboost_needs():
    runs = " ".join(i.args for i in instructions(BACKEND_DOCKERFILE, "RUN"))
    assert "libgomp1" in runs and "--no-install-recommends" in runs and "rm -rf /var/lib/apt/lists" in runs


def test_api_image_healthcheck_probes_the_health_route_on_the_configured_port():
    checks = instructions(BACKEND_DOCKERFILE, "HEALTHCHECK")
    assert len(checks) == 1
    args = checks[0].args
    assert "/a/v1/health" in args and "A_PORT" in args and "CMD" in args
    assert "--start-period" in args and "--interval" in args


def test_api_image_cmd_honours_a_port_and_serves_the_app():
    cmds = instructions(BACKEND_DOCKERFILE, "CMD")
    assert len(cmds) == 1
    argv = _json_list(cmds[0].args)
    assert argv[:2] == ["sh", "-c"], "a shell wrapper is needed so ${A_PORT} is expanded"
    command = argv[2]
    assert command.startswith("exec uvicorn jp_a.main:app")
    assert "--host 0.0.0.0" in command and "--port ${A_PORT" in command
    assert instructions(BACKEND_DOCKERFILE, "EXPOSE")[0].args == "8001"


def test_api_image_port_default_matches_settings():
    from jp_a.config import Settings

    assert str(Settings().a_port) == env_map(BACKEND_DOCKERFILE)["A_PORT"]


def test_the_cmd_target_exists():
    assert (MEMBER_A / "backend" / "jp_a" / "main.py").is_file()
    assert "app = create_app()" in (MEMBER_A / "backend" / "jp_a" / "main.py").read_text(encoding="utf-8")


# ----------------------------------------------------------------------------------------- Lambda image

def test_lambda_image_uses_the_lambda_base_and_the_mangum_handler():
    assert [i.args for i in instructions(LAMBDA_DOCKERFILE, "FROM")] == ["public.ecr.aws/lambda/python:3.12"]
    cmds = instructions(LAMBDA_DOCKERFILE, "CMD")
    assert len(cmds) == 1 and _json_list(cmds[0].args) == ["jp_a.lambda_handler.handler"]
    source = (MEMBER_A / "backend" / "jp_a" / "lambda_handler.py").read_text(encoding="utf-8")
    assert re.search(r"^handler = Mangum\(", source, re.M)


def test_lambda_image_layout_and_env():
    pairs = dict(copy_pairs(LAMBDA_DOCKERFILE))
    root = "${LAMBDA_TASK_ROOT}"
    assert pairs["backend/jp_a"] == f"{root}/jp_a"
    assert pairs["data"] == f"{root}/data" and pairs["models"] == f"{root}/models"
    env = env_map(LAMBDA_DOCKERFILE)
    assert env["A_DATA_DIR"] == f"{root}/data" and env["A_MODELS_DIR"] == f"{root}/models"
    assert env["SHARED_ENV"] == "aws"


def test_lambda_image_installs_the_openmp_runtime():
    runs = " ".join(i.args for i in instructions(LAMBDA_DOCKERFILE, "RUN"))
    assert "libgomp" in runs


def _json_list(text: str) -> list[str]:
    import json

    value = json.loads(text)
    assert isinstance(value, list) and all(isinstance(v, str) for v in value), "use the exec (JSON array) form"
    return value
