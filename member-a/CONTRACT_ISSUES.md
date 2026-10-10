# CONTRACT_ISSUES.md — Member A

Format: section · problem · workaround in use · proposed fix. `contracts/CONTRACT.md` is never edited by A.

## CI-1 · §5.6 example vs MEMBER_A F6 — `HEAT_ALERT` rule
- **Problem:** MEMBER_A F6 says add `HEAT_ALERT` when `alert_level >= 2 and heat_exposure >= 0.7`. In the §5.6 binding example the scenario has alert level 2 and `ar_03` (heat_exposure 0.90) and `ar_02` (0.70) would both qualify, yet the example `reasons[]` for those areas contain no `HEAT_ALERT`. M7 requires the response to equal the example in every field.
- **Workaround:** `HEAT_ALERT` is added only when `alert_level >= 2 and heat_exposure >= 0.7` **and none of** `HIGH_DEBT`, `HIGH_VULNERABILITY`, `HIGH_PREDICTED_SHORTFALL` already explains the area (heat is the fallback driver). Reproduces §5.6 exactly.
- **Proposed fix:** state the rule in §5.10 ("HEAT_ALERT: only as the primary driver"), or add `HEAT_ALERT` to the §5.6 example reasons for ar_02/ar_03.

## CI-2 · §4.5 — error codes for non-contract HTTP errors
- **Problem:** the §4.5 table is a closed list, but FastAPI also produces 404 (unknown path) and 405 (wrong method).
- **Workaround:** A answers with the standard envelope and codes `NOT_FOUND` / `METHOD_NOT_ALLOWED` (no contract route can trigger them).
- **Proposed fix:** add `NOT_FOUND` (404) and `METHOD_NOT_ALLOWED` (405) to §4.5, or document that unknown paths are out of scope.

## CI-3 · §5.12 — float evaluation of `S·(1 − f)/100`
- **Problem:** `floor(S·(1 − f)/100)·100` is evaluated in IEEE-754; for some `(S, f)` pairs the product lands a hair below an integer and floors one step lower than exact arithmetic would (e.g. an exact 14000 → 13900).
- **Workaround:** A uses the literal expression `math.floor(S * (1 - f) / 100) * 100` so that it matches B's mock if B implements the formula verbatim; `tests/unit/test_disruptions.py` documents known values.
- **Proposed fix:** specify decimal arithmetic (or "round half-up to 1e-9 before floor") in §5.12 for both sides.

## CI-4 · §5.3 — `ScenarioSummary.label` for derived scenarios
- **Problem:** the contract defines `label` only for the example seed.
- **Workaround:** derived scenarios get `"<parent label> · <disruption description>"` (see `scenario_repo.py`).
- **Proposed fix:** none needed; informational.
