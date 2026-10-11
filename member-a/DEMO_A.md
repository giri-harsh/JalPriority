# DEMO_A.md — Member A demo (SYNTHETIC DATA · SIMULATION RESULTS)

## Start from a clean checkout
```bash
cd member-a
uv venv -p 3.12 .venv
uv pip install -p .venv/Scripts/python.exe -r backend/requirements.txt -r backend/requirements-dev.txt
uv pip install -p .venv/Scripts/python.exe -e backend --no-deps      # Linux/macOS: .venv/bin/python
cp .env.example .env
bash scripts/run_dev.sh                                              # API  -> http://localhost:8001/a/v1/health
# second terminal
cd dashboard && npm ci
VITE_A_DEV_TOKEN=$(../.venv/Scripts/python.exe ../scripts/mint_dev_token.py) npm run dev   # harness -> http://localhost:5174
```
Data and model are committed; nothing needs generating or training.

## Click path (scenario `scn_sa01_w06`)
1. **Crisis snapshot** → pick `scn_sa01_w06` (header shows the SYNTHETIC badge; a `scenario_loaded` event appears in the event list).
2. Read the KPIs: supply **24,400 L** vs total predicted shortfall **43,125 L** (supply covers ≈ 57%; shortfall ≈ 1.77× supply), heat alert **2**, 4/4 tankers.
3. Area table (sorted by shortfall): note the **loud near areas** `ar_04` (requested 15,000 L vs shortfall 6,197 L) and `ar_06` (8,600 vs 6,615), and the **quiet far vulnerable areas** `ar_03` (requested 1,600 vs 3,804), `ar_07` (4,100 vs 6,821), `ar_02` (2,800 vs 5,058).
4. **Go to dispatch plan →** → **Generate plan** (< 1 s, CP-SAT optimal).
5. Map: tanker routes, red rings on areas with unmet need. Ranked table: rank 1 `ar_07` (priority 2.74), rank 2 `ar_03` (2.37). Click **Why?** on `ar_02` and `ar_04` (fires `explanation_viewed`).
6. Tanker panel = driver briefing; unserved panel (red) lists every shortfall that remains (18,900 L in total — never hidden).

## Headline numbers (debt from history, default weights, λ = 0.3)
| area | priority | shortfall L | allocated L | coverage |
|---|---|---|---|---|
| ar_07 (far, V 0.78) | 2.74 | 6,821 | 6,900 | 100% |
| ar_03 (far, V 0.89) | 2.37 | 3,804 | 3,900 | 100% |
| ar_05 | 2.09 | 7,100 | 7,100 | 100% |
| ar_02 (far, V 0.63) | 1.89 | 5,058 | 1,300 | 25.7% |
| ar_04 (loud, near) | 1.40 | 6,197 | 1,500 | 24.2% |

Compared with the two simple strategies (same supply 24,400 L, serving *requested* litres first-fit by tanker, route-limit checked; the seeds now have request order ≠ distance order, so the baselines differ):

| strategy | priority-weighted unmet (F-9) | total unmet L | min coverage | travel km | who it starves |
|---|---|---|---|---|---|
| **JalPriority** | **29,192.7** | **18,900** | **0.242** | 73.34 | nobody below 24% |
| FCFS | 64,986.0 | 31,511 | 0.000 | 38.74 | serves ar_01 (loud, mid-distance, called first) 7,800 L, ar_04 15,000 L, ar_06 1,600 L; starves ar_08, ar_05, ar_02, ar_07, ar_03 |
| nearest-first | 62,604.6 | 29,513 | 0.000 | 36.40 | serves ar_04 15,000 L, ar_06 8,600 L, ar_08 800 L; starves ar_01, ar_05, ar_02, ar_07, ar_03 |

**Talking point:** FCFS rewards whoever calls first; nearest-first rewards whoever is closest; both abandon the far vulnerable wards (ar_02, ar_07, ar_03 get under 10% under both, vs 26% / 100% / 100% under JalPriority). The baselines travel less only because they serve fewer wards. On `scn_sa01_w06_severe`: JalPriority F-9 46,229.4 vs FCFS 71,266.3 vs nearest 75,626.5 (km 69.51 / 23.34 / 21.92). On `scn_sa01_w06_mild`: 11,146.7 vs 42,311.5 vs 42,694.3 (km 83.45 / 55.37 / 52.43). Zero constraint violations everywhere.

## What to say about the model
XGBoost predicts each area's need for the next 12-hour window; shortfall = need − expected piped supply. On the held-out service areas (never trained on) the **simulation-only** test error is **MAE 883.6 L / RMSE 1,166.2 L** versus **1,161.7 / 1,523.4** for the "last observed" baseline (≈ 24% lower MAE). The data are synthetic, so this is not real-world accuracy; the "SYNTHETIC DATA · SIMULATION RESULTS" badge is always shown.

## Disruption demo (API)
`POST /a/v1/scenarios/scn_sa01_w06/disruptions` with `{"type":"tanker_down","params":{"tanker_id":"tk_01"}}` → derived scenario `scn_sa01_w06__d1_xxxxxx` (201; repeat = 200). Predict + solve on it takes ≈ 0.2 s here (worst case 0.5 s measured).
