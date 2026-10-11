# SUMMARY_A.md — Member A ("Need → Plan"), explained for the project owner

> **SYNTHETIC DATA · SIMULATION RESULTS.** Everything below runs on generated data. Model errors are measured on that synthetic world, never on reality.
> All numbers in this file were read from the repository files or from real runs of the code (data JSON, `models/model_meta.json`, `scripts/perf_report.md`, the live solve on `scn_sa01_w06`, `pytest --collect-only`, `npm test`).

---

## 1. The product in 5 lines

1. **Problem.** In a heat crisis a city has too little tanker water. Whoever phones first, or lives closest to the depot, gets served, while far, vulnerable, quiet neighbourhoods are abandoned and fall deeper into "deprivation debt".
2. **Users.** Municipal dispatch officers who decide, window by window (12 h), which neighbourhood gets how many litres from which tanker, in which stop order.
3. **Member A (this folder) = "Need → Plan".** What is happening (crisis snapshot), who needs water (ML shortage prediction), and the *optimal fair plan with reasons* (CP-SAT optimizer, routes, plain-language explanations), plus the two screens that show it.
4. **Member B = "Fairness, Evidence & Control".** Debt ledger, fairness weights, the FCFS / nearest-first baselines and the Compare screen, replanning, approval / audit, login and the app shell. B calls A over HTTP; A never calls B.
5. **The contract** (`contracts/CONTRACT.md` v1.0.0, frozen) fixes every field, formula and error code, so the two halves can be built separately and merged.

---

## 2. End-to-end flow (load a scenario → plan on screen)

```
 Browser (B's shell, or A's harness on :5174)                    A API (FastAPI :8001, /a/v1)
 ┌──────────────────────────────┐   GET /scenarios                ┌────────────────────────────────────┐
 │ SnapshotScreen               │ ───────────────────────────────▶│ routes_scenarios.list_scenarios     │
 │  (screens/SnapshotScreen.tsx)│   GET /scenarios/{id}           │  → ScenarioService → ScenarioRepo   │
 │  picks scenario              │ ───────────────────────────────▶│     (data/scenarios/*.json)         │
 │  emits scenario_loaded       │   GET /scenarios/{id}/predictions│ routes_predictions.get_predictions │
 │                              │ ───────────────────────────────▶│  → PredictionService                │
 │                              │   GET /model                    │     ml/features.build_feature_frame │
 │                              │ ───────────────────────────────▶│     ModelRepository.predict (XGB)   │
 └──────────────┬───────────────┘                                 │     F-1 shortfall, SHAP top-3       │
                │ "Generate plan" (B's shell / harness)           └────────────────────────────────────┘
                ▼   POST /plans/solve {scenario_id, weights, debt_by_area}
 ┌──────────────────────────────┐                                 ┌────────────────────────────────────┐
 │ DispatchPlanScreen           │ ───────────────────────────────▶│ routes_plans.solve_plan             │
 │  map + table + panels        │                                 │  → PlanService.solve                │
 │  emits explanation_viewed    │                                 │    1 load scenario (404 if unknown) │
 └──────────────────────────────┘                                 │    2 predictions (cached)           │
                                                                  │    3 domain/priority: F-2..F-5      │
                                                                  │    4 optimization/allocation (CP-SAT)│
                                                                  │       └ greedy fallback             │
                                                                  │    5 optimization/routing (TSP)     │
                                                                  │    6 services/explanation_service   │
                                                                  │    7 domain/constraints (F-15 check)│
                                                                  │  → PlanResult (strict contract JSON)│
                                                                  └────────────────────────────────────┘
```

Step by step, with the code that does it:

1. **Boot.** `jp_a/main.py:create_app` builds the app; `api/deps.py:build_container` wires `ScenarioRepository` (`repositories/scenario_repo.py`), `ModelRepository` (`repositories/model_repo.py`), `PredictionService`, `ScenarioService`, `PlanService`. The model artefact is loaded at startup (a failure only logs a warning; routes then answer 503).
2. **Every request** passes `api/middleware.py:RequestContextMiddleware` (request id), then `api/auth.py:require_auth` (JWT), and errors become the contract envelope (`api/errors.py`).
3. **Load scenario.** `ScenarioRepository.get(id)` reads `data/scenarios/<id>.json` (or `data/derived/<id>.json`) and parses it with the strict `domain/entities.py:Scenario` model.
4. **Predict.** `PredictionService.predict_for_scenario` → `ml/features.py:build_feature_frame` (9 features per area) → `ModelRepository.predict` (XGBoost + SHAP contributions) → per-area `predicted_need`, F-1 `shortfall = max(0, need − expected_piped)`, top-3 features, `run_mae_litres` against hidden truth. Result is cached per scenario id.
5. **Priorities.** `domain/priority.py` computes urgency U, normalised debt D̂, vulnerability V and the priority weight `p` per area; `rank_areas` orders them.
6. **Allocate.** `optimization/allocation.py:build_problem` + CP-SAT decide litres per (tanker, area). If CP-SAT yields nothing, `solve_greedy` answers.
7. **Route.** `optimization/routing.py:sequence_stops` orders each tanker's stops (exact TSP ≤ 7 stops).
8. **Explain.** `services/explanation_service.py` + `domain/reasons.py` attach reason codes and `reason_text`.
9. **Assemble & verify.** `services/plan_service.py:build_plan` builds the `PlanResult`; `domain/constraints.py:build_constraint_check` recomputes F-15 independently from the deliveries.
10. **Screen.** B's shell (or the harness) wraps the `PlanResult` into a `PlanRecord` and passes it as props to `DispatchPlanScreen`, which fetches the scenario again only for map coordinates.

---

## 3. Synthetic data

Code: `backend/jp_a/data/generator.py` (+ `geo.py`, `debt.py`, `cli.py`); run with `scripts/generate_data.sh`. Full parameter list: `data/DATA_GENERATION.md`.

### 3.1 How the generator works
* **Determinism.** Every random number comes from `PCG64(SeedSequence([seed, service_area, stream, area]))` with 10 independent streams (geometry, attributes, need noise, piped noise, weather, supply, delivery time, request, tankers, size). No wall clock, no `hash()`. Same seed 42 ⇒ byte-identical files (tested, including equality with the committed files).
* **Need (litres per 12 h window).** `det_need = population × 3.0 L × (1 + 0.04·max(0, max_temp_c − 35)) × (1 + 0.15·heat_exposure·alert_level/3)`; the true need is `round(det_need × exp(η))` where `η_t = 0.3·η_{t−1} + N(0, 0.08)` (AR(1) carry-over on the log deviation).
* **Piped supply.** `expected_piped = round(det_need × piped_share × (1 − 0.03·alert_level))`, `piped_share ~ U(0.55, 0.85)` per area (latent, never published). It uses only the **noise-free** need, so it cannot leak the target. Actual piped water in history = expected × exp(N(0, 0.03)), capped at need.
* **History (windows 1–5).** Each window the utility has tanker water = `U(0.45, 0.70) ×` the observed shortfall (training areas: `U(0.35, 0.95)`), delivered **nearest-first** in 100 L units. `received = actual_piped + tanker`, never above need. Because far areas come last, they stay under-served and accumulate debt. `last_delivery_*` come from the latest drop (null for `ar_03`, `ar_07`, which never got one).
* **Debt.** `unmet = max(0, need − received)`; `D_w = round(0.7·D_{w−1} + unmet_w)`, `D_0 = 0` (F-10, λ = 0.3; `data/debt.py`). Debt after window 5, identical in all seeds: ar_01 814, ar_02 6,356, ar_03 10,588, ar_04 158, ar_05 9,418, ar_06 212, ar_07 21,308, ar_08 184.
* **Geometry & distances.** Areas are sampled in an annulus 0.8–5.8 km around the depot dp_01 (28.6692, 77.4538), ≥ 0.9 km apart. Road distance = `1.3 × haversine`, rounded to 2 dp, full symmetric matrix. Resulting depot distances run 3.26–6.80 km.
* **Vulnerability, heat exposure, voice.** With `r = distance_rank/(n−1)`: vulnerability `= 0.10 + 0.75·r + N(0, 0.06)` (far = more vulnerable); heat exposure `= 0.35 + 0.50·V + N(0, 0.07)`; **voice** (how loudly an area requests) `= 1.62 − 1.25·V + N(0, 0.07)` clipped to [0.5, 1.5] — vulnerable areas are quieter. Voice is latent.
* **Requests.** `requested_litres = round100(true_shortfall × voice)`. `request_time = window_start − lead`, `lead = −55 + 225·(0.6·loudness + 0.4·nearness) + N(0, 6)` minutes: louder and nearer areas ask earlier.
* **Weather.** Alert level from max temp: `<38 → 0`, `[38,44) → 1`, `[44,47) → 2`, `≥47 → 3`; `heat_index = max_temp + U(2, 6)`. The sa_01 history temperatures are fixed (40.6, 41.4, 41.9, 42.0, 43.1 °C).
* **Fleet.** 4 tankers, `max_route_km` 60, capacities 10,600 / 11,700 / 10,800 / 10,000 L (43,100 L total).

### 3.2 The decorrelation override (added after the first release)
Originally request order equalled distance order, so FCFS and nearest-first gave identical plans. `DEMO_REQUEST_OVERRIDES = {3: (1.45, 215), 2: (0.70, 10)}` (key = distance rank, value = (voice, lead minutes)) now makes **`ar_01`** (rank 3, 5.8 km, mid-distance) *loud* (asks 1.45× its shortfall) and the **earliest** requester (02:25), and **`ar_08`** (rank 2, 4.57 km, near) *quiet* (0.70×) and late-ish (05:50, before the far wards). The far vulnerable wards are never overridden. Voice is not a feature, the training CSVs and truth files are byte-identical to before, so the model was not retrained.

### 3.3 The three seed scenarios (`data/scenarios/`)
They share geometry, attributes, history, fleet and request times; they differ only in window-6 heat, hence in need, expected piped supply, shortfall, requests and supply.

| seed | demonstrates | max temp / heat index | alert | supply | true total shortfall | supply / shortfall |
|---|---|---|---|---|---|---|
| `scn_sa01_w06` | the main demo | 44.5 / 49.0 °C | 2 | 24,400 L | 44,403 L | 0.550 |
| `scn_sa01_w06_severe` | extreme heat, very scarce water | 47.5 / 52.5 °C | 3 | 18,300 L | 52,402 L | 0.349 |
| `scn_sa01_w06_mild` | milder heat, water nearly sufficient | 41.5 / 45.0 °C | 1 | 31,600 L | 37,189 L | 0.850 |

### 3.4 The demo property — and why it matters
Per MEMBER_A F1, `scn_sa01_w06` must contain ≥ 2 areas that are simultaneously vulnerable (V ≥ 0.6), among the 3 farthest, among the 3 latest requesters, under-requesting (requested < shortfall) and carrying debt, plus total shortfall ≥ 1.6 × supply and a loud near early area that over-requests. Real values (true shortfall):

| area | km | V | request | requested / shortfall | debt |
|---|---|---|---|---|---|
| ar_03 | 6.80 | 0.89 | 06:46 | 1,600 / 2,872 (0.56) | 10,588 |
| ar_07 | 6.76 | 0.78 | 06:20 | 4,100 / 7,302 (0.56) | 21,308 |
| ar_02 | 6.74 | 0.63 | 06:10 | 2,800 / 4,433 (0.63) | 6,356 |
| ar_04 (loud, near) | 3.26 | 0.12 | 03:03 | 15,000 / 10,021 (1.50) | 158 |
| ar_06 (loud, near) | 4.45 | 0.09 | 03:33 | 8,600 / 6,028 (1.43) | 212 |
| ar_01 (loud, mid; override) | 5.80 | 0.44 | 02:25 | 7,800 / 5,366 (1.45) | 814 |

Shortfall/supply = 1.82×. **Why it matters:** it is the demo moment. Any "who asked first / who is nearest" rule drains the water into the loud areas and starves exactly the people with the highest vulnerability and debt. The property is asserted in tests with the *real model's* predictions (total predicted shortfall 44,125 L ≈ 1.81× supply).

### 3.5 Training/test split and hidden truth
* `data/training/train.csv`: service areas `sa_02`–`sa_33` (32 service areas, 258 areas, **7,482 rows**); `test.csv`: `sa_34`–`sa_41` (8 service areas, 65 areas, **1,885 rows**); windows 2–30; `sa_01` is never trained on. Weather regimes are stratified by `sa_number % 4` so both splits cover every alert level (train rows at alert 0/1/2/3: 292/3,770/2,400/1,020; test: 210/950/631/94).
* **Hidden truth** `data/truth/<scenario>.json` = `{scenario_id, window_index: 6, true_need_litres: {area: int}}`. It is used only to compute `run_mae_litres`; it is never copied into a scenario.

---

## 4. The ML model

* **Target:** `need_litres` of the 12-hour window being planned. **Shortfall** (what tankers must cover) = `max(0, predicted_need − expected_piped)` (F-1).
* **Features (exact names, `ml/features.py`):**

| feature | meaning | why |
|---|---|---|
| `max_temp_c`, `heat_index_c`, `alert_level` | planned-window heat | heat drives consumption |
| `heat_exposure` | static per area | urban-heat effect on need |
| `population` | static per area | scales need |
| `last_need_litres` | need in the last window | persistence / AR carry-over |
| `mean_need_last3_litres` | mean of last ≤ 3 needs (1 dp) | smoother level estimate |
| `last_received_litres` | what the area actually got | service history |
| `expected_piped_litres` | planned piped supply | the shortfall base |

* **Hyperparameters:** `XGBRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.9, colsample_bytree=0.9, objective=reg:squarederror, tree_method=hist, random_state=42)`; XGBoost 2.1.3; trained on 7,482 rows; artefact `models/shortage_xgb_v1.json` + `model_meta.json`.
* **Results on the unseen test service areas (1,885 rows):**

| model | MAE (L) | RMSE (L) |
|---|---|---|
| XGBoost | **883.6** | **1,166.2** |
| last-observed baseline (`last_need_litres`) | 1,161.7 | 1,523.4 |

  ≈ 24% lower MAE. A validation holdout (`sa_30`–`sa_33` carved out of train) gave XGB 916.4 / 1,200.5 vs baseline 1,140.8 / 1,472.3.
* **Top features (gain importance):** `mean_need_last3_litres` 0.4636, `last_need_litres` 0.3486, `population` 0.096, `alert_level` 0.03, `expected_piped_litres` 0.0263, `max_temp_c` 0.0214, `heat_index_c` 0.0059, `heat_exposure` 0.0042, `last_received_litres` 0.004.
* **Per-area "top contributing features" (SHAP):** `Booster.predict(DMatrix, pred_contribs=True)` returns, per row, one contribution per feature in litres plus a bias column (last). `prediction_service.top_contributions` drops the bias, sorts by |contribution| (ties in feature order), keeps 3, rounds to 1 dp. Example, `ar_02` in `scn_sa01_w06`: population +2,300.3, mean_need_last3 +832.0, last_need +517.2 L.
* **Real prediction run (`scn_sa01_w06`):** predicted total shortfall 44,125 L vs true 44,403 L; `run_mae_litres` 1,303.8 L (severe 1,505.8, mild 1,325.6). Latency ≈ 25–30 ms inside the model call, ≈ 40 ms cold HTTP, ≈ 5 ms cached.
* **Leakage precautions:** `expected_piped` is built from the noise-free need only; the split is by service area and `_check_split` raises if `sa_01` or an overlapping area appears; all tuning (`--validate`) uses the carved-out validation areas, the test split is touched once for reporting; lag features use windows before *t* only; the CSV and the scenario-time feature builder share the same definitions (tested both ways).
* **Honest limitations:** synthetic and in-distribution (the model learns the generator's own process); the gain over the baseline is moderate; population and lag features dominate while weather features have small importance although weather drives true need (likely under-prediction at the most extreme heat); sa_01 has only 5 history windows, so its run MAE (≈ 1.3–1.5 kL) is higher than the test MAE (0.88 kL); individual areas can be off by a lot (`ar_04` predicted shortfall 6,197 vs true 10,021, `ar_05` 7,100 vs 4,784) even though the total is close, so area *ranking* can differ from truth.

---

## 5. The optimizer

### 5.1 Priority formula (F-2 … F-5, `domain/priority.py`)
`U = shortfall / predicted_need`; `D̂ = debt / max(1, max debt over the scenario's areas)`; `V = vulnerability_index` (0 if unavailable);
**`p = round(1 + α·U + β·D̂ + γ·V, 4)`** with weights α = urgency 1.0, β = debt 1.0, γ = vulnerability 0.5, δ = travel 20.0 (defaults), rounded once from unrounded parts.

*Worked example* (contract example, debt {ar_01 0, ar_02 8,000, ar_03 12,000}, max debt 12,000): `ar_03`: U = 6000/21000 = 0.2857, D̂ = 1.0, V = 0.8 → p = 1 + 0.2857 + 1.0 + 0.5·0.8 = **2.6857**. `ar_02`: 1 + 0.2727 + 0.6667 + 0.275 = **2.2144**. `ar_01`: 1 + 0.2593 + 0 + 0.15 = **1.4093**.

### 5.2 CP-SAT model (`optimization/allocation.py`)
Units of 100 L, available tankers only. For tanker *k* and area *i*: sᵢ = ⌊shortfallᵢ/100⌋, Cₖ = ⌊capacityₖ/100⌋, S = ⌊supply/100⌋.

* **Variables:** `y[k,i]` ∈ [0, min(Cₖ, sᵢ)] (units delivered), `z[k,i]` ∈ {0,1} (tanker visits area; `y ≤ ub·z`, `y ≥ z`). Pairs that are unreachable (`2·d(depot,i) > max_route_km`) get no variables.
* **Constraints:** `Σₖ y[k,i] ≤ sᵢ` (never deliver above shortfall) · `Σᵢ y[k,i] ≤ Cₖ` (capacity) · `Σ y ≤ S` (water) · **star route bound** `Σᵢ z[k,i]·2·d(depot,i) ≤ max_route_kmₖ`.
* **Unmet:** `uᵢ = sᵢ − Σₖ y[k,i]`, split `uᵢ = u1ᵢ + u2ᵢ` where `u1ᵢ ≤ cᵢ = ⌈(1−φ)·shortfallᵢ/100⌉` and `u2ᵢ` is the part **below the coverage floor φ = 0.25**.
* **Objective:** minimise `Σᵢ Pᵢ·(u1ᵢ + κ·u2ᵢ) + Σ_{k,i} Tᵢ·z[k,i]`, with `Pᵢ = round(pᵢ·1000·100)`, `κ = 3.0`, `Tᵢ = round(δ·d(depot,i)·1000)`.

In words: leave as little *priority-weighted* need unserved as possible; going below 25% coverage for any area costs 3× more; and every extra tanker visit costs a bit of travel.
* **Why the coverage-floor term:** the PRD's plain `Σ p·u + δ·travel` can write off a low-priority area entirely. The soft floor realises the PRD's "minimum neighbourhood coverage" goal without a hard constraint that could make the problem infeasible. Result: JalPriority's minimum coverage is 0.21–0.23 on every seed, while the baselines leave areas at 0.
* **Solver settings:** 8 workers, seed 42, stage-1 time = `max(0.1, time_limit_s − 0.5 − upstream_elapsed)`, warm-started with a greedy hint. **Determinism:** once stage 1 proves OPTIMAL, a second 1-worker stage fixes the objective at the optimum and minimises a tie-break (give higher-ranked areas less unmet, then fewer stops), so identical inputs give identical plans (tested by repeated solves).
* **Status mapping:** OPTIMAL → `optimal`; FEASIBLE → `time_limited` if the wall time reached 95% of the limit, else `feasible`; greedy → `heuristic`. A time limit is **never** an error.

### 5.3 Greedy fallback
`solve_greedy` serves areas in rank order in two passes (first up to the coverage floor, then to full shortfall), preferring tankers already visiting the area, respecting capacity, remaining supply and star-route slack. It is used when CP-SAT returns nothing or raises (engine `greedy-fallback`, status `heuristic`, reason `TIME_LIMITED_SOLUTION` on every area), and also when CP-SAT is not optimal but greedy is better. Only if both fail does the API return `SOLVER_ERROR` (500).

### 5.4 Routing and stop order (`optimization/routing.py`)
Each tanker with deliveries starts and ends at the depot. ≤ 7 stops: exact TSP by permutation (stops pre-sorted by priority, first strictly shorter tour (1e-6 km) wins, so **ties go to the higher-priority area first**). > 7 stops: OR-Tools RoutingModel (PATH_CHEAPEST_ARC + GUIDED_LOCAL_SEARCH, 0.3 s per tanker). `route_km` is summed from the distance matrix (F-8). If a sequenced route still exceeded the limit (only possible on non-metric matrices), the lowest-priority stop is dropped and the plan is reported as `feasible`.

### 5.5 Why plans never violate constraints
(1) The star bound is conservative: any tour over a visited set is never longer than the sum of out-and-back trips (triangle inequality), so routes are within `max_route_km` by construction. (2) Capacity, supply and "only available tankers" are model constraints. (3) `domain/constraints.build_constraint_check` **re-computes** F-15 from the final deliveries (loads, statuses, route km re-measured on the matrix) rather than trusting the solver. (4) 50 random scenarios + the three seeds + extremes are tested for 0 violations.

---

## 6. Explanations

Closed set of 16 reason codes (CONTRACT 5.10). A produces these (rules as implemented, on the 4-dp wire values):

| code | fires when |
|---|---|
| `HIGH_PREDICTED_SHORTFALL` | urgency U ≥ 0.3 |
| `HIGH_DEBT` | D̂ ≥ 0.5 and debt > 0 |
| `HIGH_VULNERABILITY` | V ≥ 0.6 |
| `HEAT_ALERT` | alert ≥ 2 and heat_exposure ≥ 0.7 **and none of the three codes above applied** (CI-1) |
| `LOWER_RELATIVE_PRIORITY` | priority in the bottom third (and below the top priority), or it is the unmet cause |
| `FULLY_SERVED` / `PARTIALLY_SERVED` / `NOT_SERVED` / `NO_SHORTFALL` | exactly one: shortfall 0 → NO_SHORTFALL; allocated 0 → NOT_SERVED; unmet 0 → FULLY_SERVED; else PARTIALLY_SERVED |
| `SUPPLY_EXHAUSTED` | unmet cause: all water allocated elsewhere |
| `TANKER_CAPACITY_LIMIT` | unmet cause: all available tanker capacity used |
| `ROUTE_LIMIT` | unmet cause: no tanker can add the area within its route bound |
| `TRAVEL_COST_TRADEOFF` | unmet cause: the travel cost outweighed the benefit |
| `TIME_LIMITED_SOLUTION` | on every area when the plan is `time_limited` or `heuristic` |
| `FCFS_ORDER`, `NEAREST_FIRST_ORDER` | produced by B for baselines, not by A |

Order inside `reasons[]`: drivers, `LOWER_RELATIVE_PRIORITY`, served status, unmet cause, `TIME_LIMITED_SOLUTION`. `unserved[].reason_code` is the unmet cause. Text comes from templates with formatted numbers (`12,000 L`, `36%`, `2.69`); no LLM.

Real examples from `scn_sa01_w06` (debt from history, default weights):
* **`ar_05`** (rank 3): reasons `[HIGH_PREDICTED_SHORTFALL, FULLY_SERVED]` — *"Served in full (7,100 L). Priority 2.09, driven by a high predicted shortfall of 7,100 L (43% of need)."*
* **`ar_02`** (rank 4): reasons `[HIGH_VULNERABILITY, PARTIALLY_SERVED, SUPPLY_EXHAUSTED]` — *"Partially served: 1,900 of 5,058 L (38%). 3,158 L remain unserved because all 24,400 L of tanker water went to higher-priority areas first; this area has high vulnerability (0.63) (priority 1.89)."*

(The three texts in the contract's own §5.6 example are reproduced character for character by the golden test.)

---

## 7. Disruptions (`domain/disruptions.py`, `services/scenario_service.py`)

`POST /a/v1/scenarios/{id}/disruptions {type, params}` creates a **derived scenario** (a full scenario with `parent_scenario_id` and `disruptions_applied`).

| type | params | effect | errors |
|---|---|---|---|
| `tanker_down` | `tanker_id` | that tanker becomes `unavailable` | 422 unknown tanker · 409 already unavailable |
| `supply_cut` | `fraction` 0.05–0.9 | `S' = floor(S·(1−f)/100)·100` | 422 out of range / not a number |
| `heat_alert` | `alert_level` 0–3 | `Δ = new − old`; `max_temp_c = round(old + 1.5Δ, 1)`, `heat_index_c = round(old + 2.5Δ, 1)` | 422 invalid · 409 Δ = 0 |

* **Derived id:** `{root}__d{k}_{h}` — `root` = seed id (text before the first `__`), `k` = number of disruptions applied so far, `h` = first 6 hex chars of `sha1(json.dumps([{"type","params"}…], sort_keys=True, separators=(",",":")))`. Timestamps are not hashed. Chaining works (`…__d2_…`), order matters. Real examples: `scn_sa01_w06__d1_9c1bb9` (tk_01 down), `…__d1_d50e0b` (supply cut 0.3: 24,400 → 17,000 L), `…__d1_b48980` (alert 2 → 3: 44.5 → 46.0 °C, 49.0 → 51.5 °C), chained `…__d2_4e0ba6`.
* **Idempotency:** the same disruption on the same parent returns the stored derived scenario with **200** (first time **201**). Derived scenarios are persisted and retrievable; their `run_mae_litres` is `null` (no truth).
* **CI-3 decimal fix:** floats give `20000·(1−0.9) = 1999.9999999999995 → 1900`; exact arithmetic gives 2,000. A computes in `Decimal` (`cut_supply`), so f = 0.9 → 2,000 and f = 0.3 → 14,000.
* **CI-5 id rule:** a derived id must contain exactly one `__d<k>_<hex6>` segment and a root not ending in `_`; anything else is "not found".

---

## 8. The API (`/a/v1`)

| route | purpose | input | output | errors |
|---|---|---|---|---|
| `GET /health` | liveness (no auth) | – | `{status, service, version, model_version, time}` | – |
| `GET /scenarios?include_derived=false` | scenario summaries sorted by id | query flag | `{scenarios:[ScenarioSummary]}` | 401, 403, 422 |
| `GET /scenarios/{id}` | full scenario (seed or derived) | path id | `Scenario` | 401, 403, 404 `SCENARIO_NOT_FOUND` |
| `POST /scenarios/{id}/disruptions` | apply a disruption | `{type, params}` | derived `Scenario` (201 new / 200 existing) | 401, 403, 404, 409 `DISRUPTION_NOT_APPLICABLE`, 422 `DISRUPTION_INVALID` / `VALIDATION_ERROR` |
| `GET /scenarios/{id}/predictions` | need, shortfall, SHAP top-3 per area | path id | `PredictionSet` | 401, 403, 404, 422, 503 `MODEL_UNAVAILABLE` |
| `POST /plans/solve` | the optimal plan | `{scenario_id, weights (all 4), debt_by_area, time_limit_s? 0.5–9.5}` | `PlanResult` | 401, 403, 404, 422 (bad weights / unknown debt area), 500 `SOLVER_ERROR`, 503 |
| `GET /model` | model card (XGBoost vs baseline, importances, disclaimer) | – | `ModelInfo` | 401, 403, 503 |

* **Auth:** `Authorization: Bearer <HS256 JWT>`; `iss = jalpriority-b`, `aud = jalpriority`, required claims `exp, iat, iss, aud, sub`, 30 s leeway; roles **`dispatcher`, `admin`, `service`** (anything else → 403; bad/missing/expired/wrong iss/aud → 401). `A_AUTH_DISABLED=true` skips checks (local tests only; default `false`; the SAM template pins `"false"`). Dev tokens: `scripts/mint_dev_token.py`.
* **Error envelope** (every non-2xx): `{"error": {"code", "message", "details", "request_id"}}`. Extras: unknown paths/methods answer `NOT_FOUND` / `METHOD_NOT_ALLOWED` (CI-2); unhandled exceptions → `INTERNAL_ERROR` 500 without internals.
* **Request ids:** `X-Request-ID` is reused if supplied (trimmed to 64 chars), otherwise `req_` + 12 hex; echoed on every response and in the error body.
* **Logging:** one JSON object per line on stdout (`ts, level, service:"jp-a", request_id, event, msg`, plus fields like `duration_ms`); tokens and secrets are never logged.

---

## 9. The screens (`dashboard/`)

Stack: React 18.3.1 + Vite 5.4.11 + TypeScript 5.6.3, Leaflet 1.9.4 / react-leaflet 4.2.1. Screens use CSS Modules and only the shared `--jp-*` colour tokens.

**Crisis snapshot (`SnapshotScreen`)** — scenario picker (from `GET /scenarios`); four KPI cards (tanker water, tankers available/total, heat-alert badge 0–3 with temperatures, total predicted shortfall with a "% coverable" bar); sortable area table (`AreaTable`: name, population, last delivery with relative time, requested, predicted need, expected piped, predicted shortfall, top-3 feature chips with ± litres; default sort shortfall descending); `ModelPanel` (XGBoost vs last-observed MAE/RMSE, an honest "x% lower/higher" line, feature-importance bars and the simulation disclaimer); a "Go to dispatch plan →" button.

**Dispatch plan (`DispatchPlanScreen`)** — empty state with **Generate plan** when `plan` is null (spinner while `generating`, `generateError` inline); `PlanHeader` (strategy, solver engine + status badge — `time_limited`/`heuristic` in warn colour, solve time, allocated/supply, **total unmet in red**, plan status pill, constraint status); B's `actionsSlot` rendered verbatim above the table; `PlanMap`; `PlanTable` (rank, area, priority, shortfall, allocated, coverage %, tanker·stop·litres, unmet in red, **"Why?"** expander with reason text + chips); `TankerPanel` (driver briefing: ordered stops with litres, load/capacity, route km/max); `UnservedPanel` (always visible, red, "All predicted shortfall covered" when empty).

**Map & fallback** — `RouteMap` uses only `CircleMarker`, `Polyline`, `Rectangle` (depot square) — no default marker images. Circle size ∝ √shortfall, colour by coverage (full/partial/none), one polyline per tanker with a distinct colour *and dash style*, permanent tooltips "tk_01 #1", a **red ring** on areas with unmet need. After **3 tile errors** `useMapFallback` switches to `SchematicMap` (SVG of the same nodes and edges on projected coordinates); a manual Map/Schematic toggle exists and choosing Map resets the counter.

**Plugging into B's shell** — B imports from `member-a/dashboard/src/screens/index.ts`, which exports exactly `SnapshotScreen`, `DispatchPlanScreen`, `A_SCREENS_CONTRACT_VERSION = "1.0.0"`. Props are the contract's `ScreenContext` (`aApiBase="/a/v1"`, `token`, `officerId`, `role`, `scenarioId`, `onScenarioChange`, `onTrack`, `onOpenTab`) plus `plan`, `generating`, `generateError`, `onGeneratePlan`, `actionsSlot` for the plan screen. **Events (`onTrack`)**: `scenario_loaded {n_areas, supply_litres, tankers_available}` once per scenario id per mount; `explanation_viewed {plan_id, area_id}` when a "Why?" row is opened. Screens never read env, storage, the router or any `/b/` URL (enforced by 44 lint-style tests); a 401 shows "Session expired, please log in again" inline.

**Harness (`src/harness`, port 5174, proxies `/a` → 8001)** — `HarnessShell` imitates B's shell: SYNTHETIC badge, two tabs, event list + console logging for `onTrack`, three disabled placeholder buttons "Approve / Edit weights / Reject (B)". `VITE_A_HARNESS_PLAN_SOURCE=solve` posts to A's own `/plans/solve` with default weights and debt computed in TypeScript (F-10, λ = 0.3) and wraps the result in a `PlanRecord` with metrics computed by F-9, F-11–F-15 (`metrics.ts`); `fixture` loads the contract PlanRecord. The harness is never imported by the screens.

---

## 10. Results

### 10.1 JalPriority vs FCFS vs nearest-first
Baselines per MEMBER_B B5 (target = requested litres floored to 100 L, first-fit tankers by id, route-limit check, stop at supply), scored with F-9…F-14 (λ = 0.3, debt from history) against the **predicted** shortfall, using JalPriority's `p` for every strategy.

| seed | strategy | priority-weighted unmet | total unmet L | min coverage | travel km |
|---|---|---|---|---|---|
| `scn_sa01_w06` | **JalPriority** | **28,721.6** | **18,725** | **0.2259** | 65.12 |
| | FCFS | 64,986.0 | 31,511 | 0.0000 | 38.74 |
| | nearest-first | 62,604.6 | 29,513 | 0.0000 | 36.40 |
| `scn_sa01_w06_severe` | **JalPriority** | **45,651.2** | **27,090** | **0.2120** | 64.38 |
| | FCFS | 71,266.3 | 35,071 | 0.0000 | 23.34 |
| | nearest-first | 75,626.5 | 37,845 | 0.0000 | 21.92 |
| `scn_sa01_w06_mild` | **JalPriority** | **10,996.3** | **7,750** | **0.2321** | 70.81 |
| | FCFS | 42,311.5 | 18,980 | 0.0000 | 55.37 |
| | nearest-first | 42,694.3 | 18,980 | 0.0000 | 52.43 |

The baselines "travel less" only because they serve fewer wards. On the main seed FCFS serves `ar_01` 7,800 L, `ar_04` 15,000 L, `ar_06` 1,600 L and starves `ar_08, ar_05, ar_02, ar_07, ar_03`; nearest-first serves `ar_04` 15,000, `ar_06` 8,600, `ar_08` 800 and starves `ar_01, ar_05, ar_02, ar_07, ar_03`. JalPriority covers `ar_07` 99.7%, `ar_03` 99.9%, `ar_05` 100%, `ar_02` 37.6%, and nobody below 22.6%.

### 10.2 The main plan (`scn_sa01_w06`, optimal, 0 violations, 257 ms in the reader run)
Supply 24,400 L allocated in full, unmet 18,725 L, travel 65.12 km. Routes: tk_01 `dp_01→ar_07→ar_03→dp_01` 19.21 km (load 10,600/10,600); tk_02 `→ar_08→ar_01→` 11.61 km; tk_03 `→ar_04→ar_05→ar_06→` 20.82 km; tk_04 `→ar_02→` 13.48 km. Unserved: ar_07 21 L and ar_03 4 L (sub-100 L remainders), ar_02 3,158, ar_08 2,813, ar_01 2,917, ar_04 4,797, ar_06 5,015 (supply exhausted).

### 10.3 Performance (`scripts/perf_report.md`, in-process, 16-CPU Windows host; all 39 solves `ortools-cp-sat/optimal`)

| measure | min | median | max | target |
|---|---|---|---|---|
| plan solve @ `time_limit_s` 8.0 (3 seeds + 10 random 10-area/4-tanker) | 0.107 s | 0.208 s | **0.772 s** | < 10 s |
| plan solve @ 3.0 | 0.105 s | 0.207 s | 0.859 s | < 4 s |
| prediction, cold (wall) | 40.5 ms | 98.0 ms | 111.3 ms | < 2 s |
| prediction, cached (wall) | 5.1 ms | 7.8 ms | 10.0 ms | – |
| replan chain (disruption + cold predict + solve @ 3.0), 27 samples | 0.127 s | 0.172 s | **0.502 s** | < 4 s |

Network, Lambda cold starts and B's own work are not included.

---

## 11. Testing

Python (`pytest tests --collect-only`: **1,183 tests**; last full run **1,180 passed, 3 skipped** — conditional skips): unit 661 · contract 320 · e2e 108 · property 94. Dashboard (vitest): **150 tests in 12 files**; `npm run typecheck` and `npm run build` pass.

* **Golden** (`test_contract_golden.py` 11, `test_opt_golden.py` 8): the contract's own §5.3 scenario + §5.4 predictions + §6 request must reproduce the §5.6 plan — allocations {ar_03 6,000, ar_02 9,000, ar_01 5,000}, totals, routes, reasons and the three `reason_text` strings — in every field except `result_id`, `generated_at`, solve time. Proves the optimizer and explanation templates match the frozen contract exactly.
* **Contract tests** (endpoints 129, formulas 53, disruptions 79, fixtures 46, `types.ts` 2): strict (`extra="forbid"`) parsing of the 9 contract fixtures and every live response; independent recomputation of F-1…F-8 and F-15 (no production formula code imported; a tampered plan must fail); all error codes/statuses; `types.ts` equals CONTRACT §9.4.
* **Property tests** (94): 50 seeded random scenarios (6–10 areas, 1–4 tankers, extreme weights, zero supply …) all with 0 violations, allocation ≤ shortfall, capacity/route/supply respected; time-limit behaviour at 0.5 s; default solve < 10 s and 3 s limit < 4 s; determinism; monotonicity in debt; replan chains < 4 s.
* **E2E** (108): real app + real data + real model: flows, the demo property with real predictions, the demo moment (baselines starve the far vulnerable wards ≤ 5% while JalPriority covers them ≥ 0.20 better), baselines-differ acceptance, model honesty.
* **Dashboard:** isolation lint (44), screen render tests (dispatch 22, snapshot 19), harness metrics/shell (14 + 12), map fallback (7 + 8), api client (7), contract types (3), 10-area first paint < 300 ms (2).
* **Data/ML/AWS units:** data 138, ml 131, optimizer 163, aws 148.

---

## 12. Infra and AWS

* **`backend/Dockerfile`** (context `member-a`): `python:3.12-slim` + `libgomp1` (XGBoost), installs `requirements.txt`, copies `backend/jp_a`, `data`, `models`; non-root user (uid 10001); `HEALTHCHECK` on `/a/v1/health`; `CMD sh -c "exec uvicorn jp_a.main:app --host 0.0.0.0 --port ${A_PORT:-8001}"`.
* **`scripts/simulate_image.py`** (Docker is not installed): reproduces the image — exact `COPY` layout filtered by `.dockerignore` in a temp dir, a fresh Python 3.12 venv with only `requirements.txt`, the Dockerfile's `ENV` and `CMD`, then health/scenarios/predictions/model/solve on all seeds + a derived scenario. Result: **"docker build not run, image simulated: PASS"** (36/36 checks; Lambda variant 15/15; a negative control with the model `COPY` removed correctly failed). It does **not** prove an actual `docker build`, the apt/dnf layers, the non-root uid or the image size.
* **`infra/template.yaml`** (SAM, stack `jalpriority-a`): versioned encrypted S3 bucket `jalpriority-a-${AccountId}` (TLS-only policy, `a/derived/` expiry), Lambda container (Mangum, 2,048 MB, 15 s, x86_64) with a least-privilege role, HTTP API with throttling, SageMaker execution role limited to `jp-a-*`. **cfn-lint clean** (also enforced by a test). The only `Resource: "*"` statements are `ecr:GetAuthorizationToken` and `cloudwatch:PutMetricData`, which do not support resource scoping. The JWT secret is a NoEcho parameter delivered as a Lambda environment variable.
* **Code:** `repositories/storage_s3.py` (`A_STORAGE_MODE=s3`), `S3ModelLoader`, `sagemaker_client.py` (`A_MODEL_MODE=sagemaker_endpoint`), `lambda_handler.py`, `infra/sagemaker/train_entry.py`. Every cloud mode falls back to local files on any failure; **local is the default**.
* **Deployed vs not:** **nothing is deployed** ("AWS: not deployed (no credentials)"). S3/SageMaker/Lambda paths are tested only with fakes; no `sam build/deploy`, no real `docker build`.

---

## 13. Decisions that deviate from, or fill gaps in, the spec

* **Environment:** Python 3.12 venv via `uv` (system default 3.14 is outside the pinned range); Node 24 instead of 20 (pins unchanged); Windows + Git Bash with LF enforced (`.gitattributes`, `core.autocrlf input`); Docker unavailable → image simulated; `opus` model used for the DATA/ML/OPT agents; automatic "wip" snapshot commits; the GitHub repo already existed as PUBLIC and was switched to PRIVATE before pushing.
* **Data:** ward populations 1,800–4,800 (the spec's example used 7–18k) so that supply, not tanker capacity, is the binding limit; `expected_piped` from the noise-free need (the suggested process would have leaked the target); AR(0.3) on the log deviation; vulnerability tied to distance rank, voice to vulnerability; training weather stratified by `sa_number % 4`; fixed sa_01 history temperatures; `DEMO_REQUEST_OVERRIDES` (§3.2); `data/labels.json` holds summary labels; `save_derived` keeps the first write (first `applied_at` wins).
* **Optimizer:** the two-stage determinism trick; route repair that drops the lowest-priority stop; the upstream elapsed time is subtracted from the solver budget; a plan needing repair is reported `feasible`; the coverage-floor soft term as specified; `HEAT_ALERT` rule narrowed (CI-1).
* **Dashboard:** extra dev-only dependencies (`@testing-library/dom`, `jest-dom`, `user-event`, `@types/node`); the screens never call `/plans/solve` (only the harness does).

### The 6 contract issues, simply (and what B must do)
1. **CI-1 `HEAT_ALERT`:** the spec rule would put `HEAT_ALERT` on ar_02/ar_03 of the contract example, but the example does not show it. A only adds it when no debt/vulnerability/shortfall reason already explains the area. *B:* treat `reasons[]` as display data; don't expect `HEAT_ALERT` whenever alert ≥ 2.
2. **CI-2 error codes:** unknown paths/methods answer `NOT_FOUND` / `METHOD_NOT_ALLOWED`, which are not in the closed §4.5 list. *B:* map unknown codes to `UPSTREAM_A_ERROR`.
3. **CI-3 supply-cut rounding:** floats turn 2,000 into 1,900 for f = 0.9. A uses exact decimals. *B:* use `math.floor(Decimal(S)*(1-Decimal(str(f)))/Decimal(100))*100` in the mock A.
4. **CI-4 derived labels:** the contract does not define them; A uses "`<seed label> · <disruption>`". *B:* nothing.
5. **CI-5 derived-id regex:** the contract regex accepts several `__`; A requires exactly one `__d<k>_<hex6>` segment. *B:* generate ids with the exact `json.dumps(..., sort_keys=True, separators=(",",":"))` + sha1[:6] snippet in `HANDOFF_FOR_B.md`; never chain suffixes.
6. **CI-6 weights:** `weights` is required but individual fields have defaults; A accepts partial objects. *B:* always send all four weights.

Also for B (from `HANDOFF_FOR_B.md`): A accepts roles `dispatcher|admin|service` and `A_AUTH_DISABLED` must be `false` at merge; mount alias `../../member-a/dashboard/src/screens/index.ts`; shared React pins `react 18.3.1`, `react-dom 18.3.1`, `@types/react 18.3.12`, `@types/react-dom 18.3.1` (`npm ls` shows a single copy); and B's mock `scn_sa01_w06.json` should also have request order ≠ distance order so Compare shows three different columns.

---

## 14. What remains, risks, and the demo

### Remains / risks
* **Not done on this machine:** a real `docker build`; any AWS deployment; integration with B (done later, with `integration/INTEGRATION.md`). Possible image-size win: `xgboost-cpu` instead of the CUDA-pulling wheel (~190 MB), untested.
* **Merge risks:** B's mock must replicate the disruption rules exactly (decimal supply cut, derived-id hash); React must be a single copy in the combined SPA; both sides must read the same `SHARED_JWT_*`.
* **Model risk:** accuracy numbers are in-distribution on synthetic data; per-area predictions can be far off (ar_04, ar_05), so a judge who probes individual wards will see errors — the plan is robust because it optimises the predicted shortfall, not because the prediction is perfect.
* **Perf watch item:** the 10-area first-paint test with map takes ≈ 1.1 s against a 1 s budget on a slow machine (the 300 ms table budget is met).
* **Doc debt fixed in this commit:** `data/DATA_GENERATION.md` now mentions the request overrides; `PROGRESS.md` header refreshed.

### 60-second judge pitch (A's part)
"When water is scarce, 'first come, first served' and 'closest first' both fail the same people: the far, vulnerable neighbourhoods that ask quietly. Our engine does three things. One: an XGBoost model predicts each ward's need for the next 12 hours from heat, population and recent history — about 24% more accurate than just repeating last window, on held-out synthetic districts. Two: an optimizer — Google's CP-SAT — turns that into a plan: how many litres each ward gets from which tanker, in which stop order, maximising *priority-weighted* coverage where priority grows with predicted shortfall, accumulated deprivation debt and vulnerability, with a floor so nobody is written off, and it never violates tanker capacity, water supply or route limits. Three: every ward gets a plain-language reason — 'served in full because of 10,588 L of accumulated debt and high vulnerability' — and every unserved litre is shown in red, never hidden. On our main scenario the baselines leave the three far vulnerable wards at zero; we serve two of them essentially in full and the third at 38%, cutting priority-weighted unmet need from about 62,600–65,000 to 28,700 — in under a second."

### Five likely judge questions
1. **"Is the model accuracy real?"** — No: the data is synthetic and we say so on every screen and in `/model`. The MAE of 883.6 L vs 1,161.7 L baseline measures only our simulator; what we claim is the *method*, validated on held-out service areas with no leakage controls broken.
2. **"Why not just a greedy rule or a simple formula?"** — Greedy by priority ignores tanker capacity, route limits and the coverage floor jointly; CP-SAT optimises them together and proves optimality (status `optimal` on all 39 timed solves). We still ship a greedy fallback so a time limit never produces an error.
3. **"How do you know the plan never breaks constraints?"** — Constraints are in the model, the star-route bound guarantees routes, and a separate function recomputes the contract's violation count from the final deliveries; 50 random scenarios plus the seeds all give 0, and the contract's own golden example is reproduced exactly.
4. **"How is it fair, and who decides the trade-off?"** — Fairness enters through debt (past under-service raises priority), vulnerability and a coverage floor; the weights (urgency/debt/vulnerability/travel) are policy knobs owned by B's screen, and the officer approves or edits every plan.
5. **"What if the heat gets worse or a tanker breaks?"** — Three disruption types create derived scenarios (tanker down, supply cut, heat alert); predict + solve on a derived scenario took 0.13–0.50 s in our measurements (target < 4 s), and results are idempotent and reproducible.

---

## 15. Glossary

* **Deprivation debt (D):** litres of need an area went unserved in past windows, decayed by λ = 0.3 per window: `D' = round(0.7·D + unmet)`. High debt ⇒ higher priority.
* **Shortfall:** `max(0, predicted need − expected piped supply)` — the water tankers must supply.
* **Unmet:** `max(0, shortfall − allocated)`.
* **Coverage:** `allocated / shortfall` (capped at 1; 1 if no shortfall); **minimum coverage** is the worst area's coverage.
* **Urgency (U):** `shortfall / predicted need`.
* **Priority weight (p):** `1 + α·U + β·D̂ + γ·V`; the objective multiplies each area's unmet litres by it.
* **Vulnerability index (V):** 0–1 neighbourhood score (0 when unavailable).
* **Priority-weighted unmet (PWU, F-9):** `Σ pᵢ·uᵢ` — the headline fairness metric (lower is better).
* **CP-SAT:** Google OR-Tools' constraint-programming SAT solver; finds provably optimal integer solutions.
* **Star-route bound:** each visited area costs an out-and-back trip `2·d(depot, i)`; their sum bounds any real tour, so keeping it ≤ the route limit guarantees feasibility.
* **Coverage floor (φ = 0.25) and κ = 3:** unmet below 25% coverage is penalised 3× more.
* **SHAP:** per-prediction attribution of a model output to its features; here XGBoost `pred_contribs` in litres.
* **MAE / RMSE:** mean absolute / root-mean-square error in litres.
* **Last-observed baseline:** predict this window's need = last window's need.
* **FCFS:** first come, first served (by request time). **Nearest-first:** by depot distance.
* **Derived scenario:** a seed scenario after a disruption; id `…__d<k>_<hash>`.
* **Contract / golden test:** the frozen API definition and the test that the engine reproduces its worked example exactly.
* **Harness:** A's stand-in for B's app shell, used to run the screens alone.
* **Seed scenario:** one of the three generated demo cases. **Hidden truth:** the true needs the model never sees, used only to score it.
