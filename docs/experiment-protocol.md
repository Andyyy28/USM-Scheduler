# CP-SAT versus Genetic Algorithm experiment protocol

This protocol is the default preregistration for the thesis comparison. Any change must be written, dated, justified, and approved by the adviser **before** inspecting final comparative results.

## Research questions and outcomes

The experiment asks which engine is more suitable under the defined, authorized USM scheduling instances.

The three primary outcomes and their priority are fixed:

1. **Feasibility:** independently validated feasible-schedule generation rate.
2. **Quality:** shared raw weighted soft penalty among independently feasible results.
3. **Time to feasibility:** censor-aware restricted mean time to first independently feasible schedule under the common deadline.

Hard-violation categories, normalized quality, total execution time, resource use,
across-seed consistency, stopping reasons, and room-time utilization are
diagnostic or secondary outcomes. They must not be promoted into an unregistered
fourth primary comparison or combined into an invented overall score.

No fast or high-quality result is considered usable when its independent hard-violation count is nonzero.

## Experimental unit and controlled factors

One experimental unit is:

`problem snapshot × algorithm × seed × solver configuration × machine/software build`

For every CP-SAT/GA comparison block, hold constant:

- immutable `ProblemSnapshot` hash and schema version;
- committed term-revision content hash;
- approved constraint-policy manifest hash and fixed 50-student rule;
- approved objective-profile hash, weights, definitions, and normalization denominators;
- legal candidate map and active locks;
- random seed;
- 300-second wall-clock solver deadline;
- one logical CPU, one solver worker, and a 2,048 MB memory limit;
- host, power mode, operating system, container/image or lockfile, Python and library versions; and
- absence of other deliberate solver work on the test machine.

CP-SAT uses `num_search_workers=1` and its recorded seed. GA is single-threaded.
Both final solver profiles are selected by the equal-budget synthetic pilot below
and frozen before any authorized-term comparative result is inspected.

The deadline includes solver-specific initialization, candidate evaluation, and
the shared independent validator/scorer. Only incumbents whose validation and
scoring finish by the deadline can be returned as usable results. A late first
incumbent remains an unsuccessful algorithm observation, right-censored at the
deadline; the 60-second infrastructure grace period cannot improve its schedule.
Post-run validation and persistence are recorded separately. CP-SAT optimality
or infeasibility proofs obtained after the deadline do not become in-budget proof
claims. Implementation versions `cp-sat-v3` and `ga-v5` enforce this boundary.
The GA also interrupts unfinished greedy construction and discards partial
chromosomes. Candidate-list allocation/shuffling and final result serialization
can still add overhead; the incumbent acceptance deadline remains strict.
The GA-v5 prepared lookup context is built within that budget, and prospective
incumbents are independently rechecked without it before acceptance. Its fixed
128-request repair cap and 64-trial feasible improvement pass do not introduce
new formal pilot configurations or change the 300-second study deadline.

Excluded seed-9001 diagnostics alone enable convergence logging. They record
improving incumbents, at most 512 points per run with deterministic thinning,
in separate feasible-penalty and hard-violation panels. Missing traces are not
zero-penalty observations. Measured trials have tracing disabled. The dashboard,
API, and downloadable report use the same 10,000-resample deterministic analysis.

## Dataset instances

### Full instance

Build the primary snapshot from all active authorized offerings in the selected
USM term. Report term, campus, colleges represented, meetings, sections,
instructors, rooms by kind, time atoms, legal candidate count, lock count,
section-headcount distribution, meetings approaching the fixed limit, and
missing-data assumptions. Conclusions apply to this measured instance; do not
imply all universities or unobserved terms.

Every section must have a frozen expected enrollment from 1 through 50. Every
meeting must have at most 50 students after summing the expected enrollment of
its unique attached sections. Exactly 50 is valid; 51 or more blocks snapshot
creation. This is a uniform academic scheduling rule for classrooms,
laboratories, and special-purpose rooms—not a variable room-capacity, chair, or
floor-space model. Participating rooms must be administratively prevalidated for
the baseline before the term is frozen.

### Scaling instances

Create three nested subsets at 25%, 50%, and 75% of active course offerings, plus the 100% full instance:

1. sort offerings within `(offering college, curricular classification)` strata by SHA-256 of `snapshot-construction-seed + external_key`;
2. take the first ceiling of 25%, 50%, and 75% from every nonempty stratum;
3. include each selected offering’s complete sections, instructors, meeting requirements, capabilities, locks, and relevant availability/authorization rows;
4. retain the full room and time resource pool so the independent variable is meeting demand, and report this choice; and
5. build and record a separate immutable snapshot/hash for each level.

Use snapshot-construction seed `20260824`. If a subset contains a lock or reference whose linked offering was not selected, include the offering rather than dropping the institutional rule, then report the actual percentage. Never hand-pick an “easy” subset.

Before final experiments, run a diagnostic CP-SAT feasibility check with a separate 1,800-second limit. Use it only to describe/diagnose instances; exclude diagnostic timings and solutions from final results. A proof of infeasibility is a property of the encoded instance, not an infrastructure failure. Do not remove a frozen scale or measured trial based on diagnostic outcomes. Correct data errors before collecting formal trials and create a new frozen study if the problem changes.

If official authorization covers more than one semester, repeat the full protocol per term and report term-stratified results before any pooled summary.

### Formal and exploratory studies

Every research batch is permanently classified when created:

- **Exploratory:** development, diagnostics, synthetic tuning, or configurable
  trials. Exploratory results may identify defects or hypotheses but cannot
  produce the thesis winner.
- **Formal:** the preregistered matrix in this document, created only from one
  authorized full snapshot and its deterministic nested scaling snapshots.

A formal study freezes its source snapshot, selected offerings at every scale,
constraint-policy and objective-profile hashes, solver profiles, dependency and
build identifiers, seeds, deadlines, scale seed, order seed, and exclusions.
Formal preflight must reject missing enrollment, missing availability,
unapproved reserved blocks, absent instructor daily-load policy, placeholder
normalizers, incomplete rule provenance, or empty candidate domains. An
incomplete, cancelled, unclassified, provenance-mismatched, or otherwise invalid
study reports **No formal conclusion available**.

## Pilot tuning and leakage prevention

Use synthetic fixtures that will not be part of final inference. CP-SAT and GA
each receive at most 30 minutes of allocated pilot time: six configurations ×
five seeds (`2001–2005`) × 60 seconds. Pilot runs are exploratory and excluded
from formal denominators. Never tune objective weights against algorithm results.

The CP-SAT grid is presolve enabled/disabled × linearization level `0/1/2`.
The GA grid is population `100/200/400` × mutation `1/N` or `2/N`, with
tournament size 3, crossover 0.90, elite fraction 0.05, and 20 repair attempts
held constant. Select each engine's profile lexicographically by highest
feasibility rate, lowest median feasible raw penalty, lowest RMST to feasibility,
then configuration hash. Freeze both selected profiles before formal trials.

The selected mutation multiplier remains `1` or `2`; resolve `min(1, multiplier/N)`
using each instance's mutable meeting count, with zero mutation when all meetings
are locked. The full pilot plan, synthetic-data acknowledgement, all 60 excluded
terminal observations, and the recomputed selection digest must authenticate
the profiles. A self-consistent JSON hash alone is not pilot evidence. Launching
the command requires `--confirm-synthetic`; repeated launches cannot duplicate
the same plan's allocated budget.

Before final runs, freeze:

- source revision, snapshot and objective-profile hashes;
- solver configurations and dependency versions;
- seeds and randomized execution order;
- metric formulas, exclusions, and statistical tests; and
- scripts/queries used to create result tables.

Do not change any of these after inspecting the final algorithm labels. If a defect forces a rerun, invalidate all affected comparison-block trials, document the defect and code revision, and rerun both algorithms.

## Run matrix and execution

For every frozen snapshot size, execute both algorithms with the 30 fixed
seeds `1001` through `1030`. This creates 60 measured trials per snapshot and
240 across 25%, 50%, 75%, and 100%. Randomize CP-SAT/GA order inside each seed
block using order seed `20260824`; execute sequentially and retain planned and
actual order. The preregistered feasibility and time analyses preserve each
scale-and-seed block as a pair; this controls the experimental block without
assuming that the engines transform their random streams identically.

Before measured trials on each scale, execute one warm-up invocation of each
engine on the same snapshot/configuration. Persist all eight warm-ups but exclude
them from denominators and inference. Also persist four separate 1,800-second
CP-SAT feasibility diagnostics and one excluded trace pair per scale using seed
`9001`. The planned study therefore contains 240 measured runs, eight warm-ups,
four feasibility diagnostics, and eight trace runs.

For each trial:

1. verify the snapshot and configuration hashes;
2. record UTC start, machine/build metadata, algorithm, seed, and deadline;
3. run the solver in a fresh child process with one worker, one CPU, a 2,048 MB
   limit, and a deadline plus 60-second infrastructure grace period;
4. record monotonic runtime and first-feasible time from inside the solver boundary, including algorithm-specific model/population construction (exclude shared preprocessing, import, queue delay, persistence, and report rendering);
5. run the common independent validator and scorer;
6. persist assignments, status, stopping reason, objective breakdown, diagnostics, and raw solver metrics; and
7. record source commit, image, dependency versions, host and process identity,
   UTC timestamps, process CPU time, peak RSS, and the exact worker-side manifest;
8. classify a failure as algorithm observation, confirmed infrastructure fault,
   user cancellation, or unclassified; and
9. rerun the entire CP-SAT/GA seed pair once after an audited infrastructure
   classification. Preserve originals and link the replacement pair. A second
   infrastructure failure invalidates the study instead of causing unlimited
   retries.

Do not manually retry an algorithm until it succeeds. All 30 prespecified trials—including timeouts and no-solution results—remain in the analysis.

## Metric definitions

### Hard violations

The independent validator checks missing/duplicate/illegal placement, lock
mismatch, room conflict, instructor conflict, section conflict, distinct-day
conflict, recurring reserved-block violation, instructor daily-load excess, and
the fixed 50-student meeting limit. A resource collision is counted once per
unique `(resource, event pair)`, regardless of how many time atoms the collision
spans.

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

### Room-time utilization

`occupied room-time atoms / active, non-break, available room-time atoms in the snapshot × 100`

Count every participating room atom once. Do not use capacities, seats, chairs,
or floor area. Because every complete schedule contains the same fixed-duration
meetings, aggregate room-time utilization will often be constant across
algorithms; report it as a descriptive resource-demand measure, not evidence
that one solver optimized physical space. Report the fixed 50-student rule and
section-headcount distribution separately.

## Statistical analysis

Use two-sided `α = .05`; report exact sample sizes, estimates, 95% intervals, adjusted p-values, and effect sizes. Retain per-instance results rather than pooling away scale.

Use deterministic 10,000-resample percentile bootstrap intervals and the
two-sided comparisons below. Apply Holm adjustment only
across the three primary comparisons within each scale.

- Feasibility: success counts, percentage-point difference, Wilson intervals per algorithm, and the exact paired binary test on discordant seed blocks.
- Time to feasibility: RMST through 300 seconds, a deterministic 10,000-resample within-seed swap permutation of the RMST difference, and a paired bootstrap interval for that difference. Unsuccessful trials remain right-censored at the deadline.
- Quality/penalty: use all independently feasible outputs, a deterministic two-sided 10,000-resample label-permutation test of medians, bootstrap intervals, and Vargha–Delaney A12. Do not discard unmatched feasible outputs to manufacture complete pairs.
- Consistency: descriptive median/IQR/MAD normalized Hamming distance; do not attach a winner claim without a separately preregistered preference for stability.
- Multiple testing: each scale applies Holm adjustment across feasibility, feasible quality, and time to feasibility. Any later cross-instance aggregate analysis must declare and adjust its own family separately.

Always show distributions or individual trial points alongside aggregates. Never report only the single best seed.

## Primary-engine decision rule

Apply this lexicographic rule only to a complete, protocol-valid 100% formal USM
instance:

1. Only independently feasible schedules enter the feasibility numerator and quality sample; every eligible algorithm observation remains in the feasibility and censored-time denominator.
2. Prefer higher feasible-generation rate when the difference is at least 5 percentage points and the Holm-adjusted preregistered feasibility comparison is significant.
3. Otherwise, if feasibility is not materially different, prefer lower common raw soft penalty only when its median is at least 5% lower than the other engine's nonzero median and the Holm-adjusted unpaired test is significant at `.05`; report the component breakdown, per-meeting penalty, and normalized score alongside it. A zero comparator median cannot be improved under this rule.
4. Otherwise, prefer lower RMST to feasibility when the relative reduction is at least 10%, its Holm-adjusted permutation result is significant at `.05`, and its bootstrap difference interval supports the same direction.
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
- **Solver tuning bias:** separate synthetic pilot data, equal tuning budgets, and freeze both configurations before authorized-term results.
- **Objective-weight bias:** approve versioned weights before final runs and add a prespecified sensitivity analysis.
- **Hardware noise:** sequential randomized execution on one machine, one worker, same power mode, and monotonic clocks.
- **Missing/incorrect source data:** staged validation, completeness acknowledgements, administrative review, and documented assumptions.
- **Instance infeasibility versus algorithm failure:** diagnostic exact check, separate proven-infeasible strata, and precise statuses.
- **Selective reporting:** retain every prespecified seed, timeout, no-solution result, and stopping reason.
