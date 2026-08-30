# GA tuning — 30 August 2026

This pass changes the Genetic Algorithm from `ga-v3` to `ga-v4`. It improves
construction and repair while preserving the existing candidate domains, shared
hard-rule validator, objective scorer, lock rules, and incumbent deadline. The
CP-SAT implementation and the equal-budget formal pilot grid are unchanged.

These are local, synthetic development results. They do not select a formal
profile, demonstrate superiority over CP-SAT, or establish performance on an
authorized USM term.

## Changes

- Greedy initialization accounts for each instructor's frozen daily teaching
  limit, including locked meetings, multiple instructors, and actual occupied
  teaching atoms. It no longer favors raw faculty preferences when their
  approved objective weight is zero.
- Candidate scans reuse non-room conflict counts for placements at the same
  time. A zero-cost placement ends a shuffled scan without favoring time groups
  that happen to have fewer room alternatives. Mutation avoids allocating a
  complete alternatives list for every changed gene.
- Repair searches implicated meetings for a hard-conflict reduction before
  accepting a smaller soft-penalty improvement. This corrects the previous
  ordering that could spend an iteration improving preferences on the first
  meeting while leaving a removable conflict on another.
- When single moves cannot reduce conflicts, repair can try a second unlocked
  meeting after a neutral-hard intermediate move. It retains at most four
  intermediate candidates and makes at most 64 second-move evaluation requests
  per repair call, including cache hits. Only strict final improvements are
  adopted; new counters expose the additional work. This bounded operator solves
  the regression fixture requiring two coordinated moves, but the larger dense
  benchmark below still fails within three seconds.
- Expired or unfinished initialization produces no partial incumbent. Every
  accepted incumbent must finish independent validation and scoring within the
  deadline. Large candidate-list allocation/shuffling and final serialization
  can still add wall-clock overhead.

Changed implementations require a fresh excluded synthetic pilot before formal
profiles are frozen. Earlier results remain historical evidence and must not be
silently relabeled as `ga-v4` results.

## Matched synthetic comparison

Three complete schema-1.2 fixtures contain 30, 48, and 40 meetings, respectively.
Each has an independently validated feasible witness that is not supplied to the
solver, apart from the fixture's declared locks. The measured configuration uses
one worker, population 40, tournament size 3, crossover 0.9, automatic mutation,
elite fraction 0.05, 20 repair attempts, and a three-second deadline. Each case
uses seeds 5001–5005, with a separate 0.25-second warmup.

The table compares the final implementation run with the repeated baseline run
performed afterward. Both use identical problem/configuration hashes and the
same benchmark harness and supporting source hashes. Earlier development runs
remain in the local artifact directory; they are not pooled as independent
observations.

| Synthetic case | Feasible runs, before → after | Median feasible penalty, before → after | Median first feasibility, before → after |
|---|---:|---:|---:|
| Mixed rooms/resources, 30 meetings | 5/5 → 5/5 | 382 → 370 | 0.000 s → 0.015 s |
| Fully occupied grid, 48 meetings | 0/5 → 0/5 | Not available | Not reached |
| Daily-load limits, 40 meetings | 5/5 → 5/5 | 41 → 37 | 0.641 s → 0.015 s |

Lower penalty is better. The penalty changes are approximately 3.1% and 9.8% for
the mixed and daily-limit cases. Individual seeds can regress, and the new
implementation is not uniformly faster. Windows elapsed times in these runs
are visibly quantized: a recorded zero does not mean zero computation. The
fully occupied fixture is known feasible; `NO_SOLUTION` here describes the
search result within a short budget, not a proof of infeasibility.

Five seeds and shortened deadlines are insufficient for a formal statistical
conclusion. No room-capacity policy, objective weights, formal seeds, or
institutional records were changed to improve these outcomes.

## Evidence and reproduction

[Machine-readable observations](evidence/algorithm/ga-v4-development.json)
contain all 30 final comparison observations, problem/configuration identifiers,
implementation and harness hashes, environment details, and raw report checksums.
Full source/problem snapshots, witnesses, diagnostics, and convergence traces
are saved locally under `experiment-results/ga-tuning-2026-08-30/`. That directory
is intentionally ignored by Git; preserve it when transferring this evidence.

```powershell
# Current implementation; does not access the database.
.venv\Scripts\python.exe scripts/benchmark_ga.py --output experiment-results/ga-current.json --seconds 3 --seeds 5001 5002 5003 5004 5005

# Saved starting implementation, using the same current supporting modules.
.venv\Scripts\python.exe scripts/benchmark_ga.py --output experiment-results/ga-baseline-replay.json --solver-source experiment-results/ga-tuning-2026-08-30/genetic_before.py --seconds 3 --seeds 5001 5002 5003 5004 5005
```

Replay requires the recorded supporting source and harness versions. Wall-clock
bounded runs can differ with host load even when their input seeds match.

## Verification

The focused regression tests cover independent feasibility, hard-first repair,
neutral two-move plateaus, locks, daily-load policy, disabled objectives,
interrupted construction/evaluation, and the second-move work cap.

`pytest -q --ignore=tests/e2e`: **262 passed, 3 diagnostic tests deselected,
137.70 seconds**. This includes both solvers' non-browser regression coverage;
the separate 30-run final benchmark comparison is diagnostic evidence, not an
additional count of tests or formal trials.

Lint, Django checks, migration drift, and `git diff --check` pass. No browser,
container, target-device, formal pilot, commit, push, or deployment result is
claimed for this algorithm-only pass.
