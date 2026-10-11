# HANDOFF_FOR_B.md — what Member B needs from Member A

Branch from tag `t0` (main == t0). A lives only in `member-a/**` on `a/main`. Contract: `contracts/CONTRACT.md` v1.0.0.

## Contract issues (A's `CONTRACT_ISSUES.md`)
| # | One line | What B must do |
|---|---|---|
| CI-1 | `HEAT_ALERT` reason is added only when no debt/vulnerability/shortfall reason already explains the area (matches the §5.6 example). | Treat `reasons[]` as display data; do not assume `HEAT_ALERT` appears whenever alert ≥ 2. B's baseline reasons are B's own. |
| CI-2 | Unknown path / wrong method return the envelope with codes `NOT_FOUND` / `METHOD_NOT_ALLOWED` (not in §4.5). | Nothing; B never calls such paths. Map unknown codes to `UPSTREAM_A_ERROR`. |
| CI-3 | `supply_cut` is computed in exact decimal arithmetic (floats give 1900 instead of 2000 for S=20000, f=0.9). | **Use the Decimal snippet below in B's mock A.** |
| CI-4 | Derived scenario `label` = `"<seed label> · <disruption>"…` (informational). | Nothing. |
| CI-5 | A validates derived ids strictly: exactly one `__d<k>_<hex6>` segment (the contract regex alone is too loose). | B's mock must generate ids with the snippet below; never chain suffixes. |
| CI-6 | `weights` is required, but individual missing fields take §5.2 defaults. | Always send all four weights (B does anyway). |

### CI-3 — decimal-safe `supply_cut` (use this in B's mock)
```python
from decimal import Decimal
import math

def cut_supply(S: int, f: float) -> int:
    return math.floor(Decimal(S) * (Decimal(1) - Decimal(str(f))) / Decimal(100)) * 100
# cut_supply(20000, 0.9) == 2000 ; cut_supply(20000, 0.3) == 14000
```
`heat_alert`: `Δ = new − old`; `max_temp_c = round(old + 1.5·Δ, 1)`; `heat_index_c = round(old + 2.5·Δ, 1)`.

### Derived-id hashing (exact `json.dumps` args)
```python
import hashlib, json

def derived_id(root: str, applied: list[dict]) -> str:
    # applied = ALL disruptions of the derived scenario in order, each {"type": str, "params": dict}
    payload = json.dumps([{"type": a["type"], "params": a["params"]} for a in applied],
                         sort_keys=True, separators=(",", ":"))
    return f"{root}__d{len(applied)}_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:6]}"
```
`root` is the seed id (text before the first `__`). `params` are normalised: `{"tanker_id": "tk_01"}`, `{"fraction": 0.3}` (float), `{"alert_level": 3}` (int). Extra param keys are dropped by A.

## Auth
* A accepts roles **`dispatcher`, `admin`, `service`**; any other role → 403. Wrong/missing token, `iss`, `aud`, expiry (30 s leeway) → 401.
* `A_AUTH_DISABLED` defaults to `false` and **must stay `false` at merge** (it exists only for A's local tests).
* A reads `SHARED_JWT_SECRET`, `SHARED_JWT_ISSUER` (`jalpriority-b`), `SHARED_JWT_AUDIENCE` (`jalpriority`) from the environment / `/integration/.env`.

## Frontend mount
* Alias target (relative to `member-b/dashboard`): `../../member-a/dashboard/src/screens/index.ts`
  (`VITE_B_A_SCREENS_PATH`). Exports exactly `SnapshotScreen`, `DispatchPlanScreen`, `A_SCREENS_CONTRACT_VERSION = "1.0.0"`.
* Vite: `resolve.dedupe = ["react","react-dom","react/jsx-runtime"]`, `server.fs.allow = [".", "../../member-a/dashboard"]`.
* `npm ci` works in `member-a/dashboard` from a clean checkout; `leaflet`/`react-leaflet` resolve from its `node_modules`.
* Shared pins that **must match B's lockfile**: `react@18.3.1`, `react-dom@18.3.1`, `@types/react@18.3.12`, `@types/react-dom@18.3.1`.

`npm ls react react-dom` in `member-a/dashboard`:
```
@jp/a-dashboard@1.0.0
+-- @testing-library/react@16.1.0 -> react@18.3.1, react-dom@18.3.1 (deduped)
+-- react-dom@18.3.1
+-- react-leaflet@4.2.1 -> react@18.3.1, react-dom@18.3.1 (deduped)
`-- react@18.3.1
```
(`@types/react@18.3.12`, `@types/react-dom@18.3.1`.) Only one copy of each is installed.

## Other facts B may rely on
* A emits only `scenario_loaded` and `explanation_viewed` via `onTrack`.
* A is stateless w.r.t. debt: B passes `debt_by_area` (debt after window `window_index − 1`).
* `time_limit_s` default 8.0, range [0.5, 9.5]; A responds within `time_limit_s + 1.0` s and never errors on a time limit.
* Disruption on an existing derived id → 200 (idempotent), new → 201; errors 422 `DISRUPTION_INVALID`, 409 `DISRUPTION_NOT_APPLICABLE`.

## Fixture advice for B's mock
B's mock fixture `scn_sa01_w06.json` should also have request-time order ≠ distance order (one loud early mid-distance area, one quiet late near area) so the Compare screen shows three different columns during B's build. A's seeds do this: `ar_01` (5.8 km) is loud (1.45× its shortfall) and requests earliest; `ar_08` (4.57 km, 3rd nearest) is quiet (0.7×) and requests at 05:50; the far vulnerable wards request last.
