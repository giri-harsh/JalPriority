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
6. Tanker panel = driver briefing; unserved panel (red) lists every shortfall that remains (18,725 L in total — never hidden).

## Headline numbers (debt from history, default weights, λ = 0.3)
| area | priority | shortfall L | allocated L | coverage |
|---|---|---|---|---|
| ar_07 (far, V 0.78) | 2.74 | 6,821 | 6,800 | 99.7% |
| ar_03 (far, V 0.89) | 2.37 | 3,804 | 3,800 | 99.9% |
| ar_05 | 2.09 | 7,100 | 7,100 | 100% |
| ar_02 (far, V 0.63) | 1.89 | 5,058 | 1,900 | 37.6% |
| ar_04 (loud, near) | 1.40 | 6,197 | 1,400 | 22.6% |

Compared with simple strategies (same supply, serving *requested* litres; FCFS and nearest-first coincide on this seed because request order = distance order):

| strategy | priority-weighted unmet (F-9) | total unmet L | min coverage |
|---|---|---|---|
| **JalPriority** | **28,721.6** | **18,725** | **0.226** |
| FCFS / nearest-first | 62,604.6 | 29,513 | 0.000 |

FCFS and nearest-first give the far, vulnerable, late areas (`ar_03`, `ar_07`, `ar_02`) **0%** because the loud near areas drain the water first; JalPriority serves `ar_07` and `ar_03` essentially in full and `ar_02` to 38%. On `scn_sa01_w06_severe` the same holds (F-9 45,651 vs 75,627; min coverage 0.212 vs 0). The mild seed also favours JalPriority (10,996 vs 40,923). Zero constraint violations everywhere.

## What to say about the model
XGBoost predicts each area's need for the next 12-hour window; shortfall = need − expected piped supply. On the held-out service areas (never trained on) the **simulation-only** test error is **MAE 883.6 L / RMSE 1,166.2 L** versus **1,161.7 / 1,523.4** for the "last observed" baseline (≈ 24% lower MAE). The data are synthetic, so this is not real-world accuracy; the "SYNTHETIC DATA · SIMULATION RESULTS" badge is always shown.

## Disruption demo (API)
`POST /a/v1/scenarios/scn_sa01_w06/disruptions` with `{"type":"tanker_down","params":{"tanker_id":"tk_01"}}` → derived scenario `scn_sa01_w06__d1_xxxxxx` (201; repeat = 200). Predict + solve on it takes ≈ 0.2 s here (worst case 0.5 s measured).
