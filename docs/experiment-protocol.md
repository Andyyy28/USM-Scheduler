# CP-SAT versus Genetic Algorithm experiment protocol

This protocol is the default preregistration for the thesis comparison. Any change must be written, dated, justified, and approved by the adviser **before** inspecting final comparative results.

## Research questions and outcomes

The experiment asks which engine is more suitable under the defined, authorized USM scheduling instances.

Outcome priority is fixed:

1. **Correctness and feasibility:** independently validated hard-violation count and feasible-schedule generation rate.
2. **Quality:** shared weighted soft penalty and normalized schedule-quality score among feasible results.
3. **Time:** time to first feasible schedule and total execution time under the same deadline.
4. **Reliability/supporting behavior:** across-seed consistency, attempts required to obtain feasibility, stopping reason, and descriptive room-slot utilization.

No fast or high-quality result is considered usable when its independent hard-violation count is nonzero.

## Experimental unit and controlled factors

One experimental unit is:

`problem snapshot × algorithm × seed × solver configuration × machine/software build`

For every CP-SAT/GA comparison block, hold constant:

- immutable `ProblemSnapshot` hash and schema version;
- committed term-revision content hash;
- approved objective-profile hash, weights, definitions, and normalization denominators;
- legal candidate map and active locks;
- random seed;
- 300-second wall-clock solver deadline;
- one solver worker/CPU allocation;
- host, power mode, operating system, container/image or lockfile, Python and library versions; and
- absence of other deliberate solver work on the test machine.

CP-SAT uses `num_search_workers=1` and its recorded seed. GA is single-threaded. Default frozen GA configuration is population 200, tournament size 3, crossover rate 0.90, per-gene mutation `1 / number_of_events`, elite fraction 0.05, 20 repair attempts, and time-limit termination with no generation cap. These defaults may be replaced only by a pilot-selected profile frozen before final trials.

## Dataset instances

### Full instance

Build the primary snapshot from all active authorized offerings in the selected USM term. Report term, campus, colleges represented, meetings, sections, instructors, rooms by kind, time atoms, legal candidate count, lock count, and missing-data assumptions. Conclusions apply to this measured instance; do not imply all universities or unobserved terms.

### Scaling instances

Create three nested subsets at 25%, 50%, and 75% of active course offerings, plus the 100% full instance:

1. sort offerings within `(offering college, curricular classification)` strata by SHA-256 of `snapshot-construction-seed + external_key`;
2. take the first ceiling of 25%, 50%, and 75% from every nonempty stratum;
3. include each selected offering’s complete sections, instructors, meeting requirements, capabilities, locks, and relevant availability/authorization rows;
4. retain the full room and time resource pool so the independent variable is meeting demand, and report this choice; and
5. build and record a separate immutable snapshot/hash for each level.

Use snapshot-construction seed `20260824`. If a subset contains a lock or reference whose linked offering was not selected, include the offering rather than dropping the institutional rule, then report the actual percentage. Never hand-pick an “easy” subset.

Before final experiments, run a diagnostic CP-SAT feasibility check with a separate 1,800-second limit. Use it only to classify/diagnose instances; exclude diagnostic timings and solutions from final results. A CP-SAT proof of infeasibility is a property of the encoded instance, not an algorithm failure. Correct data errors before freezing final snapshots; retain genuinely infeasible instances as a separately reported stress category rather than mixing them with known-feasible success-rate cells.

If official authorization covers more than one semester, repeat the full protocol per term and report term-stratified results before any pooled summary.

## Pilot tuning and leakage prevention

Use synthetic fixtures or one designated pilot subset that will not be part of final inference. Give CP-SAT and GA the same total tuning wall-clock budget. Pilot goals are implementation checking and selection of one GA configuration; do not tune objective weights to make an algorithm win.

The executable pilot grid contains 24 configurations: population `100/200/400`, tournament size `3/5`, crossover `0.80/0.90`, and mutation `1/N` or `2/N`, each run with synthetic-only seeds `2001â€“2010`. The selection order is highest feasibility rate, lowest median feasible raw penalty, then lowest median execution time; a configuration-ID hash breaks an exact tie. `python manage.py ga_tuning_grid <synthetic_snapshot_id>` emits the complete 240-run plan and hash without executing it. Only explicit `--mode direct` or `--mode queue` launches the pilot.

Before final runs, freeze:

- source revision, snapshot and objective-profile hashes;
- solver configurations and dependency versions;
- seeds and randomized execution order;
- metric formulas, exclusions, and statistical tests; and
- scripts/queries used to create result tables.

Do not change any of these after inspecting the final algorithm labels. If a defect forces a rerun, invalidate all affected comparison-block trials, document the defect and code revision, and rerun both algorithms.

## Run matrix and execution

For every known-feasible snapshot size, execute both algorithms with the 30 fixed seeds `1001` through `1030`. This creates 60 trials per snapshot. Randomize CP-SAT/GA order inside each seed block using order seed `20260824`; execute sequentially and retain that order. A shared numeric seed is a reproducibility control, not a statistical pairing mechanism: the algorithms transform randomness differently.

Before measured trials on a machine/build, execute one unmeasured warm-up invocation of each engine on the same snapshot/configuration. Mark both as warm-ups and exclude them from all denominators, inference, and result tables.

For each trial:

1. verify the snapshot and configuration hashes;
2. record UTC start, machine/build metadata, algorithm, seed, and deadline;
3. run the solver in a fresh task/process context with one worker;
4. record monotonic runtime and first-feasible time from inside the solver boundary, including algorithm-specific model/population construction (exclude shared preprocessing, import, queue delay, persistence, and report rendering);
5. run the common independent validator and scorer;
6. persist assignments, status, stopping reason, objective breakdown, diagnostics, and raw solver metrics; and
7. mark infrastructure crashes separately and rerun the entire CP-SAT/GA pair once after fixing the infrastructure cause. Preserve the failed records but exclude only confirmed infrastructure faults from algorithm denominators.

Do not manually retry an algorithm until it succeeds. All 30 prespecified trials—including timeouts and no-solution results—remain in the analysis.

## Metric definitions

### Hard violations

The independent validator checks missing/duplicate/illegal placement, lock mismatch, room conflict, instructor conflict, section conflict, and distinct-day conflict. A resource collision is counted once per unique `(resource, event pair)`, regardless of how many time atoms the collision spans.

Report total and category counts for every returned candidate. A proof of infeasibility/no returned schedule is reported as that status, not misleadingly compared as an ordinary candidate with one “missing assignment” per event.

### Feasible-schedule generation rate

`independently feasible trials / all non-infrastructure trials`

Report numerator, denominator, percentage, and 95% Wilson interval. “Feasible” requires a complete candidate and zero hard violations. CP-SAT `OPTIMAL` is also feasible; GA cannot claim proof of optimality or infeasibility.

### Time

- **Time to first feasible:** monotonic seconds from solver entry until the first independently feasible incumbent is observed.
- **Execution time:** monotonic seconds from solver entry until return, including search but excluding snapshot building and persistence.

Trials without feasibility are right-censored at 300 seconds. Primary time-to-feasibility summaries include restricted mean time to feasibility (RMST) through 300 seconds; do not calculate a deceptively fast mean using successes only. Also report median/IQR execution time and first-feasible time among successes as clearly labeled secondary summaries.

### Schedule quality

Evaluate only independently feasible complete schedules with the common scorer. Lower raw weighted penalty is better. The normalized score is:

`100 × (1 − mean of active normalized weighted penalties)`, clamped to `[0, 100]`.

The components are faculty-preference penalty, section internal-gap atoms, instructor internal-gap atoms, and daily-load imbalance. Report component values, raw weighted total, and 0–100 quality. If faculty preferences are unavailable, set that objective weight to zero in the pre-approved profile and state that the measure was unavailable—do not fabricate preferences.

### Consistency

For each algorithm/instance, calculate all-pairs normalized Hamming distance between feasible event→candidate placement maps. `0` means identical schedules; `1` means every placement differs. Report median/IQR/MAD distance and the distribution of quality scores. Consistency is descriptive: diversity is not automatically bad if feasibility and quality remain strong.

### Attempts/retries required

Benchmark seeds are not retries. A retry is recorded only for a separately identified operational episode in which an authorized scheduler deliberately starts another attempt after a prior result. Cap one episode at five attempts, preserve every attempt, and report whether/when feasibility was reached. Never hide these attempts inside benchmark execution time or infer retries from the 30-seed research matrix.

### Resource utilization

`occupied room-time atoms / active, non-break, available room-time atoms in the snapshot × 100`

Count every physical room atom once. Do not use seats or chairs. Because every complete schedule contains the same fixed-duration meetings, aggregate utilization will often be constant across algorithms; report it as a descriptive validity/resource-demand measure, not evidence that one solver optimized utilization. If room-load balance is later studied, preregister a separate dispersion metric rather than redefining utilization after seeing results.

## Statistical analysis

Use two-sided `α = .05`; report exact sample sizes, estimates, 95% intervals, adjusted p-values, and effect sizes. Retain per-instance results rather than pooling away scale.

The implementation uses deterministic 10,000-resample percentile bootstrap intervals for medians and two-sided 10,000-resample independent label-permutation tests. It applies Holm–Bonferroni adjustment across the four prespecified outcome comparisons within each instance report.

- Feasibility: success counts, percentage-point difference, Wilson intervals per algorithm, and a preregistered label-permutation comparison of the two independent outcome samples.
- Time to feasibility: RMST through 300 seconds as the primary censor-aware estimate. Add a preregistered unpaired permutation or Mann–Whitney comparison on the declared analysis representation, with the censoring limitation stated.
- Quality/penalty: among feasible outputs, use a preregistered unpaired permutation or Mann–Whitney comparison, bootstrap intervals, and Vargha–Delaney A12. The common seed numbers do not make the solver outcomes statistically paired.
- Consistency: descriptive median/IQR/MAD normalized Hamming distance; do not attach a winner claim without a preregistered preference for stability.
- Multiple testing: the implemented per-instance report applies Holm–Bonferroni adjustment across its four prespecified comparisons. Any later cross-instance aggregate analysis must declare and adjust its own family separately.

Always show distributions or individual trial points alongside aggregates. Never report only the single best seed.

## Primary-engine decision rule

Apply this lexicographic rule per full USM instance:

1. An engine whose produced candidates are not independently feasible is not eligible on those trials.
2. Prefer higher feasible-generation rate when the difference is at least 5 percentage points and the Holm-adjusted preregistered feasibility comparison is significant.
3. Otherwise, if feasibility is not materially different, prefer lower common raw soft penalty only when its median is at least 5% lower than the other engine's nonzero median and the Holm-adjusted unpaired test is significant at `.05`; report the component breakdown, per-meeting penalty, and normalized score alongside it. A zero comparator median cannot be improved under this rule.
4. Otherwise, prefer lower RMST to feasibility when the relative reduction is at least 10% and the declared secondary analysis points in the same direction.
5. If none applies, conclude that neither engine demonstrated a practically and statistically meaningful overall advantage; recommend based on transparent secondary tradeoffs rather than declaring a forced winner.

CP-SAT proof capability is an operational advantage to discuss separately. GA diversity is a possible decision-support advantage, not evidence of correctness.

## Required result tables

1. Instance characteristics and snapshot hashes.
2. Frozen solver/objective configurations and environment versions.
3. Status/feasibility counts with Wilson intervals.
4. Hard-violation categories for non-feasible returned candidates.
5. RMST and runtime descriptive statistics.
6. Quality and component penalties among feasible trials.
7. Consistency, attempts/retries, and utilization supporting measures.
8. Independent-sample tests, Holm-adjusted p-values, effect sizes, and practical-threshold decisions.
9. Failure/stopping reasons and excluded infrastructure events.

Archive de-identified trial-level CSV/JSON, report-generation code, hashes, and dependency versions with adviser/USM approval. Do not publish the source institutional workbook or direct identifiers.

## Threats to validity and mitigations

- **One-term external validity:** label conclusions as a USM case study and repeat across terms when authorized.
- **GA tuning bias:** separate pilot data, equal tuning budget, and freeze configuration.
- **Objective-weight bias:** approve versioned weights before final runs and add a prespecified sensitivity analysis.
- **Hardware noise:** sequential randomized execution on one machine, one worker, same power mode, and monotonic clocks.
- **Missing/incorrect source data:** staged validation, completeness acknowledgements, administrative review, and documented assumptions.
- **Instance infeasibility versus algorithm failure:** diagnostic exact check, separate proven-infeasible strata, and precise statuses.
- **Selective reporting:** retain every prespecified seed, timeout, no-solution result, and stopping reason.
