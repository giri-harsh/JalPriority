"""Deterministic synthetic data generator (MEMBER_A F1). Full description: ``data/DATA_GENERATION.md``.

Writes, under the output directory:

    scenarios/scn_sa01_w06.json          main demo seed      (heat alert 2, tanker water ~55% of shortfall)
    scenarios/scn_sa01_w06_severe.json   severe heat seed    (heat alert 3, ~35%)
    scenarios/scn_sa01_w06_mild.json     mild heat seed      (heat alert 1, ~85%)
    truth/<scenario_id>.json             hidden true need for window 6 (only used for run_mae_litres)
    labels.json                          ScenarioSummary.label per seed id
    training/train.csv, training/test.csv  service areas sa_02..sa_33 / sa_34..sa_41, windows 2..30

Determinism: every random draw comes from a numpy PCG64 generator seeded with
``SeedSequence([seed, service_area_number, stream, (area_number)])`` - no wall clock, no salted ``hash()``,
no global RNG state. Floats are rounded before they are written, JSON uses ``json_text`` (indent 2, UTF-8,
LF) and CSV values use fixed formats, so the same seed always gives byte-identical files.

Data-generating process (per service area, per area i, per 12-hour window t):

    det_need[i,t] = population * 3.0 * (1 + 0.04 * max(0, max_temp_c[t] - 35))
                                     * (1 + 0.15 * heat_exposure * alert_level[t] / 3)
    eta[i,t]      = 0.3 * eta[i,t-1] + N(0, 0.08)          (AR(1) log-deviation; eta[i,1] stationary)
    need[i,t]     = round(det_need * exp(eta))             (the regression target)
    expected_piped[i,t] = round(det_need * piped_share * (1 - 0.03 * alert_level[t]))   (noise-free: no leakage)
    received[i,t] = actual piped (expected * exp(N(0, 0.03)), capped at need) + tanker water delivered
                    nearest-first from a limited supply, so distant areas accumulate deprivation debt.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from pathlib import Path

import numpy as np

from ..domain.entities import Scenario, to_iso
from ..repositories.storage_local import json_text
from .geo import distance_matrix, offset_latlon

DEFAULT_SEED = 42

# --------------------------------------------------------------------------- calendar
CURRENT_WINDOW = 6
WINDOW_HOURS = 12
CURRENT_WINDOW_START = datetime(2026, 5, 21, 6, 0, tzinfo=UTC)  # CONTRACT 5.3 example window_start
TRAINING_WINDOWS = 30

# --------------------------------------------------------------------------- service areas
DEMO_SERVICE_AREA = 1
DEMO_N_AREAS = 8
TRAIN_SERVICE_AREAS = tuple(range(2, 34))  # sa_02 .. sa_33
TEST_SERVICE_AREAS = tuple(range(34, 42))  # sa_34 .. sa_41
TRAINING_AREAS_RANGE = (6, 10)  # inclusive

# --------------------------------------------------------------------------- geometry
DEPOT_ID = "dp_01"
DEPOT_NAME = "Central Depot (synthetic)"
DEPOT_LAT, DEPOT_LON = 28.6692, 77.4538
RADIUS_KM = (0.8, 5.8)  # straight-line distance of an area from the depot
MIN_SEPARATION_KM = 0.9

# --------------------------------------------------------------------------- area attributes
POPULATION_RANGE = (1800, 4800)  # rounded to 100
VULN_BASE, VULN_SLOPE, VULN_SIGMA = 0.10, 0.75, 0.06  # vulnerability rises with distance rank
VULN_RANGE = (0.05, 0.95)
HEAT_EXPOSURE_BASE, HEAT_EXPOSURE_SLOPE, HEAT_EXPOSURE_SIGMA = 0.35, 0.50, 0.07
HEAT_EXPOSURE_RANGE = (0.10, 0.98)
PIPED_SHARE_RANGE = (0.55, 0.85)
VOICE_BASE, VOICE_SLOPE, VOICE_SIGMA = 1.62, 1.25, 0.07  # voice falls with vulnerability
VOICE_RANGE = (0.5, 1.5)

# --------------------------------------------------------------------------- need / supply model
LITRES_PER_PERSON = 3.0
TEMP_BASE_C, TEMP_SLOPE = 35.0, 0.04
HEAT_EXPOSURE_NEED_SLOPE = 0.15
AR_COEF, NEED_SIGMA = 0.3, 0.08
PIPED_ALERT_CUT = 0.03
PIPED_DELIVERY_SIGMA = 0.03
HISTORY_SUPPLY_SHARE_DEMO = (0.45, 0.70)  # tanker water / observed shortfall in sa_01 windows 1-5
HISTORY_SUPPLY_SHARE_TRAINING = (0.35, 0.95)
ALERT_THRESHOLDS_C = (38.0, 44.0, 47.0)  # <38: 0, [38,44): 1, [44,47): 2, >=47: 3
HEAT_INDEX_OFFSET_C = (2.0, 6.0)  # humidity: heat_index = max_temp + U(2, 6)

# --------------------------------------------------------------------------- weather
DEMO_HISTORY_TEMPS_C = (40.6, 41.4, 41.9, 42.0, 43.1)  # sa_01 windows 1-5 (all heat alert 1)
# Per-service-area mean max temperature, stratified by heat regime = sa_number % 4 so that both splits
# cover every alert level (cool, warm, hot, extreme).
TRAINING_HEAT_REGIMES_C = ((38.0, 40.0), (40.5, 42.5), (42.5, 44.5), (44.5, 46.5))
TRAINING_TEMP_TREND_C = (-0.05, 0.15)  # per window
TRAINING_TEMP_AR, TRAINING_TEMP_SIGMA = 0.6, 1.0
TEMP_CLIP_C = (30.0, 50.0)

# --------------------------------------------------------------------------- dispatch timing
DELIVERY_FIRST_MIN, DELIVERY_STEP_MIN, DELIVERY_JITTER_MIN = 60, 45, 25
REQUEST_LEAD_BASE_MIN, REQUEST_LEAD_SPAN_MIN, REQUEST_LEAD_SIGMA_MIN = -55.0, 225.0, 6.0
REQUEST_VOICE_WEIGHT, REQUEST_NEARNESS_WEIGHT = 0.6, 0.4

# Decorrelate request order from depot distance for NEAR / MID areas of the sa_01 seeds only, so FCFS and
# nearest-first serve different areas. Key = distance rank (0 = nearest); value = (voice, request lead in minutes
# before window start). The far, vulnerable, late-requesting areas (ranks 5-7) are never overridden.
#   rank 3 (mid distance): LOUD (requests 1.45x its shortfall) and requests EARLIEST of all -> FCFS serves it first
#   rank 1 (near):         QUIET (0.70x) and requests late-ish (before the far wards) -> nearest-first serves it first
# Voice is latent and absent from the training CSV, so the training path and the feature set are unchanged.
DEMO_REQUEST_OVERRIDES = {3: (1.45, 215), 1: (0.70, 10)}

# --------------------------------------------------------------------------- fleet
N_TANKERS = 4
TANKER_CAPACITY_UNITS = (100, 120)  # x 100 L, inclusive -> 10,000 .. 12,000 L
MAX_ROUTE_KM = 60.0

TRAINING_COLUMNS = (
    "service_area_id", "area_id", "window_index", "max_temp_c", "heat_index_c", "alert_level",
    "heat_exposure", "population", "last_need_litres", "mean_need_last3_litres", "last_received_litres",
    "expected_piped_litres", "need_litres",
)  # fmt: skip


class Stream(IntEnum):
    """Independent random streams; part of every SeedSequence key."""

    GEOMETRY = 1
    ATTRIBUTES = 2
    NEED_NOISE = 3
    PIPED_NOISE = 4
    WEATHER = 5
    SUPPLY = 6
    DELIVERY_TIME = 7
    REQUEST = 8
    TANKERS = 9
    SIZE = 10


@dataclass(frozen=True)
class SeedVariant:
    scenario_id: str
    label_head: str
    max_temp_c: float
    heat_index_c: float
    supply_share: float  # tanker water / total true shortfall in window 6

    @property
    def alert_level(self) -> int:
        return alert_level_for(self.max_temp_c)

    @property
    def label(self) -> str:
        return (
            f"{self.label_head} · heat alert {self.alert_level} · "
            f"supply ≈{round(self.supply_share * 100)}% of shortfall"
        )


SEED_VARIANTS = (
    SeedVariant("scn_sa01_w06", "Main demo", 44.5, 49.0, 0.55),
    SeedVariant("scn_sa01_w06_severe", "Severe heat", 47.5, 52.5, 0.35),
    SeedVariant("scn_sa01_w06_mild", "Mild heat", 41.5, 45.0, 0.85),
)


@dataclass(frozen=True)
class Weather:
    max_temp_c: float
    heat_index_c: float
    alert_level: int


@dataclass(frozen=True)
class AreaProfile:
    """Static attributes of one area. ``piped_share`` and ``voice`` are latent (never published)."""

    number: int
    lat: float
    lon: float
    depot_km: float
    distance_rank: int  # 0 = nearest to the depot
    population: int
    vulnerability_index: float
    heat_exposure: float
    piped_share: float
    voice: float

    @property
    def area_id(self) -> str:
        return f"ar_{self.number:02d}"

    @property
    def name(self) -> str:
        return f"Ward {self.number:02d} (synthetic)"


@dataclass
class AreaPanel:
    """Simulated per-window series of one area; list index 0 is window 1."""

    det_need: list[float]
    need: list[int]
    expected_piped: list[int]
    received: list[int]
    tanker: list[int]
    delivery_minute: list[int | None]  # minutes after window start of the tanker drop, None if no drop


@dataclass(frozen=True)
class ServiceArea:
    number: int
    areas: tuple[AreaProfile, ...]
    distance_km: dict[str, dict[str, float]]

    @property
    def service_area_id(self) -> str:
        return f"sa_{self.number:02d}"


# --------------------------------------------------------------------------- small pure helpers
def _rng(seed: int, *keys: int) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence([seed, *keys])))


def _clip(value: float, bounds: tuple[float, float]) -> float:
    return min(bounds[1], max(bounds[0], value))


def _round100(value: float) -> int:
    return int(math.floor(value / 100.0 + 0.5)) * 100


def alert_level_for(max_temp_c: float) -> int:
    return sum(max_temp_c >= t for t in ALERT_THRESHOLDS_C)


def window_start(window_index: int) -> datetime:
    return CURRENT_WINDOW_START + timedelta(hours=WINDOW_HOURS * (window_index - CURRENT_WINDOW))


def deterministic_need(population: int, heat_exposure: float, weather: Weather) -> float:
    """Noise-free need of an area for one window."""
    temp_factor = 1 + TEMP_SLOPE * max(0.0, weather.max_temp_c - TEMP_BASE_C)
    exposure_factor = 1 + HEAT_EXPOSURE_NEED_SLOPE * heat_exposure * weather.alert_level / 3
    return population * LITRES_PER_PERSON * temp_factor * exposure_factor


def expected_piped(det_need: float, piped_share: float, alert_level: int) -> int:
    """Piped supply expected for a window; depends only on the noise-free need (no target leakage)."""
    return round(det_need * piped_share * (1 - PIPED_ALERT_CUT * alert_level))


def lag_features(needs: Sequence[int], received: Sequence[int]) -> tuple[int, float, int]:
    """``(last_need, mean_need_last3, last_received)`` from the windows before t, oldest first.

    The same definitions turn a Scenario's history into model features at prediction time:
    ``history[-1].need_litres``, the mean of the last up to 3 history needs (1 dp), ``history[-1].received_litres``.
    """
    last3 = list(needs[-3:])
    return int(needs[-1]), round(sum(last3) / len(last3), 1), int(received[-1])


# --------------------------------------------------------------------------- service area construction
def _place_areas(seed: int, sa_number: int, n_areas: int) -> list[tuple[float, float]]:
    """Rejection-sample (east_km, north_km) offsets with a minimum separation between areas."""
    rng = _rng(seed, sa_number, Stream.GEOMETRY)
    points: list[tuple[float, float]] = []
    while len(points) < n_areas:
        radius = math.sqrt(rng.uniform(RADIUS_KM[0] ** 2, RADIUS_KM[1] ** 2))  # uniform over the annulus
        angle = rng.uniform(0.0, 2 * math.pi)
        point = (radius * math.cos(angle), radius * math.sin(angle))
        if all(math.dist(point, q) >= MIN_SEPARATION_KM for q in points):
            points.append(point)
    return points


def build_service_area(seed: int, sa_number: int, n_areas: int) -> ServiceArea:
    points = _place_areas(seed, sa_number, n_areas)
    coords = [offset_latlon(DEPOT_LAT, DEPOT_LON, east, north) for east, north in points]
    nodes = [(DEPOT_ID, DEPOT_LAT, DEPOT_LON)] + [(f"ar_{k + 1:02d}", lat, lon) for k, (lat, lon) in enumerate(coords)]
    matrix = distance_matrix(nodes)
    depot_km = [matrix[DEPOT_ID][f"ar_{k + 1:02d}"] for k in range(n_areas)]
    order = sorted(range(n_areas), key=lambda k: (depot_km[k], k))
    rank = {k: r for r, k in enumerate(order)}

    rng = _rng(seed, sa_number, Stream.ATTRIBUTES)
    areas = []
    for k in range(n_areas):
        rank_norm = rank[k] / (n_areas - 1)
        population = _round100(rng.uniform(*POPULATION_RANGE))
        vulnerability = round(_clip(VULN_BASE + VULN_SLOPE * rank_norm + rng.normal(0, VULN_SIGMA), VULN_RANGE), 2)
        heat_exposure = round(
            _clip(
                HEAT_EXPOSURE_BASE + HEAT_EXPOSURE_SLOPE * vulnerability + rng.normal(0, HEAT_EXPOSURE_SIGMA),
                HEAT_EXPOSURE_RANGE,
            ),
            2,
        )
        piped_share = round(rng.uniform(*PIPED_SHARE_RANGE), 3)
        voice = round(_clip(VOICE_BASE - VOICE_SLOPE * vulnerability + rng.normal(0, VOICE_SIGMA), VOICE_RANGE), 3)
        lat, lon = coords[k]
        areas.append(
            AreaProfile(
                number=k + 1, lat=lat, lon=lon, depot_km=depot_km[k], distance_rank=rank[k],
                population=population, vulnerability_index=vulnerability, heat_exposure=heat_exposure,
                piped_share=piped_share, voice=voice,
            )  # fmt: skip
        )
    return ServiceArea(number=sa_number, areas=tuple(areas), distance_km=matrix)


def _weather(max_temp_c: float, heat_offset: float) -> Weather:
    temp = round(max_temp_c, 1)
    return Weather(max_temp_c=temp, heat_index_c=round(temp + heat_offset, 1), alert_level=alert_level_for(temp))


def training_weather(seed: int, sa_number: int, n_windows: int) -> list[Weather]:
    """AR(1) heat series around a per-service-area mean (stratified heat regime) with a linear trend."""
    rng = _rng(seed, sa_number, Stream.WEATHER)
    mean = rng.uniform(*TRAINING_HEAT_REGIMES_C[sa_number % len(TRAINING_HEAT_REGIMES_C)])
    trend = rng.uniform(*TRAINING_TEMP_TREND_C)
    x = rng.normal(0, TRAINING_TEMP_SIGMA / math.sqrt(1 - TRAINING_TEMP_AR**2))
    out = []
    for w in range(n_windows):
        if w:
            x = TRAINING_TEMP_AR * x + rng.normal(0, TRAINING_TEMP_SIGMA)
        temp = _clip(mean + trend * w + x, TEMP_CLIP_C)
        out.append(_weather(temp, rng.uniform(*HEAT_INDEX_OFFSET_C)))
    return out


def demo_history_weather(seed: int) -> list[Weather]:
    rng = _rng(seed, DEMO_SERVICE_AREA, Stream.WEATHER)
    return [_weather(t, rng.uniform(*HEAT_INDEX_OFFSET_C)) for t in DEMO_HISTORY_TEMPS_C]


def need_noise(seed: int, sa_number: int, area_number: int, n_windows: int) -> list[float]:
    """AR(1) log-deviation path eta[1..n]; eta[1] is drawn from the stationary distribution."""
    rng = _rng(seed, sa_number, Stream.NEED_NOISE, area_number)
    eta = [rng.normal(0, NEED_SIGMA / math.sqrt(1 - AR_COEF**2))]
    for _ in range(n_windows - 1):
        eta.append(AR_COEF * eta[-1] + rng.normal(0, NEED_SIGMA))
    return eta


# --------------------------------------------------------------------------- history simulation
def simulate_history(
    seed: int,
    sa: ServiceArea,
    weather: Sequence[Weather],
    eta: dict[int, list[float]],
    supply_share_range: tuple[float, float],
) -> dict[int, AreaPanel]:
    """Simulate windows 1..len(weather): need, expected/actual piped, nearest-first tanker deliveries."""
    n_windows = len(weather)
    supply_rng = _rng(seed, sa.number, Stream.SUPPLY)
    time_rng = _rng(seed, sa.number, Stream.DELIVERY_TIME)
    piped_rngs = {a.number: _rng(seed, sa.number, Stream.PIPED_NOISE, a.number) for a in sa.areas}
    panels = {a.number: AreaPanel([], [], [], [], [], []) for a in sa.areas}
    nearest_first = sorted(sa.areas, key=lambda a: (a.distance_rank, a.number))

    for w in range(n_windows):
        wx = weather[w]
        actual_piped: dict[int, int] = {}
        for a in sa.areas:
            p = panels[a.number]
            det = deterministic_need(a.population, a.heat_exposure, wx)
            need = round(det * math.exp(eta[a.number][w]))
            exp_piped = expected_piped(det, a.piped_share, wx.alert_level)
            actual_piped[a.number] = min(need, round(exp_piped * math.exp(piped_rngs[a.number].normal(0, PIPED_DELIVERY_SIGMA))))
            p.det_need.append(det)
            p.need.append(need)
            p.expected_piped.append(exp_piped)

        observed_shortfall = {n: panels[n].need[w] - actual_piped[n] for n in panels}
        remaining = _round100(supply_rng.uniform(*supply_share_range) * sum(observed_shortfall.values()))
        position = 0
        for a in nearest_first:
            give = min(remaining, observed_shortfall[a.number] // 100 * 100)
            p = panels[a.number]
            jitter = int(time_rng.integers(0, DELIVERY_JITTER_MIN + 1))  # drawn for every area: stable streams
            if give > 0:
                p.delivery_minute.append(DELIVERY_FIRST_MIN + DELIVERY_STEP_MIN * position + jitter)
                position += 1
            else:
                p.delivery_minute.append(None)
            remaining -= give
            p.tanker.append(give)
            p.received.append(actual_piped[a.number] + give)
    return panels


# --------------------------------------------------------------------------- training data
def training_rows(sa: ServiceArea, weather: Sequence[Weather], panels: dict[int, AreaPanel]) -> list[list[str]]:
    """One row per (area, window t >= 2), values formatted exactly as written to the CSV."""
    rows = []
    for a in sa.areas:
        p = panels[a.number]
        for t in range(2, len(weather) + 1):
            wx = weather[t - 1]
            last_need, mean3, last_received = lag_features(p.need[: t - 1], p.received[: t - 1])
            rows.append([
                sa.service_area_id, a.area_id, str(t), f"{wx.max_temp_c:.1f}", f"{wx.heat_index_c:.1f}",
                str(wx.alert_level), f"{a.heat_exposure:.2f}", str(a.population), str(last_need), f"{mean3:.1f}",
                str(last_received), str(p.expected_piped[t - 1]), str(p.need[t - 1]),
            ])  # fmt: skip
    return rows


def training_service_area(seed: int, sa_number: int) -> tuple[ServiceArea, list[Weather], dict[int, AreaPanel]]:
    size_rng = _rng(seed, sa_number, Stream.SIZE)
    n_areas = int(size_rng.integers(TRAINING_AREAS_RANGE[0], TRAINING_AREAS_RANGE[1] + 1))
    sa = build_service_area(seed, sa_number, n_areas)
    weather = training_weather(seed, sa_number, TRAINING_WINDOWS)
    eta = {a.number: need_noise(seed, sa_number, a.number, TRAINING_WINDOWS) for a in sa.areas}
    panels = simulate_history(seed, sa, weather, eta, HISTORY_SUPPLY_SHARE_TRAINING)
    return sa, weather, panels


def training_csv(seed: int, sa_numbers: Sequence[int]) -> str:
    lines = [",".join(TRAINING_COLUMNS)]
    for sa_number in sa_numbers:
        sa, weather, panels = training_service_area(seed, sa_number)
        lines.extend(",".join(row) for row in training_rows(sa, weather, panels))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- demo seeds (sa_01)
@dataclass(frozen=True)
class DemoBase:
    """Everything the three sa_01 seeds share: geometry, attributes, history (windows 1-5), fleet."""

    seed: int
    sa: ServiceArea
    history_weather: list[Weather]
    panels: dict[int, AreaPanel]
    eta_current: dict[int, float]
    request_noise_min: dict[int, float]
    tanker_capacities: tuple[int, ...]


def build_demo_base(seed: int) -> DemoBase:
    sa = build_service_area(seed, DEMO_SERVICE_AREA, DEMO_N_AREAS)
    history_weather = demo_history_weather(seed)
    eta = {a.number: need_noise(seed, DEMO_SERVICE_AREA, a.number, CURRENT_WINDOW) for a in sa.areas}
    panels = simulate_history(seed, sa, history_weather, eta, HISTORY_SUPPLY_SHARE_DEMO)
    request_rng = _rng(seed, DEMO_SERVICE_AREA, Stream.REQUEST)
    tanker_rng = _rng(seed, DEMO_SERVICE_AREA, Stream.TANKERS)
    capacities = tuple(
        int(tanker_rng.integers(TANKER_CAPACITY_UNITS[0], TANKER_CAPACITY_UNITS[1] + 1)) * 100 for _ in range(N_TANKERS)
    )
    return DemoBase(
        seed=seed,
        sa=sa,
        history_weather=history_weather,
        panels=panels,
        eta_current={n: path[CURRENT_WINDOW - 1] for n, path in eta.items()},
        request_noise_min={a.number: float(request_rng.normal(0, REQUEST_LEAD_SIGMA_MIN)) for a in sa.areas},
        tanker_capacities=capacities,
    )


def _demo_voice(area: AreaProfile) -> float:
    return DEMO_REQUEST_OVERRIDES[area.distance_rank][0] if area.distance_rank in DEMO_REQUEST_OVERRIDES else area.voice


def _request_lead_minutes(base: DemoBase, area: AreaProfile) -> int:
    """Louder (higher voice) and nearer areas request earlier; positive = before window start.

    Areas listed in ``DEMO_REQUEST_OVERRIDES`` use their fixed lead instead (see there)."""
    if area.distance_rank in DEMO_REQUEST_OVERRIDES:
        return DEMO_REQUEST_OVERRIDES[area.distance_rank][1]
    loudness = (area.voice - VOICE_RANGE[0]) / (VOICE_RANGE[1] - VOICE_RANGE[0])
    nearness = 1 - area.distance_rank / (len(base.sa.areas) - 1)
    score = REQUEST_VOICE_WEIGHT * loudness + REQUEST_NEARNESS_WEIGHT * nearness
    return round(REQUEST_LEAD_BASE_MIN + REQUEST_LEAD_SPAN_MIN * score + base.request_noise_min[area.number])


def build_seed_scenario(base: DemoBase, variant: SeedVariant) -> tuple[Scenario, dict]:
    """Return ``(scenario, truth)`` for one sa_01 variant (window 6)."""
    current_weather = Weather(variant.max_temp_c, variant.heat_index_c, variant.alert_level)
    start = window_start(CURRENT_WINDOW)

    true_need: dict[str, int] = {}
    areas = []
    shortfalls = {}
    for a in base.sa.areas:
        det = deterministic_need(a.population, a.heat_exposure, current_weather)
        need = round(det * math.exp(base.eta_current[a.number]))
        piped = expected_piped(det, a.piped_share, current_weather.alert_level)
        true_need[a.area_id] = need
        shortfalls[a.area_id] = max(0, need - piped)

        p = base.panels[a.number]
        delivered = [w for w in range(len(p.tanker)) if p.tanker[w] > 0]
        if delivered:
            w = delivered[-1]
            last_litres = p.tanker[w]
            last_at = to_iso(window_start(w + 1) + timedelta(minutes=p.delivery_minute[w]))
        else:
            last_litres, last_at = 0, None

        areas.append({
            "area_id": a.area_id, "name": a.name, "lat": a.lat, "lon": a.lon,
            "population": a.population, "vulnerability_index": a.vulnerability_index, "vulnerability_available": True,
            "heat_exposure": a.heat_exposure, "expected_piped_litres": piped,
            "requested_litres": _round100(shortfalls[a.area_id] * _demo_voice(a)),
            "request_time": to_iso(start - timedelta(minutes=_request_lead_minutes(base, a))),
            "last_delivery_litres": last_litres, "last_delivery_at": last_at,
            "history": [
                {"window_index": w + 1, "need_litres": p.need[w], "received_litres": p.received[w],
                 "max_temp_c": base.history_weather[w].max_temp_c, "alert_level": base.history_weather[w].alert_level}
                for w in range(len(p.need))
            ],
        })  # fmt: skip

    scenario = Scenario.model_validate({
        "schema_version": "1.0",
        "scenario_id": variant.scenario_id,
        "parent_scenario_id": None,
        "service_area_id": base.sa.service_area_id,
        "service_area_name": f"Synthetic Service Area {base.sa.number:02d}",
        "window_index": CURRENT_WINDOW,
        "window_start": to_iso(start),
        "window_hours": WINDOW_HOURS,
        "is_synthetic": True,
        "seed": base.seed,
        "heat": {"alert_level": current_weather.alert_level, "max_temp_c": current_weather.max_temp_c,
                 "heat_index_c": current_weather.heat_index_c},
        "supply": {"tanker_water_available_litres": _round100(variant.supply_share * sum(shortfalls.values()))},
        "depot": {"depot_id": DEPOT_ID, "name": DEPOT_NAME, "lat": DEPOT_LAT, "lon": DEPOT_LON},
        "tankers": [
            {"tanker_id": f"tk_{k + 1:02d}", "capacity_litres": cap, "status": "available", "max_route_km": MAX_ROUTE_KM}
            for k, cap in enumerate(base.tanker_capacities)
        ],
        "areas": areas,
        "distance_km": base.sa.distance_km,
        "disruptions_applied": [],
    })  # fmt: skip
    truth = {"scenario_id": variant.scenario_id, "window_index": CURRENT_WINDOW, "true_need_litres": true_need}
    return scenario, truth


def build_seed_scenarios(seed: int = DEFAULT_SEED) -> list[tuple[Scenario, dict]]:
    base = build_demo_base(seed)
    return [build_seed_scenario(base, v) for v in SEED_VARIANTS]


# --------------------------------------------------------------------------- output
def generate_files(seed: int = DEFAULT_SEED) -> dict[str, str]:
    """``{relpath: file text}`` for every generated artefact (pure; nothing is written)."""
    if seed < 0:
        raise ValueError("seed must be a non-negative integer")
    files: dict[str, str] = {}
    labels: dict[str, str] = {}
    for variant, (scenario, truth) in zip(SEED_VARIANTS, build_seed_scenarios(seed), strict=True):
        files[f"scenarios/{scenario.scenario_id}.json"] = json_text(scenario.model_dump(mode="json"))
        files[f"truth/{scenario.scenario_id}.json"] = json_text(truth)
        labels[scenario.scenario_id] = variant.label
    files["labels.json"] = json_text(labels)
    files["training/train.csv"] = training_csv(seed, TRAIN_SERVICE_AREAS)
    files["training/test.csv"] = training_csv(seed, TEST_SERVICE_AREAS)
    return files


def write_files(out_dir: str | Path, seed: int = DEFAULT_SEED) -> dict[str, Path]:
    """Generate everything and write it under ``out_dir`` (UTF-8, LF). Returns ``{relpath: written path}``."""
    out = Path(out_dir)
    written: dict[str, Path] = {}
    for relpath, text in generate_files(seed).items():
        path = out.joinpath(*relpath.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        written[relpath] = path
    return written
