# CLAUDE.md — Member A (JalPriority AI, "Need → Plan" slice)

You are building Member A's half of JalPriority AI. Your working directory is `member-a/`.
Sources of truth, in order: `../contracts/CONTRACT.md` (frozen), then `./MEMBER_A.md`.

## Authority
- You have full decision authority. Run everything end to end autonomously: scaffold, install, generate data, train, implement, test, fix, commit.
- Make every routine engineering decision yourself (library usage, file structure inside your folder, algorithms, UI layout details). Do not ask for confirmation.
- Ask the user ONLY when genuinely blocked: missing credentials for an AWS step, a hard platform failure you cannot work around, or a contradiction that makes the contract impossible to satisfy (log it first, see below).

## Boundaries (non-negotiable)
- Create, edit and delete files ONLY under `member-a/`. Never touch `../contracts/`, `../member-b/`, `../integration/` or any repo-root file.
- Never modify `../contracts/CONTRACT.md`. If it seems wrong, ambiguous or impossible, append an entry to `member-a/CONTRACT_ISSUES.md` (section number, problem, workaround in use, proposed fix) and keep building against the contract as written.
- Do not read `../member-b/` for hints. Build only from the contract.
- Only use env vars prefixed `A_` or `VITE_A_`, plus the listed `SHARED_*` read-only vars. Only serve routes under `/a/v1`. Never call `/b/*` from anything.
- Before every commit, run `scripts/check_ownership.sh` (fails if `git diff --name-only t0...HEAD` contains any path outside `member-a/`).

## How to work
- Follow the milestone order in MEMBER_A.md §4. After each milestone: run the tests, fix until green, then commit with message `A-M<n>: <summary>`.
- Contract tests are mandatory and must stay green. Strict models in tests (`extra="forbid"`), tolerant at runtime.
- Exact field names, ID formats, error codes and rounding come from the contract. Never invent or rename fields.
- Prefer the local fallback for every AWS service. AWS work (M12) is last and must never break the local default.
- Keep the solver within its time limit; always return a feasible plan with 0 violations.
- Python 3.11+, a venv at `member-a/.venv`, `pip install -e backend -r backend/requirements-dev.txt`. Node 20; `npm ci` in `member-a/dashboard`.
- When finished, verify the Definition of Done checklist in MEMBER_A.md §7 item by item and report the result.
