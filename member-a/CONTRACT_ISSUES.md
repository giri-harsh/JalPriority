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
- **Concrete case:** `S = 20000, f = 0.9`: IEEE-754 gives `20000 * (1 - 0.9) = 1999.9999999999995` -> `floor(19.999…) * 100 = 1900`, exact arithmetic gives `2000`. (`f = 0.3`, the value used by INTEGRATION E14, is unaffected: 14000 either way.)
- **Workaround:** A evaluates the formula in exact decimal arithmetic (`Decimal(S) * (1 - Decimal(repr(f))) / 100`, then floor) - the faithful reading of the formula. `tests/unit/test_disruptions.py` pins the known values. If B's mock uses plain floats the two sides can differ by 100 L for fragile fractions (e.g. 0.9); at merge, run B's mock against A for the 4 fractions in `test_supply_cut_known_values`.
- **Proposed fix:** state in §5.12 that `S'` is computed in decimal arithmetic (or that f is a multiple of 0.01) for both sides.

## CI-4 · §5.3 — `ScenarioSummary.label` for derived scenarios
- **Problem:** the contract defines `label` only for the example seed.
- **Workaround:** derived scenarios get `"<parent label> · <disruption description>"` (see `scenario_repo.py`).
- **Proposed fix:** none needed; informational.

## CI-5 · §4.2 — derived-id regex accepts multiple `__`
- **Problem:** `^scn_[a-z0-9_]+__d\d+_[0-9a-f]{6}$` also matches `scn_a__b__d1_abcdef`.
- **Workaround (implemented, tested in `tests/unit/test_ci5_scenario_ids.py`):** `is_valid_scenario_id` requires exactly one `__` (the `__d<k>_<hex6>` segment) and a root not ending in `_`; other ids are reported as not found.
- **Proposed fix:** tighten the regex to `^scn_[a-z0-9]+(?:_[a-z0-9]+)*__d\d+_[0-9a-f]{6}$`.

## CI-6 · §5.2 — `weights` "required" but with per-field defaults
- **Problem:** the table gives defaults for every weight field while §6 says `weights` is required.
- **Workaround:** the object is required; missing individual fields take the §5.2 defaults.
- **Proposed fix:** clarify that partial weights objects are allowed (or forbidden).
