# GA-v5 development — 30 August 2026

GA-v5 limits repair work so a difficult offspring cannot occupy the entire
search budget. It also prepares shared evaluation lookups once per solve and
adds feasible schedule improvement after completed generations. The primary
development target is 30 seconds; three seconds remains a stress test.

At 30 seconds, dense-case feasibility improved from **2/10 to 10/10** across
the original and additional dense fixtures. All 15 easier-case runs remained
feasible in both versions. This satisfies the development feasibility gate on
these fixtures; it is not a guarantee for other schedules.

These are synthetic development measurements, not formal thesis evidence or a
comparison against CP-SAT. Objective weights, candidate domains, database
schemas, browser APIs, CP-SAT source, and the formal tuning grid were not changed
by this pass. No profiles were frozen and nothing was committed or deployed.

## Implementation and safeguards

- `PreparedProblem` holds read-only event/candidate lookups and per-day atom
  positions. Preparation is inside the solve deadline. The optional `prepared`
  argument leaves existing validator/scorer callers valid, rejects a context for
  another problem object, and preserves every raw policy check. It caches
  lookups, not feasibility decisions.
- Each prospective incumbent is independently validated and scored without the
  prepared context. It must agree exactly and finish by the solve deadline;
  a mismatch fails closed. An expired evaluation cannot introduce an incumbent,
  including when its result later exists in the chromosome cache.
- Repair requests at most 128 evaluations per call, including cache hits: at
  most 96 single-state requests (the initial state consumes one) and 32
  coordinated requests. The existing iteration limit still applies. Candidate
  ordering favors fewer blockers; coordinated requests alternate across four
  retained intermediates and prioritize implicated blocking meetings. Locks
  remain fixed and the returned fitness cannot worsen.
- After a completed generation, the best feasible incumbent receives at most
  64 additional single-move/swap trials within the remaining solve deadline.
  A swap must exist in both meetings' candidate domains. Only independently
  feasible schedules with a strictly lower existing weighted penalty survive.
  A partial generation padded with copies does not trigger this pass.
- Counters expose completed offspring/generations, incumbent rechecks, repair
  requests and budget exhaustion, and feasible improvement work. The maximum
  per-call counters verify the limits. `repair_budget_exhaustions` counts calls
  that reach either sub-budget, not just calls that consume all 128 requests.
  `completed_offspring` counts timely, unique offspring admitted to a generation.
- Phase times appear only with `diagnostic_trace=True`. Initialization includes
  preparation/evaluation; repair and feasible improvement include their
  validation/scoring calls. These times overlap and must not be summed as a
  partition of runtime. Post-search result serialization/revalidation can add
  wall time but cannot improve the deadline-qualified incumbent.

## Staged screen at 3, 10, and 30 seconds

The saved starting `ga-v4`, prepared evaluation plus diagnostics/certification,
bounded repair, and the combined `ga-v5` were measured serially on the same three
schema-1.2 inputs with seed 5001. These are cumulative stages, each identified by
its source hash. The saved GA-v4 uses the current shared evaluator's unprepared
path, whose behavior is covered by differential tests. Each cell is one run;
this screen is not five-seed evidence for every intermediate stage.

Values are feasible weighted penalties (lower is better), except explicit
failures showing the remaining number of hard violations.

| Case | Seconds | Saved GA-v4 | Prepared | Bounded repair | GA-v5 |
|---|---:|---:|---:|---:|---:|
| Mixed, 30 meetings | 3 | 330 | 330 | 337 | 283 |
| Mixed, 30 meetings | 10 | 309 | 264 | 238 | 241 |
| Mixed, 30 meetings | 30 | 255 | 227 | 232 | 183 |
| Dense, 48 meetings | 3 | Fail: 2 | Fail: 2 | 591 | 591 |
| Dense, 48 meetings | 10 | Fail: 1 | 569 | 558 | 520 |
| Dense, 48 meetings | 30 | 569 | 569 | 449 | 442 |
| Daily limits, 40 meetings | 3 | 37 | 37 | 35 | 37 |
| Daily limits, 40 meetings | 10 | 37 | 37 | 35 | 37 |
| Daily limits, 40 meetings | 30 | 37 | 37 | 35 | 35 |

The prepared stage reached dense-case feasibility at ten seconds where GA-v4
did not. Bounded repair reached it at three seconds where the prepared stage
did not. At the primary 30-second target, adding feasible improvement lowered
mixed/dense penalties from 232/449 to 183/442 and tied the daily-limit result.
Those observations support retaining the three changes for this development
build. They do not establish a stable effect estimate for each component.

The quality stage was not uniformly better: at ten seconds its mixed penalty
was 241 versus 238 for bounded repair alone; at three and ten seconds its daily
penalty was 37 versus 35. Local acceptance still requires a strict improvement,
but extra trials change the subsequent RNG trajectory and consume search time.
All these outcomes remain in the evidence, including the regressions.

The first dense three-second GA-v5 run completed 25 offspring and found its
first feasible schedule at approximately 2.05 seconds. Its largest repair call
used 128 evaluation requests. It completed no generation, so feasible local
improvement did not account for this first short-budget success.

## Five-seed validation

The combined candidate and saved GA-v4 use 30 seconds and seeds 5001–5005. All
five cases completed and passed independent evidence revalidation. The two
previously unused synthetic variants contain 60 fully occupied-grid meetings and
32 meetings with binding daily limits. They belong to the same synthetic fixture
family and were not used to change the algorithm after their results were seen.
No variant's feasible witness enters the solver; only declared locks are part of
its input.

| Synthetic case | Feasible runs, GA-v4 → GA-v5 | Median feasible penalty, GA-v4 → GA-v5 | Median first feasibility among successes |
|---|---:|---:|---:|
| Mixed, 30 meetings | 5/5 → 5/5 | 256 → 241 | 0.015 → 0.000 s |
| Dense, 48 meetings | 2/5 → 5/5 | 557 → 446 | 11.59 → 3.67 s |
| Daily limits, 40 meetings | 5/5 → 5/5 | 37 → 33 | 0.016 → 0.015 s |
| Additional dense variant, 60 meetings | 0/5 → 5/5 | Not available → 414 | Not reached → 7.47 s |
| Additional daily-limit variant, 32 meetings | 5/5 → 5/5 | 162 → 161 | 0.000 → 0.000 s |

Dense-case medians use different successful subsets and must not be treated as
paired effect estimates. On the two dense seeds where both engines succeeded,
GA-v5 lowered the penalty in both. For mixed schedules it improved three seeds
and worsened two (5002: 256 → 263; 5005: 251 → 257), without losing feasibility.
For each daily-limit variant it improved three and tied two. Across all
25 matched 30-second observations, GA-v5 gained feasibility on eight and lost it
on none. Of the 17 pairs where both versions were feasible, penalties improved
on 11, tied on four, and worsened on two.

All runs use population 40, tournament size 3, crossover 0.9, automatic mutation,
elite fraction 0.05, repair iteration limit 20, one worker, no generation cap,
and diagnostic traces. Every measured cell has a separate 0.25-second warmup.
Execution order reverses between seed/budget blocks. Timed comparisons are not
run concurrently with other solvers or the regression suite.

For the five original dense-case GA-v5 runs, completed offspring ranged from
367 to 530. Median initialization was about 0.21 seconds, validation 16.82,
scoring 5.07, repair 29.25, and feasible improvement 0.51 seconds. These are
overlapping phase times. The quality pass accepted 11–19 strict feasible
improvements per run; it did not receive a separate time budget. Validation
remains the largest measured evaluation cost.

## Failed observations

All 11 unsuccessful searches from the 80-cell comparison are listed below.
They returned no feasible incumbent within their budget; none proves the
fixture infeasible. Raw conflicting penalties and complete results remain in
the machine-readable evidence. GA-v5 had no feasibility failure in this matrix,
but the penalty regressions above remain relevant.

| Implementation | Case | Seconds | Seed | Final hard violations |
|---|---|---:|---:|---:|
| GA-v4 | Dense, 48 meetings | 3 | 5001 | 2 |
| Prepared stage | Dense, 48 meetings | 3 | 5001 | 2 |
| GA-v4 | Dense, 48 meetings | 10 | 5001 | 1 |
| GA-v4 | Dense, 48 meetings | 30 | 5002 | 1 |
| GA-v4 | Dense, 48 meetings | 30 | 5003 | 1 |
| GA-v4 | Dense, 48 meetings | 30 | 5005 | 1 |
| GA-v4 | Additional dense, 60 meetings | 30 | 5001 | 2 |
| GA-v4 | Additional dense, 60 meetings | 30 | 5002 | 2 |
| GA-v4 | Additional dense, 60 meetings | 30 | 5003 | 3 |
| GA-v4 | Additional dense, 60 meetings | 30 | 5004 | 3 |
| GA-v4 | Additional dense, 60 meetings | 30 | 5005 | 3 |

## Reproduction and evidence

Source snapshots, complete results, problem snapshots, witnesses, and convergence
traces are preserved under the ignored `experiment-results/ga-v5/` directory.
Keep that directory with any transfer of the evidence. The old GA-v4 tuning
report remains historical and is not overwritten with these results.

The [machine-readable evidence](evidence/algorithm/ga-v5-development.json)
contains all **80 observations**, paired comparisons, failed observations,
source/harness/supporting-source hashes, environment identities, diagnostic
counters, and raw report checksums. Every complete saved result was independently
validated and rescored. Across all 31 GA-v5 runs (including shorter stress runs),
the largest repair call used 128 requests and the largest quality pass used
64 trials, matching the implementation caps.

```powershell
# Cumulative one-seed stage screen: 36 measured cells.
.venv\Scripts\python.exe scripts/compare_ga.py --output experiment-results/ga-v5/screen --source ga-v4=experiment-results/ga-v5/baseline/genetic.py --source prepared=experiment-results/ga-v5/prepared_only.py --source bounded=experiment-results/ga-v5/bounded_repair.py --source ga-v5=scheduler/solvers/genetic.py --budgets 3 10 30 --seeds 5001

# Four remaining development seeds: seed 5001 is already in the screen.
.venv\Scripts\python.exe scripts/compare_ga.py --output experiment-results/ga-v5/validation --source ga-v4=experiment-results/ga-v5/baseline/genetic.py --source ga-v5=scheduler/solvers/genetic.py --budgets 30 --seeds 5002 5003 5004 5005

# Previously unused synthetic variants, five seeds each.
.venv\Scripts\python.exe scripts/compare_ga.py --output experiment-results/ga-v5/holdouts --source ga-v4=experiment-results/ga-v5/baseline/genetic.py --source ga-v5=scheduler/solvers/genetic.py --budgets 30 --seeds 5001 5002 5003 5004 5005 --cases unseen_dense unseen_daily

# Independently revalidate all saved outputs before producing the compact report.
.venv\Scripts\python.exe scripts/report_ga_comparison.py experiment-results/ga-v5/screen experiment-results/ga-v5/validation experiment-results/ga-v5/holdouts --output docs/evidence/algorithm/ga-v5-development.json
```

`compare_ga.py` resumes only complete matching observations. Source/harness/
supporting-source hashes, Python/platform, seeds, cases, and budgets must match.
Use a fresh output directory after changing an implementation; an incomplete
observation is preserved and rejected rather than silently overwritten.
`report_ga_comparison.py` checks matrix completeness, inputs, source identities,
independent feasibility and scoring, deadlines, duplicates, and comparison
configuration hashes. Profiled runs are excluded from quality comparisons.

## Regression verification and limits

Focused tests cover prepared/default equivalence on valid, conflicting,
incomplete, malformed, and policy-invalid schedules; problem identity and
immutability; sparse time-grid order; repair locks, coordinated plateaus, four
intermediate candidates, cache-hit caps and late evaluations; feasible swaps,
rejection of cheaper conflicts; diagnostic-only timings; and report tampering
or interrupted/resumed comparisons.

| Check | Final result |
|---|---|
| `pytest -q --ignore=tests/e2e` | **295 passed, 3 diagnostic tests deselected; 123.63 seconds** |
| `ruff check .` | Passed |
| `git diff --check` | Passed; Git emitted line-ending normalization notices only |
| `manage.py check` | Passed; no issues |
| `manage.py makemigrations --check --dry-run` | Passed; no changes detected |
| Saved-result evidence revalidation | All 80 observations passed; input/configuration/source hashes matched |
| Current measured build | Current GA source matches the recorded GA-v5 hash; CP-SAT matches its saved starting source byte-for-byte |

The final regression suite had no failures and includes both solvers' non-browser
coverage. The three deselected tests are opt-in performance/research-protocol
exercises, excluded by the repository's normal test configuration. The 80
development measurements are separate observations, not additional tests or
formal trials. The final pytest transcript is retained locally at
`experiment-results/ga-v5/non-browser-tests.txt`.

Time-limited search can vary with host load even for the same seed. Windows
elapsed-clock values can be quantized; zero does not mean zero computation.
Synthetic witnesses prove these fixtures are feasible, not that every GA run
will solve them. These measurements do not prove a formal statistical advantage,
general scalability, target-device performance, or superiority over CP-SAT.
The formal equal-budget pilot and thesis deadline remain unchanged. Browser,
container, institutional-data, and target-device acceptance were not run here.
