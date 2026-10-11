# JalPriority Member A - performance report

Generated 2026-10-11 02:28:07Z by `scripts/perf_report.py` (3 repeats per measurement). Host: Windows-11-10.0.26200-SP0, 16 logical CPUs, Python 3.12.8. Real app in-process (`TestClient`), auth enabled with a minted dispatcher token, committed model and data (copied to a temp dir). All times are wall clock around the HTTP call.

## (a) Solve wall time, seconds (POST /plans/solve, default weights, debt from history)

| Group / limit | samples | min | median | max |
|---|---|---|---|---|
| 3 seeds @ time_limit_s=8.0 | 9 | 0.107 | 0.137 | 0.278 |
| 10 random 10-area/4-tanker @ time_limit_s=8.0 | 30 | 0.138 | 0.217 | 0.772 |
| all @ time_limit_s=8.0 | 39 | 0.107 | 0.208 | 0.772 |
| 3 seeds @ time_limit_s=3.0 | 9 | 0.108 | 0.126 | 0.142 |
| 10 random 10-area/4-tanker @ time_limit_s=3.0 | 30 | 0.105 | 0.219 | 0.859 |
| all @ time_limit_s=3.0 | 39 | 0.105 | 0.207 | 0.859 |

### Solve wall time per scenario (median of repeats, seconds; engine/status)

| scenario | areas | tankers | @8.0 s median | @8.0 s max | @3.0 s median | @3.0 s max | status @8.0 | status @3.0 |
|---|---|---|---|---|---|---|---|---|
| scn_sa01_w06 | 8 | 4 | 0.131 | 0.137 | 0.128 | 0.142 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_sa01_w06_severe | 8 | 4 | 0.153 | 0.154 | 0.133 | 0.139 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_sa01_w06_mild | 8 | 4 | 0.125 | 0.278 | 0.109 | 0.109 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_perf_r01 | 10 | 4 | 0.447 | 0.462 | 0.467 | 0.489 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_perf_r02 | 10 | 4 | 0.205 | 0.208 | 0.206 | 0.215 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_perf_r03 | 10 | 4 | 0.156 | 0.157 | 0.160 | 0.207 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_perf_r04 | 10 | 4 | 0.229 | 0.232 | 0.218 | 0.219 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_perf_r05 | 10 | 4 | 0.752 | 0.772 | 0.748 | 0.859 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_perf_r06 | 10 | 4 | 0.315 | 0.327 | 0.289 | 0.292 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_perf_r07 | 10 | 4 | 0.216 | 0.216 | 0.232 | 0.244 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_perf_r08 | 10 | 4 | 0.141 | 0.155 | 0.109 | 0.118 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_perf_r09 | 10 | 4 | 0.181 | 0.208 | 0.219 | 0.220 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |
| scn_perf_r10 | 10 | 4 | 0.405 | 0.452 | 0.419 | 0.426 | ortools-cp-sat/optimal | ortools-cp-sat/optimal |

## (a) Prediction latency, milliseconds (GET /scenarios/{id}/predictions)

| Measure | samples | min | median | max |
|---|---|---|---|---|
| cold, wall, 3 seeds | 3 | 40.5 | 110.6 | 111.2 |
| cold, API `latency_ms`, 3 seeds | 3 | 24.0 | 46.0 | 49.0 |
| cached, wall, 3 seeds | 3 | 5.3 | 7.1 | 10.0 |
| cold, wall, 10 random | 10 | 65.2 | 96.4 | 111.3 |
| cold, API `latency_ms`, 10 random | 10 | 23.0 | 41.0 | 50.0 |
| cached, wall, 10 random | 10 | 5.1 | 8.3 | 10.0 |
| cold, wall, all | 13 | 40.5 | 98.0 | 111.3 |
| cold, API `latency_ms`, all | 13 | 23.0 | 42.0 | 50.0 |
| cached, wall, all | 13 | 5.1 | 7.8 | 10.0 |

## (b) Replan chain: disruption + predict (cold) + solve @ time_limit_s=3.0, seconds

| seed | disruption | params | samples | min | median | max | disrupt (med) | predict (med) | solve (med) | status |
|---|---|---|---|---|---|---|---|---|---|---|
| scn_sa01_w06 | tanker_down | `{"tanker_id": "tk_01"}` | 3 | 0.178 | 0.182 | 0.188 | 0.014 | 0.033 | 0.135 | ortools-cp-sat/optimal |
| scn_sa01_w06 | supply_cut | `{"fraction": 0.3}` | 3 | 0.153 | 0.157 | 0.174 | 0.013 | 0.027 | 0.121 | ortools-cp-sat/optimal |
| scn_sa01_w06 | heat_alert | `{"alert_level": 3}` | 3 | 0.177 | 0.192 | 0.207 | 0.015 | 0.029 | 0.149 | ortools-cp-sat/optimal |
| scn_sa01_w06_severe | tanker_down | `{"tanker_id": "tk_01"}` | 3 | 0.163 | 0.172 | 0.180 | 0.016 | 0.028 | 0.123 | ortools-cp-sat/optimal |
| scn_sa01_w06_severe | supply_cut | `{"fraction": 0.3}` | 3 | 0.127 | 0.134 | 0.139 | 0.013 | 0.028 | 0.092 | ortools-cp-sat/optimal |
| scn_sa01_w06_severe | heat_alert | `{"alert_level": 2}` | 3 | 0.153 | 0.154 | 0.155 | 0.012 | 0.028 | 0.110 | ortools-cp-sat/optimal |
| scn_sa01_w06_mild | tanker_down | `{"tanker_id": "tk_01"}` | 3 | 0.163 | 0.172 | 0.502 | 0.014 | 0.027 | 0.133 | ortools-cp-sat/optimal |
| scn_sa01_w06_mild | supply_cut | `{"fraction": 0.3}` | 3 | 0.169 | 0.177 | 0.181 | 0.015 | 0.028 | 0.134 | ortools-cp-sat/optimal |
| scn_sa01_w06_mild | heat_alert | `{"alert_level": 2}` | 3 | 0.167 | 0.179 | 0.193 | 0.012 | 0.028 | 0.137 | ortools-cp-sat/optimal |

| chain aggregate | samples | min | median | max |
|---|---|---|---|---|
| tanker_down | 9 | 0.163 | 0.178 | 0.502 |
| supply_cut | 9 | 0.127 | 0.157 | 0.181 |
| heat_alert | 9 | 0.153 | 0.177 | 0.207 |
| all disruption types | 27 | 0.127 | 0.172 | 0.502 |

`heat_alert` is +1 level (2 -> 3) on the main and mild seeds; the severe seed is already at the maximum level 3, so it uses 3 -> 2.

## Targets

- PASS: plan wall time at time_limit_s=8.0, max 0.772 s (target < 10 s)
- PASS: replan chain at time_limit_s=3.0, max 0.502 s (target < 4 s)

