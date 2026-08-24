# Complete system development plan

## 1. System understanding

USM Scheduler coordinates subjects, active sections, instructors, meeting recurrence/duration, time atoms, classrooms, laboratories, availability, college/unit room authority, and administrative locks for one academic term. It prevents schedules that look complete but are operationally invalid.

This is an optimization problem because each meeting may have many individually legal room/time candidates, while selecting one candidate changes the remaining choices for shared instructors, sections, and rooms. With `n` meetings and an average of `c` candidates, the unconstrained search space is approximately `cⁿ`; local manual choices cannot reliably resolve global interactions or optimize schedule quality.

CP-SAT is appropriate because the problem is discrete and dominated by exactly-one, at-most-one, implication, and weighted-objective relationships. It can find feasible solutions and sometimes prove optimality or infeasibility. A GA is appropriate as a contrasting stochastic metaheuristic: a timetable maps naturally to a chromosome, it can explore a large irregular search space, and repeated runs reveal reliability and solution diversity. Their different search strategies create a substantive BSCS comparison.

Success means the prototype can ingest an authorized semester dataset, produce or honestly fail to produce candidate schedules, independently verify every hard rule, compare both engines reproducibly, preserve reviewed assignments, and leave final approval with authorized USM personnel.

## 2. System architecture plan

Recommended and implemented stack:

| Layer | Technology | Reason |
|---|---|---|
| Frontend | Django templates, semantic HTML, custom CSS, vanilla JS | Fast to build and maintain; accessible; no separate SPA/API complexity |
| Backend | Python 3.12, Django 5.2, Django REST Framework | Mature data/auth/admin stack and direct access to optimization libraries |
| Database | PostgreSQL 18; SQLite local fallback | Transactions, constraints, JSON result data, reliable concurrent web/worker access |
| Optimization | OR-Tools CP-SAT and project-owned seeded GA | Exact constraint solver plus transparent comparison algorithm |
| Queue | Celery + Redis | Solvers run outside HTTP requests and expose durable lifecycle state |
| Delivery | Gunicorn, WhiteNoise, Docker Compose, GitHub Actions | Reproducible setup, separate web/worker scaling, automated verification |

Primary data flow:

`admin/import → validated term revision → approved objective policy → immutable problem snapshot → queued solver → independent validation/scoring → candidate schedule version → college review/locks → central approval → export/report`

The frontend never encodes scheduling rules. Application services own authorization and state transitions; the problem builder owns input normalization/candidate generation; solvers depend only on immutable domain contracts; validation and scoring are common components.

## 3. Database design

The relational model is documented field-by-field in [Data dictionary](data-dictionary.md). Its important design decisions are:

- `College → Department → Program`, with reusable `Subject` and curriculum-specific `ProgramSubject` classification/authoritative unit.
- `AcademicTerm → TermDatasetRevision`, so new freshmen, continuing/graduating sections, changing availability, and changed offerings never overwrite historical evidence.
- `CourseOffering` joins one subject/offering department to term sections and instructors; `MeetingRequirement` represents each lecture/lab occurrence and duration.
- `Room` is the single inventory; a lab is a room kind with optional `LaboratoryProfile`. Capabilities, ownership, and revision-specific authorization are separate.
- Availability uses explicit term profiles and atom rows; assuming full availability requires a named acknowledgement.
- Student storage is optional, pseudonymous, and linked to aggregate sections. The baseline solver does not use direct student identity.
- `ImportBatch/ImportError` stage data before an immutable committed revision.
- `ObjectiveProfile`, `ProblemSnapshot`, `ExperimentBatch`, `ScheduleRun`, `ValidationResult`, and `RunMetric` preserve the research chain.
- `ScheduleVersion/Assignment` plus atom allocation tables preserve timetables and enforce room/instructor/section uniqueness again at the database layer.
- `LockedAssignment`, `ScheduleReview`, `ScheduleApproval`, and append-only `AuditLog` support human control and traceability.

Historical schedules are `ScheduleVersion` rows. An imported historical/fixed timetable uses source `IMPORTED`; a fixed class selected for regeneration becomes an explicit active `LockedAssignment`. These concepts are not conflated.

## 4. Optimization engine design

### Common problem representation

The problem snapshot contains ordered time atoms, rooms, meeting events, legal candidate placements, lock map, and one objective profile. A candidate already satisfies local hard rules: correct revision/campus, contiguous duration, active/non-break and available atoms, room availability, required capability, unit authorization, and lock placement.

The independent validator handles output completeness and cross-event room/instructor/section/distinct-day conflicts. Both solvers use the same candidate IDs, resource IDs, objective definitions, and scorer.

### CP-SAT

- **Variable:** Boolean `x[event, candidate]`, true when the event selects that legal placement.
- **Event constraint:** exactly one candidate per meeting.
- **Resource constraints:** at most one selected candidate in every `(room, atom)`, `(instructor, atom)`, and `(section, atom)` bucket.
- **Institutional constraints:** candidate domains enforce authorization/capability/availability; distinct-day groups use at-most-one per group/day; locks force the required variable to one.
- **Objective:** minimize the shared integer weighted sum of preference penalty, section internal gaps, instructor internal gaps, and daily-load imbalance.
- **Evidence:** record first-feasible time, runtime, objective/bound/gap, branches/conflicts, worker count, seed, status, and stopping reason.

### Genetic Algorithm

- **Chromosome:** one integer gene per ordered meeting; the allele indexes that meeting’s legal candidates.
- **Population:** seeded randomized-greedy complete timetables; default 200.
- **Fitness:** lexicographic `(independent hard-violation count, shared weighted soft penalty)`, so any lower-violation candidate outranks a softer invalid candidate.
- **Selection:** tournament selection, default size three.
- **Crossover:** uniform crossover over offering-aware blocks to preserve related meeting structure.
- **Mutation:** choose a different legal candidate per gene, default rate `1/events`; locked genes never mutate.
- **Repair/elitism:** bounded conflict repair and top 5% preservation; record evaluated chromosomes, generations, and final fitness.
- **Claim limit:** GA can find a feasible schedule but cannot prove optimality or infeasibility.

Fair comparison uses the same snapshot/objective/deadline/CPU/seed set, a separate pilot for GA tuning, randomized within-block run order, and one independent validator/scorer. See [Experiment protocol](experiment-protocol.md).

## 5. System modules

### Administration and academic data

- manage accounts/roles/college scope, colleges, departments, programs, curricula, subjects, sections, instructors, unified rooms/labs/capabilities, and time slots;
- manage room and instructor availability plus explicit full-availability acknowledgements;
- configure room authorization by classification/unit and objective profiles; and
- view append-only audit history.

### Semester import

- download a versioned XLSX template;
- upload into staging, validate required references/types/duplicates/completeness, and show row-level errors;
- preview counts/assumptions and commit an immutable revision; and
- prevent duplicate workbook hash per term and source-data leakage into Git.

### Scheduling and optimization

- preflight/build/hash one problem snapshot;
- queue CP-SAT, GA, or controlled comparison batches;
- monitor/cancel runs and persist exact configuration/results; and
- compare only matching snapshots or experiment batches.

### Validation

- independently detect missing/duplicate/illegal placements, lock mismatch, room/instructor/section collisions, and distinct-day violations;
- re-score every feasible result with the common objective;
- explain preflight events with no legal candidates; and
- refuse promotion/approval of invalid schedules.

### Schedule management

- promote feasible runs to immutable-lineage versions;
- inspect/print timetable assignments;
- submit for college review, record endorsement/change request comments, and create child versions;
- lock selected accepted assignments before regeneration; and
- restrict final approval to central roles and independently feasible schedules.

### Reports

- run status/configuration/diagnostic detail;
- matched CP-SAT/GA feasibility, timing, quality, and stopping comparison;
- violation category and preflight infeasibility reports;
- room-time utilization and soft-component breakdown;
- de-identified experiment CSV/JSON/figures with hashes/seeds/build metadata; and
- approved timetable CSV/XLSX and snapshot manifest export.

## 6. Algorithm evaluation plan

The default controlled design uses one full authorized term instance plus nested 25/50/75% stratified scaling snapshots, seeds `1001–1030` for each algorithm, a 300-second deadline, one worker, and randomized sequential order within each seed block. All timeouts/no-solution results remain in the denominator; the common seed numbers are not treated as statistical pairs.

Metrics are precisely defined in [Experiment protocol](experiment-protocol.md):

- execution time and censor-aware restricted mean time to feasibility;
- independent hard-violation total/category;
- feasible-generation rate with 95% Wilson interval;
- shared raw penalty and normalized 0–100 quality among feasible schedules;
- attempts/retries in fixed seed order;
- pairwise normalized-Hamming consistency; and
- occupied/available usable room-time utilization, disclosed as mainly descriptive when fixed meeting demand makes it constant.

Analysis uses preregistered unpaired feasibility/time/quality comparisons, bootstrap intervals, effect sizes, Holm correction, full distributions, and a preregistered lexicographic primary-engine rule. Its fixed practical thresholds are a five-percentage-point feasibility-rate difference, a 5% median raw-penalty reduction, and a 10% RMST-to-feasibility reduction. Proven instance infeasibility is separated from search failure.

## 7. Development roadmap

Seven gated phases—requirements, data/basic system, shared model, CP-SAT, GA, controlled comparison, and final testing/documentation—are specified with deliverables and exit criteria in [Development roadmap](development-roadmap.md). The critical path is official rule/data authorization → shared immutable model/validator → both solvers → frozen experiment → UAT/defense.

## 8. Thesis defense preparation

The contribution is the formal USM constraint model, exact-versus-metaheuristic implementation, independent correctness oracle, and controlled evidence—not the administrative screens. [Thesis defense notes](thesis-defense.md) provide the argument, likely questions/answers, weaknesses/mitigations, demo sequence, evidence checklist, and claims to avoid.

Principal weaknesses to disclose are limited term coverage, source-data completeness, objective-weight subjectivity, GA tuning sensitivity, hardware effects, section-level aggregation, and the non-discriminating nature of aggregate room-time utilization. The design mitigates them with immutable provenance, staged validation, preregistration, pilot separation, controlled repeated runs, narrow claims, component reporting, and human review.

## 9. Recommended bounded improvements

Add only after the baseline hard rules and comparison are stable:

1. constraint/preflight explanation report for meetings with no legal candidates;
2. scenario copies for room/faculty what-if analysis;
3. locked partial regeneration from reviewed versions;
4. import completeness/data-quality dashboard;
5. reproducible de-identified result export and figures; and
6. ICS/PDF calendar output for an approved schedule.

Defer walking distance, seat capacity, real-time enrollment, automated negotiation, predictive AI/chatbot, and multi-campus travel. They change the research question or require data not established by the concept paper.
