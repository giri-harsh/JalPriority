# Synthetic data — how `member-a/data` is generated

**Everything in this folder is synthetic.** No real wards, people, weather records or tanker logs are used.
Scenarios carry `"is_synthetic": true` (CONTRACT §4.10), and any model metrics trained on this data are
simulation results, not real-world accuracy.

## Regenerate

```bash
scripts/generate_data.sh            # from member-a/, Git Bash on Windows or Linux/macOS; seed = A_SEED (default 42)
scripts/generate_data.sh --seed 7   # explicit seed
# equivalent:
.venv/Scripts/python.exe -m jp_a.data.cli --out data --seed 42     # Windows venv
.venv/bin/python -m jp_a.data.cli --out data --seed 42             # Linux/macOS venv
```

The generator is deterministic. The same seed always gives **byte-identical** files: there is no wall clock,
no salted `hash()` and no global RNG. Each random stream comes from
`numpy.random.PCG64(SeedSequence([seed, service_area_number, stream, (area_number)]))`. Floats are rounded
before they are written, JSON is written with indent 2, UTF-8 and LF line endings, and CSV values use fixed
formats. `tests/unit/test_data_generator.py` checks that regenerating with seed 42 reproduces the committed
files exactly.

Code: `backend/jp_a/data/generator.py` (DGP and writers), `cli.py`, `geo.py` (haversine and road
distance) and `debt.py` (F-10 debt over a history).

## Files

| Path | Content |
|---|---|
| `scenarios/scn_sa01_w06.json` | Main demo seed: heat alert 2, tanker water ≈ 55 % of the total shortfall |
| `scenarios/scn_sa01_w06_severe.json` | Severe heat: alert 3, ≈ 35 % |
| `scenarios/scn_sa01_w06_mild.json` | Mild heat: alert 1, ≈ 85 % |
| `truth/<scenario_id>.json` | Hidden ground truth `{"scenario_id", "window_index": 6, "true_need_litres": {area_id: int}}`. It is used only for `run_mae_litres` and is never copied into a scenario |
| `labels.json` | `ScenarioSummary.label` per seed id, read by `ScenarioRepository` |
| `training/train.csv` | Service areas `sa_02`–`sa_33`, windows 2–30: 7,482 rows (258 areas) |
| `training/test.csv` | Service areas `sa_34`–`sa_41`, windows 2–30: 1,885 rows (65 areas) |
| `derived/` | Disrupted scenarios written at runtime by `POST /a/v1/scenarios/{id}/disruptions` (gitignored) |

Every seed file is a strict CONTRACT §5.3 `Scenario` with no extra keys. Each has 8 areas and 4 tankers
(`tk_01`–`tk_04`, 10,000–12,000 L in multiples of 100, all `available`, `max_route_km` 60.0). History covers
windows 1–5, the scenario plans window 6 (`window_start` `2026-05-21T06:00:00.000Z`, 12-hour windows), and
the depot is `dp_01` at (28.6692, 77.4538). The three seeds share the service area, geometry, fleet, history
and request times. They differ only in window-6 heat, which changes need, expected piped supply, shortfall,
requested litres and the tanker water available.

## Data-generating process (final parameters)

### Geometry
- Area positions are uniform over the annulus 0.8–5.8 km (straight line) around the depot, with at least
  0.9 km between areas. They are projected to lat/lon with 111.32 km per degree and rounded to 4 dp.
- Road distance = haversine (R = 6371 km) × **1.3**, rounded to 2 dp. `distance_km` is the full symmetric
  matrix over the depot and all areas, with a diagonal of 0.0.

### Static area attributes
Let `r` be an area's depot-distance rank, normalised to [0, 1] (0 = nearest).

| Attribute | Formula |
|---|---|
| `population` | U(1,800, 4,800), rounded to 100 |
| `vulnerability_index` | clip(0.10 + 0.75·r + N(0, 0.06), 0.05, 0.95), 2 dp (peri-urban fringe is more vulnerable) |
| `heat_exposure` | clip(0.35 + 0.50·V + N(0, 0.07), 0.10, 0.98), 2 dp |
| piped share (latent) | U(0.55, 0.85) |
| voice (latent) | clip(1.62 − 1.25·V + N(0, 0.07), 0.5, 1.5): negatively correlated with vulnerability |
| `vulnerability_available` | always `true` |

### Weather (per service area and window)
- `alert_level` comes from `max_temp_c`: < 38 → 0, [38, 44) → 1, [44, 47) → 2, ≥ 47 → 3.
- `heat_index_c` = `max_temp_c` + U(2, 6) (humidity), 1 dp.
- In `sa_01`, windows 1–5 are 40.6, 41.4, 41.9, 42.0 and 43.1 °C (all alert 1). Window 6 depends on the seed:

  | Seed | `max_temp_c` | `heat_index_c` | `alert_level` |
  |---|---|---|---|
  | main | 44.5 | 49.0 | 2 |
  | severe | 47.5 | 52.5 | 3 |
  | mild | 41.5 | 45.0 | 1 |

- Training areas use an AR(1) series: `T_w = mean + trend·(w−1) + x_w`, with `x_w = 0.6·x_{w−1} + N(0, 1.0)`
  (stationary start), trend U(−0.05, 0.15) per window, clipped to [30, 50] °C. The mean is drawn from a heat
  regime chosen by `sa_number % 4`: cool U(38, 40), warm U(40.5, 42.5), hot U(42.5, 44.5) or extreme
  U(44.5, 46.5). Both splits therefore contain every alert level (train 292/3,770/2,400/1,020 rows at alert
  0/1/2/3; test 210/950/631/94).

### Need, piped supply, history

```text
det_need[i,t] = population · 3.0 L · (1 + 0.04·max(0, max_temp_c[t] − 35)) · (1 + 0.15·heat_exposure·alert_level[t]/3)
eta[i,t]      = 0.3·eta[i,t−1] + N(0, 0.08)            AR(0.3) carry-over of the log-deviation; eta[i,1] stationary
need[i,t]     = round(det_need · exp(eta))             ← true need (regression target / hidden truth)
expected_piped[i,t] = round(det_need · piped_share · (1 − 0.03·alert_level[t]))
actual_piped[i,t]   = min(need, round(expected_piped · exp(N(0, 0.03))))
```

- **No target leakage.** `expected_piped_litres` depends only on the noise-free `det_need`, which is a
  function of the published features. It never depends on window t's realised noise `eta`. A test checks
  that its correlation with the log residual `log(need/det_need)` is ≈ 0, and that `expected_piped/det_need`
  stays constant per area apart from integer rounding.
- **Tanker history (nearest-first).** In each history window the tanker water is
  `round100(U(lo, hi) · Σ (need − actual_piped))`, with (lo, hi) = (0.45, 0.70) for `sa_01` and
  (0.35, 0.95) for training areas. It is delivered nearest-first, in 100 L units, up to each area's observed
  shortfall. `received = actual_piped + tanker ≤ need`. Distant areas are starved, so they build up
  deprivation debt.
- `last_delivery_litres` and `last_delivery_at` come from the latest history window with a tanker drop. The
  drop time is window start + 60 min + 45 min × service position + U(0, 25) min. When an area never received
  a drop, these are 0 and `null`.

### Window-6 requests (seeds only)
- `requested_litres` = round100(true_shortfall · voice), where `true_shortfall = max(0, true_need − expected_piped)`.
- `request_time` = window_start − lead, where lead (minutes) = −55 + 225·(0.6·loudness + 0.4·nearness) + N(0, 6),
  loudness = (voice − 0.5)/1.0 and nearness = 1 − r. Louder and nearer areas request earlier; the quietest
  and farthest request up to about 1 h after the window starts.
- Supply = round100(share · Σ true_shortfall), with share = 0.55 (main), 0.35 (severe) or 0.85 (mild).
  Supply always fits within the fleet's single-trip capacity (43,100 L), so tanker water is the binding resource.

### Deprivation debt (CONTRACT 5.11 F-10, λ = 0.3)
`unmet_w = max(0, need_w − received_w)`, then `D_w = round(0.7·D_{w−1} + unmet_w)`, starting from `D_0 = 0`.
`jp_a.data.debt.debt_after_history(history)` / `debt_by_area(scenario)`. The §5.3 example history gives
{ar_01: 0, ar_02: 8000, ar_03: 12000}.

## Training CSV schema (cross-agent contract — do not rename or reorder)

```text
service_area_id,area_id,window_index,max_temp_c,heat_index_c,alert_level,heat_exposure,population,last_need_litres,mean_need_last3_litres,last_received_litres,expected_piped_litres,need_litres
```

There is one row per (service area, area, window t ≥ 2). Litres and population are integers, temperatures and
`mean_need_last3_litres` have 1 dp, and `heat_exposure` has 2 dp.

| Column | Definition | Scenario equivalent at prediction time |
|---|---|---|
| `max_temp_c`, `heat_index_c`, `alert_level` | weather of window t | `scenario.heat.*` |
| `heat_exposure`, `population` | static per area | `area.*` |
| `last_need_litres` | need at t−1 | `history[-1].need_litres` |
| `mean_need_last3_litres` | mean need over windows max(1, t−3)..t−1, 1 dp | mean of the last ≤ 3 history needs |
| `last_received_litres` | piped + tanker received at t−1 | `history[-1].received_litres` |
| `expected_piped_litres` | piped supply expected for window t | `area.expected_piped_litres` |
| `need_litres` | true need at t (target) | hidden truth file |

`jp_a.data.generator.lag_features(needs, received)` implements the three lag definitions. Tests check that
the CSV rows match features computed from a history of windows 1..t−1, and that the seed histories come from
the same simulation.

## Demo property (MEMBER_A F1, asserted in `tests/unit/test_data_demo_property.py`)

On `scn_sa01_w06` (shortfall computed from the hidden truth):

| Area | Depot km | V | Request | Requested / true shortfall | Debt after w5 | Role |
|---|---|---|---|---|---|---|
| ar_03 | 6.80 (farthest) | 0.89 | 06:46 (latest) | 1,600 / 2,872 (0.56) | 10,588 | far, vulnerable, late, quiet |
| ar_07 | 6.76 | 0.78 | 06:20 | 4,100 / 7,302 (0.56) | 21,308 | far, vulnerable, late, quiet |
| ar_02 | 6.74 | 0.63 | 06:10 | 2,800 / 4,433 (0.63) | 6,356 | far, vulnerable, late, quiet |
| ar_04 | 3.26 (nearest) | 0.12 | 03:03 (earliest) | 15,000 / 10,021 (1.50) | 158 | loud, near, early: over-requests |
| ar_06 | 4.45 | 0.09 | 03:33 | 8,600 / 6,028 (1.43) | 212 | loud, near, early: over-requests |

The total true shortfall is 44,403 L against 24,400 L of supply (1.82×; the requirement is ≥ 1.6×). There is
margin against model error: at least two demo areas still qualify if every predicted need is off by ±12 %,
the two strongest request at most 56 % of their shortfall, and ar_04 still over-requests at +12 %.

Debt after window 5 per area (all three seeds share it): ar_01 814, ar_02 6,356, ar_03 10,588, ar_04 158,
ar_05 9,418, ar_06 212, ar_07 21,308, ar_08 184.

| Seed | Alert | Supply (L) | True total shortfall (L) | Supply / shortfall |
|---|---|---|---|---|
| `scn_sa01_w06` | 2 | 24,400 | 44,403 | 0.550 |
| `scn_sa01_w06_severe` | 3 | 18,300 | 52,402 | 0.349 |
| `scn_sa01_w06_mild` | 1 | 31,600 | 37,189 | 0.850 |
