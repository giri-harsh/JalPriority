#!/usr/bin/env python3
"""Simulate the member-a API image without Docker (docker / podman are not installed on the build host).

What it does, driven entirely by the Dockerfile:

1. Parses the Dockerfile (default ``backend/Dockerfile``) and copies exactly its ``COPY`` sources - filtered through
   ``.dockerignore`` - into a temporary directory that plays the part of ``/app`` (same layout, same relative paths).
2. Creates a FRESH python 3.12 virtualenv (``uv venv -p 3.12``) and installs ONLY the requirements file the
   Dockerfile's ``RUN pip install -r ...`` names (no dev requirements, no editable ``jp_a``). ``jp_a`` therefore
   resolves only through the Dockerfile's ``PYTHONPATH`` / ``WORKDIR``, exactly as in the image.
3. Starts the Dockerfile's ``CMD`` (through ``sh -c`` when it uses one) with the Dockerfile's ``ENV`` values mapped
   onto the temporary paths, on a free port, with a minimal environment (nothing leaks in from the host shell).
4. Runs the ``HEALTHCHECK`` command, mints an HS256 dev token and smoke-tests health, scenarios, predictions, model,
   plans/solve (every seed, plus a derived disruption scenario): HTTP 200 and 0 constraint violations.
5. Kills the server and removes the temporary directory.

``--dockerfile infra/lambda.Dockerfile`` runs the Lambda variant instead: the same copy / install steps, then the
Mangum handler is invoked in-process with API Gateway HTTP API (payload 2.0) events.

Only the standard library is used by this script. Usage (from member-a/)::

    python scripts/simulate_image.py [--dockerfile backend/Dockerfile] [--keep] [--python 3.12]

Exit status 0 = PASS. The last line is ``docker build not run, image simulated: PASS`` (or ``FAIL``).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # member-a (the Docker build context)
DEFAULT_DOCKERFILE = "backend/Dockerfile"
# Dev values of the three contract-owned SHARED_* settings (jp_a.config defaults; the image does not override them).
DEV_SECRET = "dev-only-jalpriority-secret-change-me-0123456789"
DEV_ISSUER = "jalpriority-b"
DEV_AUDIENCE = "jalpriority"
SEEDS = ("scn_sa01_w06", "scn_sa01_w06_mild", "scn_sa01_w06_severe")
WEIGHTS = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
API = "/a/v1"
IS_WINDOWS = os.name == "nt"


# ----------------------------------------------------------------------------------------------- reporting
@dataclass
class Report:
    rows: list[tuple[bool, str, str]] = field(default_factory=list)

    def check(self, ok: bool, name: str, detail: str = "") -> bool:
        self.rows.append((bool(ok), name, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""), flush=True)
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(ok for ok, _, _ in self.rows)


def step(message: str) -> None:
    print(f"==> {message}", flush=True)


# ------------------------------------------------------------------------------------------ Dockerfile model
@dataclass(frozen=True)
class Instruction:
    name: str
    args: str
    line: int


def parse_dockerfile(path: Path) -> list[Instruction]:
    """Instructions with ``\\`` continuations joined; comments and blank lines dropped."""
    out: list[Instruction] = []
    pending, start = "", 0
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("#") or (not pending and not stripped):
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
    if pending:
        raise SystemExit("Dockerfile ends inside a line continuation")
    return out


def parse_env(args: str) -> dict[str, str]:
    values: dict[str, str] = {}
    tokens = shlex.split(args)
    if tokens and "=" not in tokens[0]:  # legacy "ENV key value"
        return {tokens[0]: " ".join(tokens[1:])}
    for token in tokens:
        key, _, value = token.partition("=")
        values[key] = value
    return values


def _expand(text: str, env: dict[str, str]) -> str:
    """``${VAR}`` / ``$VAR`` / ``${VAR:-default}`` expansion for Dockerfile (build-time) strings."""

    def repl(match: re.Match[str]) -> str:
        braced, default, bare = match.group(1), match.group(2), match.group(3)
        name = braced or bare
        value = env.get(name, "")
        return value if value else (default or "")

    return re.sub(r"\$\{(\w+)(?::-([^}]*))?\}|\$(\w+)", repl, text)


@dataclass
class DockerfileModel:
    base: str
    env: dict[str, str]
    workdir: str
    copies: list[tuple[list[str], str]]  # (sources, destination as written, already ENV-expanded)
    requirements: list[str]  # files named by "pip install -r"
    cmd: list[str]
    healthcheck: list[str] | None
    user: str | None


def model_dockerfile(path: Path) -> DockerfileModel:
    env: dict[str, str] = {}
    workdir = "/"
    copies: list[tuple[list[str], str]] = []
    requirements: list[str] = []
    cmd: list[str] = []
    healthcheck: list[str] | None = None
    base, user = "", None
    for ins in parse_dockerfile(path):
        if ins.name == "FROM":
            base = ins.args
            if "lambda" in base.lower():  # the Lambda base image defines these
                env["LAMBDA_TASK_ROOT"] = "/var/task"
                workdir = "/var/task"
        elif ins.name == "ENV":
            for key, value in parse_env(ins.args).items():
                env[key] = _expand(value, env)
        elif ins.name == "WORKDIR":
            target = _expand(ins.args, env)
            workdir = target if target.startswith("/") else f"{workdir.rstrip('/')}/{target}"
        elif ins.name == "COPY":
            tokens = [t for t in shlex.split(ins.args) if not t.startswith("--")]
            if any(t.startswith("--from") for t in shlex.split(ins.args)):
                raise SystemExit(f"line {ins.line}: multi-stage COPY is not simulated")
            *sources, dest = tokens
            dest = _expand(dest, env)
            if not dest.startswith("/"):
                dest = f"{workdir.rstrip('/')}/{dest}"
            copies.append((sources, dest))
        elif ins.name == "RUN":
            for match in re.finditer(r"pip install\b[^&;|]*?-r\s+(\S+)", ins.args):
                req = _expand(match.group(1), env)
                requirements.append(req if req.startswith("/") else f"{workdir.rstrip('/')}/{req}")
        elif ins.name == "CMD":
            cmd = json.loads(ins.args)
        elif ins.name == "HEALTHCHECK":
            m = re.search(r"\bCMD\s+(\[.*\])\s*$", ins.args)
            if m:
                healthcheck = json.loads(m.group(1))
        elif ins.name == "USER":
            user = ins.args
    return DockerfileModel(base, env, workdir, copies, requirements, cmd, healthcheck, user)


# ------------------------------------------------------------------------------------------- .dockerignore
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        else:
            ch = pattern[i]
            out += {"*": "[^/]*", "?": "[^/]"}.get(ch, re.escape(ch))
            i += 1
    return re.compile(f"^{out}$")


class DockerIgnore:
    """Docker semantics: a path is excluded when it or any parent directory matches; ``!`` re-includes; last wins."""

    def __init__(self, path: Path):
        self.rules: list[tuple[bool, re.Pattern[str]]] = []
        if path.is_file():
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line and not line.startswith("#"):
                    negate = line.startswith("!")
                    self.rules.append((negate, _glob_to_regex((line[1:] if negate else line).strip("/"))))

    def ignored(self, relpath: str) -> bool:
        parts = relpath.strip("/").split("/")
        candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        result = False
        for negate, regex in self.rules:
            if any(regex.match(c) for c in candidates):
                result = not negate
        return result


# --------------------------------------------------------------------------------------------- build steps
def map_path(value: str, app_root: Path, image_root: str) -> str:
    """Map an absolute image path (``/app/...``) onto the temporary directory; leave other values alone."""
    if value == image_root or value.startswith(image_root + "/"):
        return str(app_root / value[len(image_root):].lstrip("/"))
    return value


def build_context(model: DockerfileModel, df_path: Path, ignore: DockerIgnore, image_root: str, app_root: Path,
                  report: Report) -> list[str]:
    """Replay every COPY into ``app_root`` (the temp equivalent of the image root). Returns the files copied."""
    copied: list[str] = []
    for sources, dest in model.copies:
        for source in sources:
            src = ROOT / source
            if not src.exists():
                report.check(False, f"COPY source exists: {source}", "missing in the build context")
                continue
            if ignore.ignored(source):
                report.check(False, f"COPY source not excluded by .dockerignore: {source}", "excluded")
                continue
            target = Path(map_path(dest, app_root, image_root))
            if src.is_dir():
                for file in sorted(p for p in src.rglob("*") if p.is_file()):
                    rel = file.relative_to(ROOT).as_posix()
                    if ignore.ignored(rel):
                        continue
                    out = target / file.relative_to(src)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, out)
                    copied.append(rel)
                target.mkdir(parents=True, exist_ok=True)
            else:
                out = target / src.name if dest.endswith("/") else target
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, out)
                copied.append(source)
    return copied


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, text=True, capture_output=True, **kwargs)


def find_uv() -> list[str]:
    uv = shutil.which("uv")
    if uv:
        return [uv]
    probe = run([sys.executable, "-m", "uv", "--version"])
    if probe.returncode == 0:
        return [sys.executable, "-m", "uv"]
    raise SystemExit("uv is required (pip install uv)")


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if IS_WINDOWS else "bin")


def create_venv_and_install(venv: Path, requirements: Path, python: str, report: Report) -> bool:
    uv = find_uv()
    created = run([*uv, "venv", "-p", python, str(venv)])
    if not report.check(created.returncode == 0, f"fresh venv (uv venv -p {python})",
                        "" if created.returncode == 0 else created.stderr.strip()[-300:]):  # fmt: skip
        return False
    py = venv_python(venv)
    version = run([str(py), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"]).stdout.strip()
    report.check(version == python, f"venv interpreter is python {python}", f"got {version}")
    installed = run([*uv, "pip", "install", "-p", str(py), "-r", str(requirements)])
    if not report.check(installed.returncode == 0, "pip install -r <requirements from the Dockerfile> only",
                        "" if installed.returncode == 0 else installed.stderr.strip()[-600:]):  # fmt: skip
        return False
    listing = run([*uv, "pip", "list", "-p", str(py), "--format", "json"])
    names = {item["name"].lower().replace("_", "-") for item in json.loads(listing.stdout or "[]")}
    report.check("jp-a" not in names and "pytest" not in names, "no editable jp-a / dev packages in the image venv",
                 f"{len(names)} packages")  # fmt: skip
    return True


def check_linux_wheels(requirements: Path, python: str, report: Report) -> None:
    """The host (Windows) venv proves the code runs; this proves the pinned set also installs on the image's platform.

    ``python:3.12-slim`` has no compiler, so every pin must exist as a binary wheel for manylinux x86_64 (the Lambda
    template pins x86_64 too). Nothing is downloaded: only the resolution is performed (``uv pip compile``).
    """
    for platform in ("x86_64-manylinux_2_28", "aarch64-manylinux_2_28"):
        done = run([*find_uv(), "pip", "compile", "--only-binary", ":all:", "--python-platform", platform,
                    "--python-version", python, "--quiet", str(requirements)])  # fmt: skip
        packages = [ln.split("==")[0] for ln in done.stdout.splitlines() if "==" in ln and not ln.startswith(("#", " "))]
        heavy = [p for p in packages if p.startswith("nvidia-")]
        report.check(done.returncode == 0, f"requirements resolve to binary wheels only on {platform}",
                     f"{len(packages)} packages" + (f"; note: {', '.join(heavy)} is pulled in (large)" if heavy else "")
                     if done.returncode == 0 else done.stderr.strip()[-300:])  # fmt: skip


def container_env(model: DockerfileModel, app_root: Path, image_root: str, port: int, venv: Path) -> dict[str, str]:
    """Minimal process environment + the Dockerfile ENV with image paths mapped onto the temp directory."""
    env: dict[str, str] = {}
    for key in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "COMSPEC", "PATHEXT", "WINDIR", "USERPROFILE", "LANG"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["HOME"] = str(app_root.parent)
    for key, value in model.env.items():
        if key == "PYTHONPATH":
            env[key] = os.pathsep.join(map_path(part, app_root, image_root) for part in value.split(":") if part)
        else:
            env[key] = map_path(value, app_root, image_root)
    env["A_PORT"] = str(port)
    # the image's python is first on PATH; sh (for CMD ["sh", "-c", ...]) comes from the host's POSIX tools
    sh = shutil.which("sh")
    tail = [str(Path(sh).parent)] if sh else []
    env["PATH"] = os.pathsep.join([str(venv_bin(venv)), *tail, *([os.environ.get("PATH", "")] if not IS_WINDOWS else [])])
    if IS_WINDOWS:  # keep the Windows system dirs so cygwin/msys sh can find its DLLs and coreutils
        env["PATH"] += os.pathsep + os.path.join(env.get("SYSTEMROOT", r"C:\Windows"), "System32")
    return env


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def mint_token(role: str = "dispatcher", sub: str = "officer_01", ttl: int = 3600) -> str:
    """HS256 JWT with exactly the CONTRACT section 8 claims (stdlib only)."""

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    now = int(time.time())
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"iss": DEV_ISSUER, "aud": DEV_AUDIENCE, "sub": sub, "role": role, "iat": now, "exp": now + ttl}
    body = b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(DEV_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{b64(signature)}"


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)


# ----------------------------------------------------------------------------------------------- HTTP smoke
class Http:
    def __init__(self, port: int, token: str):
        self.port, self.token = port, token

    def call(self, method: str, path: str, body: dict | None = None, auth: bool = True) -> tuple[int, object]:
        headers = {"Accept": "application/json"}
        data = None
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw or b"null")
            except ValueError:
                return exc.code, raw.decode("utf-8", "replace")


def debt_by_area(scenario: dict, lam: float = 0.3) -> dict[str, int]:
    """CONTRACT F-10: D_w = round((1 - lam) * D_{w-1} + max(0, need_w - received_w))."""
    out: dict[str, int] = {}
    for area in scenario["areas"]:
        debt = 0
        for entry in sorted(area["history"], key=lambda e: e["window_index"]):
            debt = round((1 - lam) * debt + max(0, entry["need_litres"] - entry["received_litres"]))
        out[area["area_id"]] = debt
    return out


def check_plan(report: Report, label: str, plan: dict) -> None:
    check = plan["constraint_check"]
    flags = (check["water_cap_ok"], check["capacity_ok"], check["route_limit_ok"], check["unavailable_tanker_ok"])
    report.check(not check["violations"] and all(flags), f"{label}: 0 constraint violations",
                 f"status={plan['solver']['status']} engine={plan['solver']['engine']} "
                 f"allocated={plan['totals']['allocated_litres']}")  # fmt: skip


def smoke_test(report: Report, http: Http, health_cmd: list[str] | None, env: dict[str, str], cwd: Path) -> None:
    status, health = http.call("GET", f"{API}/health", auth=False)
    report.check(status == 200 and isinstance(health, dict) and health.get("status") == "ok", "GET /health (no token) 200",
                 json.dumps(health)[:140])  # fmt: skip
    if health_cmd:
        probe = run(health_cmd, env=env, cwd=str(cwd))
        report.check(probe.returncode == 0, "Dockerfile HEALTHCHECK command exits 0", probe.stderr.strip()[-200:])
    status, _ = http.call("GET", f"{API}/scenarios", auth=False)
    report.check(status == 401, "GET /scenarios without a token is 401", f"got {status}")

    status, listing = http.call("GET", f"{API}/scenarios")
    ids = [s["scenario_id"] for s in listing["scenarios"]] if status == 200 else []
    report.check(status == 200 and set(SEEDS) <= set(ids), "GET /scenarios 200 with the 3 seeds", ", ".join(ids))

    status, model = http.call("GET", f"{API}/model")
    report.check(status == 200 and model.get("is_simulation") is True and model.get("model_version"),
                 "GET /model 200 (model artefact loaded)", str(model.get("model_version") if status == 200 else model)[:120])

    for sid in SEEDS:
        status, scenario = http.call("GET", f"{API}/scenarios/{sid}")
        if not report.check(status == 200, f"GET /scenarios/{sid} 200"):
            continue
        status, pred = http.call("GET", f"{API}/scenarios/{sid}/predictions")
        report.check(status == 200 and len(pred["areas"]) == len(scenario["areas"]), f"GET /scenarios/{sid}/predictions 200",
                     f"latency_ms={pred.get('latency_ms') if status == 200 else status}")  # fmt: skip
        body = {"scenario_id": sid, "weights": WEIGHTS, "debt_by_area": debt_by_area(scenario)}
        status, plan = http.call("POST", f"{API}/plans/solve", body)
        if report.check(status == 200, f"POST /plans/solve {sid} 200", "" if status == 200 else str(plan)[:200]):
            check_plan(report, f"plans/solve {sid}", plan)

    # derived scenarios are written under A_DATA_DIR/derived (the directory the image makes writable)
    status, derived = http.call("POST", f"{API}/scenarios/{SEEDS[0]}/disruptions",
                                {"type": "supply_cut", "params": {"fraction": 0.2}})  # fmt: skip
    if report.check(status in (200, 201), "POST /scenarios/{seed}/disruptions writes a derived scenario", f"status={status}"):
        sid = derived["scenario_id"]
        body = {"scenario_id": sid, "weights": WEIGHTS, "debt_by_area": debt_by_area(derived)}
        status, plan = http.call("POST", f"{API}/plans/solve", body)
        if report.check(status == 200, f"POST /plans/solve {sid} 200", "" if status == 200 else str(plan)[:200]):
            check_plan(report, f"plans/solve {sid}", plan)


def wait_for_health(port: int, proc: subprocess.Popen, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", f"{API}/health")
            if conn.getresponse().status == 200:
                return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------------------------- API image
def simulate_api(model: DockerfileModel, ignore: DockerIgnore, tmp: Path, args, report: Report) -> None:
    image_root = model.workdir
    app_root = tmp / "app"
    app_root.mkdir()
    step(f"copy the COPY sources (filtered by .dockerignore) into {app_root} (= {image_root})")
    copied = build_context(model, ROOT / args.dockerfile, ignore, image_root, app_root, report)
    report.check(bool(copied), "COPY layout reproduced", f"{len(copied)} files")
    leaked = [p for p in copied if p.startswith(("tests/", "dashboard/", "infra/", "data/derived/")) or "__pycache__" in p]
    report.check(not leaked, "no tests / dashboard / infra / derived data / bytecode in the image", ", ".join(leaked[:5]))
    report.check(not any(Path(p).name == ".env" for p in copied), "no .env file in the image")

    reqs = [map_path(r, app_root, image_root) for r in model.requirements]
    if not report.check(len(reqs) == 1 and Path(reqs[0]).is_file(), "Dockerfile installs one requirements file that was COPYed",
                        ", ".join(model.requirements)):  # fmt: skip
        return
    step("fresh venv + pip install of the Dockerfile requirements (no dev extras, no editable package)")
    venv = tmp / "venv"
    if not create_venv_and_install(venv, Path(reqs[0]), args.python, report):
        return

    check_linux_wheels(Path(reqs[0]), args.python, report)
    port = free_port()
    env = container_env(model, app_root, image_root, port, venv)
    py = str(venv_python(venv))
    where = run([py, "-I", "-c", "import sys; sys.path[:0] = [p for p in sys.argv[1].split(sys.argv[2]) if p]; "
                 "import jp_a; print(jp_a.__file__)", env["PYTHONPATH"], os.pathsep], env=env, cwd=str(app_root))  # fmt: skip
    location = where.stdout.strip()
    report.check(where.returncode == 0 and Path(location).resolve().is_relative_to(app_root.resolve()),
                 "jp_a resolves from the copied code via PYTHONPATH", location or where.stderr.strip()[-200:])  # fmt: skip

    cmd = list(model.cmd)  # the shell expands ${A_PORT:-8001} itself, exactly as in the image
    if cmd[:2] == ["sh", "-c"] and not shutil.which("sh", path=env["PATH"]):
        report.check(False, "sh is available for the CMD wrapper", "install Git for Windows / a POSIX shell")
        return
    step(f"start CMD {model.cmd} on 127.0.0.1:{port}")
    log_path = tmp / "server.log"
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(cmd, env=env, cwd=str(app_root), stdout=log, stderr=subprocess.STDOUT)
        try:
            up = wait_for_health(port, proc)
            report.check(up, "server answers /a/v1/health after start", f"port {port}")
            if not up:
                print("---- server log ----\n" + log_path.read_text(errors="replace")[-3000:], flush=True)
                return
            health_cmd = None
            if model.healthcheck:
                health_cmd = [py if part == "python" else part for part in model.healthcheck]
            smoke_test(report, Http(port, mint_token()), health_cmd, env, app_root)
            log_text = log_path.read_text(errors="replace")
            report.check("Traceback" not in log_text, "no tracebacks in the server log")
            report.check("model_load_failed" not in log_text, "model loaded at startup (no model_load_failed event)")
        finally:
            kill_tree(proc)
    report.check(proc.poll() is not None, "server process stopped")


# -------------------------------------------------------------------------------------------- Lambda image
LAMBDA_DRIVER = r"""
import json, sys
sys.path[:0] = [p for p in sys.argv[3].split(sys.argv[4]) if p]
from jp_a.lambda_handler import handler

def event(method, path, token=None, body=None):
    headers = {"accept": "application/json"}
    if token:
        headers["authorization"] = "Bearer " + token
    if body is not None:
        headers["content-type"] = "application/json"
    return {"version": "2.0", "routeKey": "$default", "rawPath": path, "rawQueryString": "", "headers": headers,
            "requestContext": {"accountId": "123456789012", "apiId": "api", "domainName": "x.execute-api.ap-south-1.amazonaws.com",
                               "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "127.0.0.1",
                                        "userAgent": "simulate_image"}, "requestId": "r", "routeKey": "$default",
                               "stage": "$default", "time": "01/Jan/2026:00:00:00 +0000", "timeEpoch": 0},
            "body": json.dumps(body) if body is not None else None, "isBase64Encoded": False}

def call(method, path, token=None, body=None):
    r = handler(event(method, path, token, body), None)
    return r["statusCode"], json.loads(r["body"] or "null")

token = sys.argv[1]
seed = sys.argv[2]
out = {}
out["health"] = call("GET", "/a/v1/health")
out["no_token"] = call("GET", "/a/v1/scenarios")[0]
out["scenarios"] = call("GET", "/a/v1/scenarios", token)
out["model"] = call("GET", "/a/v1/model", token)[0]
status, scenario = call("GET", "/a/v1/scenarios/" + seed, token)
debt = {}
for a in scenario["areas"]:
    d = 0
    for e in sorted(a["history"], key=lambda e: e["window_index"]):
        d = round(0.7 * d + max(0, e["need_litres"] - e["received_litres"]))
    debt[a["area_id"]] = d
out["predictions"] = call("GET", "/a/v1/scenarios/" + seed + "/predictions", token)[0]
w = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
status, plan = call("POST", "/a/v1/plans/solve", token, {"scenario_id": seed, "weights": w, "debt_by_area": debt})
out["solve"] = [status, plan["constraint_check"], plan["solver"]]
print("RESULT " + json.dumps(out))
"""


def simulate_lambda(model: DockerfileModel, ignore: DockerIgnore, tmp: Path, args, report: Report) -> None:
    image_root = model.env.get("LAMBDA_TASK_ROOT", "/var/task")
    task_root = tmp / "var-task"
    task_root.mkdir()
    step(f"copy the COPY sources into {task_root} (= {image_root})")
    copied = build_context(model, ROOT / args.dockerfile, ignore, image_root, task_root, report)
    report.check(bool(copied), "COPY layout reproduced", f"{len(copied)} files")
    reqs = [map_path(r, task_root, image_root) for r in model.requirements]
    if not report.check(len(reqs) == 1 and Path(reqs[0]).is_file(), "Dockerfile installs one requirements file that was COPYed",
                        ", ".join(model.requirements)):  # fmt: skip
        return
    venv = tmp / "venv"
    if not create_venv_and_install(venv, Path(reqs[0]), args.python, report):
        return
    check_linux_wheels(Path(reqs[0]), args.python, report)
    env = container_env(model, task_root, image_root, free_port(), venv)
    env["PYTHONPATH"] = str(task_root)  # the Lambda base image puts LAMBDA_TASK_ROOT on PYTHONPATH
    env["AWS_DEFAULT_REGION"] = "ap-south-1"
    env["SHARED_JWT_SECRET"] = DEV_SECRET
    step("invoke jp_a.lambda_handler.handler with API Gateway HTTP API (2.0) events")
    done = run([str(venv_python(venv)), "-I", "-c", LAMBDA_DRIVER, mint_token(), SEEDS[0], str(task_root), os.pathsep],
               env=env, cwd=str(task_root))  # fmt: skip
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")), "")
    if not report.check(done.returncode == 0 and bool(line), "Lambda handler imports and answers events",
                        done.stderr.strip()[-500:]):  # fmt: skip
        return
    out = json.loads(line[len("RESULT "):])
    report.check(out["health"][0] == 200, "handler: GET /health 200")
    report.check(out["no_token"] == 401, "handler: GET /scenarios without a token is 401")
    report.check(out["scenarios"][0] == 200 and len(out["scenarios"][1]["scenarios"]) >= 3, "handler: GET /scenarios 200")
    report.check(out["model"] == 200 and out["predictions"] == 200, "handler: /model and /predictions 200")
    status, check, solver = out["solve"]
    report.check(status == 200 and not check["violations"], "handler: plans/solve 200 with 0 violations", f"status={solver['status']}")


# ------------------------------------------------------------------------------------------------- driver
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dockerfile", default=DEFAULT_DOCKERFILE, help="path relative to member-a/ (default %(default)s)")
    parser.add_argument("--python", default="3.12", help="python version of the image (default %(default)s)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory")
    args = parser.parse_args(argv)

    dockerfile = ROOT / args.dockerfile
    report = Report()
    if not dockerfile.is_file():
        print(f"no such Dockerfile: {dockerfile}", file=sys.stderr)
        return 2
    model = model_dockerfile(dockerfile)
    ignore = DockerIgnore(ROOT / ".dockerignore")
    is_lambda = "lambda" in model.base.lower()
    tmp = Path(tempfile.mkdtemp(prefix="jp-a-image-"))
    step(f"simulating {args.dockerfile} (FROM {model.base}) in {tmp}")
    try:
        if is_lambda:
            simulate_lambda(model, ignore, tmp, args, report)
        else:
            simulate_api(model, ignore, tmp, args, report)
    except Exception as exc:  # noqa: BLE001 - a crash of the simulation is a failure, not a pass
        report.check(False, "simulation completed", f"{type(exc).__name__}: {exc}")
    finally:
        if args.keep:
            print(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            report.check(not tmp.exists(), "temporary directory removed")
    passed = sum(1 for ok, _, _ in report.rows if ok)
    print(f"\n{passed}/{len(report.rows)} checks passed")
    print(f"docker build not run, image simulated: {'PASS' if report.ok else 'FAIL'}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
