# Thesis defense notes

## Core defense position

This is a BS Computer Science contribution because it formalizes a real institutional timetabling problem, implements two different search paradigms over the same model, validates and scores them independently, and evaluates them with a controlled reproducible experiment.

The strongest claim is not “the system creates schedules.” It is:

> The study provides a versioned, college-boundary-aware constraint model and empirical evidence about the feasibility, time, reliability, and quality tradeoffs of CP-SAT and a Genetic Algorithm on authorized USM scheduling instances.

## Why it is not only CRUD

CRUD supports data preparation and oversight, but the research core includes:

- combinatorial decision variables across meetings, candidates, rooms, time atoms, instructors, and sections;
- formal hard constraints and common soft objective functions;
- candidate-domain preprocessing and infeasibility explanation;
- an exact/constraint-based solver and a population metaheuristic;
- a common immutable problem contract and independent validator/scorer;
- deterministic hashes, seeds, experiment batches, censor-aware time analysis, and statistical comparison; and
- human review, versioning, and locks that become hard inputs to regeneration.

A manual form cannot search this combinatorial space or prove infeasibility/optimality.

## Two-minute system explanation

1. USM supplies/authorizes one semester dataset.
2. The system validates it and freezes an immutable revision.
3. It enumerates legal placements for each required meeting using duration, availability, specialized-room capabilities, college authorization, and locks.
4. Both CP-SAT and GA receive the identical snapshot, weights, seed set, deadline, and CPU allowance.
5. CP-SAT models exact Boolean choices/constraints; GA evolves one legal-candidate index per meeting and ranks hard violations before soft penalty.
6. A third, algorithm-independent validator decides whether outputs are feasible, then one scorer measures quality.
7. Authorized staff review a version, request changes/lock accepted meetings, and centrally approve only an independently feasible schedule.
8. The thesis compares repeated controlled runs, not one impressive demo result.

## Likely panel questions and concise answers

### Why CP-SAT and GA?

CP-SAT is well suited to discrete exactly-one and no-overlap constraints and can prove optimality or infeasibility. GA represents a contrasting population-based metaheuristic that can explore many candidate timetables and is common in timetabling research. Comparing them on one contract isolates search approach rather than different rule implementations.

### Is the comparison fair if CP-SAT is built for constraints?

Both receive identical legal candidates, locks, hard definitions, shared soft scorer, seeds, one worker, and deadline. GA uses hard-first fitness/repair; neither validates itself. The study reports feasibility first and acknowledges CP-SAT proof capability rather than pretending both algorithms have identical theoretical guarantees.

### Why might CP-SAT win? Does that make the thesis obvious?

Tightly constrained assignment problems often suit CP-SAT, but relative performance depends on instance size, candidate density, objective structure, and deadline. An empirical negative or dominant result remains useful if the model, protocol, and limits are rigorous. The contribution includes when/why behavior changes, not a predetermined winner.

### How do you know a schedule is valid?

Candidate generation removes local illegality, both solvers encode/penalize shared constraints, and a separate validator checks completeness, locks, resource conflicts, and distinct-day rules. Database atom-allocation uniqueness provides another guard when promoting a timetable. Only zero violations is feasible.

### What does “college-boundary-aware” mean?

Subject classification is curriculum-specific through `ProgramSubject`. Room ownership and permission are separate. `RoomAuthorization` states which college/department may use a room for major, minor, or GE offerings in a particular revision, allowing shared facilities and explicit exceptions without assuming ownership equals authorization.

### Why is subject classification not a field on Subject?

The same catalog subject can be major in one curriculum and service/minor in another. Classification and authoritative unit therefore belong to the program/curriculum relationship and are validated through the offering-section link.

### Why no room capacity?

The approved scope explicitly excludes physical chair/capacity optimization. The schema can be extended later, but adding unreliable enrollment/capacity data would broaden the research question and threaten completion. Room utilization is room-time, not seats.

### Why store students at all?

The optimizer conflicts aggregate sections, so individual records are optional. If USM needs membership evidence, only pseudonymous codes are stored. The experiment can omit students entirely; this minimizes privacy risk and keeps the model aligned with the problem.

### How do you handle changing semesters?

Each term has its own active data revisions. Committed revisions are immutable, so incoming/continuing/graduating cohorts and changing availability are represented in a new revision without corrupting prior evidence. Historical runs retain their exact snapshot hash.

### What if there is no feasible schedule?

The system reports no solution or CP-SAT-proven infeasibility and identifies meetings/resources with exhausted candidates or conflicting requirements. It never silently relaxes a hard rule. Authorized personnel must correct data, change policy explicitly, or alter/justify locks in a new revision.

### Why repeated runs if CP-SAT can be deterministic?

GA is stochastic, and CP-SAT can react to a recorded search seed. Repeated runs measure reliability/distribution while one-worker execution reduces nondeterminism. Both engines use the same recorded seed set, but those numbers do not make their different random processes statistically paired. The protocol retains every timeout and records block order.

### Why not compare only execution time?

A fast invalid timetable has no operational value. The preregistered hierarchy is feasibility, quality, time, then supporting consistency/retry/utilization measures. Failures are censored at the deadline rather than discarded from time analysis.

### How is quality calculated without bias?

One approved objective profile defines nonnegative weights and normalizers before final experiments. Both algorithms use the same lower-is-better penalty and 0–100 normalized score. Component penalties and a sensitivity analysis are reported so conclusions do not rely on one hidden weighting choice.

### Is room utilization a useful comparison metric?

It is primarily descriptive because fixed meeting durations make total occupied room atoms similar or identical across complete schedules. The thesis reports it honestly and does not claim algorithm superiority from a constant metric. Gap/balance objectives are more discriminating.

### How do locks affect fairness?

Active locks are included in the same immutable problem snapshot and candidate map for both engines. They are hard preassignments, not manual post-processing of one algorithm’s result.

### Can this be generalized beyond USM?

The architecture and formal model may transfer, but empirical conclusions apply only to the authorized USM instances tested. The thesis should call this a USM case evaluation and report dataset composition/limitations.

### Why Django/Python?

Python supports OR-Tools and a transparent custom GA, while Django provides mature authentication, admin/data modeling, migrations, validation, and fast prototype development. PostgreSQL enforces scheduling and versioning invariants; Celery isolates long solver work from web requests.

## Weaknesses and mitigations

| Weakness | Honest mitigation |
|---|---|
| One or few semesters | term-stratified/scaling instances; narrow case-study claims; publish composition |
| Incomplete/dirty institutional data | staged import, stable errors, completeness acknowledgement, reviewer verification |
| GA configuration sensitivity | equal-budget pilot on separate data, frozen settings, sensitivity appendix |
| Objective weights are normative | adviser/scheduler approval before runs, versioned hash, component reporting |
| CP-SAT has stronger proof semantics | distinguish proof from heuristic failure; do not let GA claim optimal/infeasible |
| Runtime affected by hardware | same machine, one worker, sequential randomized order, monotonic clocks |
| Aggregate section assumption | document it; optional pseudonymous memberships; no claim about individual elective conflicts |
| University-wide label versus partial data | state actual colleges/term coverage; system-ready scope is not evaluation coverage |
| Utilization may be constant | treat as descriptive, retain balance/gap quality measures |
| No capacity or walking distance | explicit approved boundary and future work, not a hidden omission |

## Defense demonstration plan

Use a small de-identified fixture for the live demo and precomputed full-instance results for evidence.

1. Show a committed term revision and its hash.
2. Show one major laboratory meeting, its required capability, and authorized room candidates.
3. Intentionally show a rejected illegal room or overlapping assignment diagnostic.
4. Open CP-SAT/GA runs on the same snapshot/configuration and emphasize zero hard violations.
5. Compare time-to-feasible and shared quality components; do not wait for a large live solve.
6. Promote one feasible result, record a college change request, lock an accepted meeting, and show child-version regeneration logic.
7. Show that approved content is immutable and the audit trail remains.
8. End with the full experiment table and the preregistered decision rule.

Keep screenshots and a short recorded backup in case network/container setup fails. Never expose authorized workbook contents or personal identifiers during the defense.

## Evidence checklist

- Approved scope/rule catalogue and data authorization.
- Constraint-to-test traceability matrix.
- Architecture and data dictionary.
- Synthetic cases for every hard rule and infeasibility.
- Snapshot, objective, configuration, dependency, and source hashes.
- Pilot tuning separated from final data.
- All prespecified seeds, including timeout/no-solution records.
- Independent violation/scoring output.
- Reproducible statistical tables and effect sizes.
- UAT/accessibility/security evidence.
- Threats, limitations, and decision-support disclaimer.

## Claims to avoid

- “The algorithm always finds the best schedule” unless CP-SAT proved optimality for that exact run.
- “GA proved the schedule infeasible.” It cannot.
- “University-wide validated” when only selected colleges/one term were tested.
- “No conflicts” based only on visual inspection or solver status.
- “Better utilization” when only fixed total room-time occupancy was measured.
- “AI scheduling.” The contribution is formal optimization and empirical evaluation.
- “The system replaces the scheduling office.” It supports authorized human decisions.
