# A Comparative Evaluation of CP-SAT and Genetic Algorithm for University Timetabling and College-Boundary-Aware Room Assignment at the University of Southern Mindanao

**Prepared by:** Ruby Jean B. Solomon and Edgardo Gabriel L. Paclibar

**Program:** Bachelor of Science in Computer Science

**Study setting:** University of Southern Mindanao, Kabacan Main Campus
**Document type:** Concept-paper working draft

> **Research and institutional disclaimer.** This is a BS Computer Science thesis prototype and research proposal, not an official USM scheduling policy or deployed university system. The scheduling rules described as “USM requirements” originate from the project brief and must be validated and signed off by authorized USM scheduling personnel. Public USM sources are used only for institutional context. No real institutional dataset may be collected, imported, analyzed, or published without the required university authorization, privacy review, and de-identification controls.

## Abstract

University class timetabling requires many interdependent decisions involving active sections, fixed instructors, meeting durations, rooms, laboratories, time periods, availability, and academic-unit rules. A locally acceptable placement can make another meeting impossible because the same instructor, section, or room cannot be used twice at the same time. This study proposes a university scheduling decision-support system for one authorized semester at the University of Southern Mindanao (USM) Kabacan Main Campus. It formalizes stakeholder-provided rules, including explicit college- or offering-unit room authorization, and compares two contrasting optimization approaches: Constraint Programming–SAT (CP-SAT) and a custom Genetic Algorithm (GA).

Both engines will receive the same immutable problem snapshot, legal candidate placements, hard constraints, soft-objective profile, computational budget, and independent validation procedure. Evaluation will use three prespecified primary outcomes: independently verified feasible-generation rate, common raw schedule-quality penalty among feasible results, and censor-aware time to feasibility. The system will preserve human control through college review, locked assignments, child-version regeneration, and central approval. The main contribution is the formal USM constraint model, exact-versus-metaheuristic comparison, algorithm-independent correctness checking, and reproducible experiment—not the administrative data-entry interface. Conclusions will be limited to the authorized campus, term, instances, objective weights, implementation, hardware, and time budget actually tested.

## Statement of the Problem

The University of Southern Mindanao describes its main campus as being located in Kabacan, Cotabato, and its public mission includes instruction and research in support of Southern Philippines ([USM About](https://www.usm.edu.ph/about-usm/); [USM location](https://www.usm.edu.ph/about-usm/location-map/); [USM mandates, vision, and mission](https://www.usm.edu.ph/about-usm/mandates-vision-mission/)). Those public facts establish institutional context but do not establish the university’s internal scheduling rules.

For this study, the operational problem is defined by the stakeholder-supplied thesis requirements. The input consists of predeclared course offerings, active sections, fixed instructor assignments and teaching loads, required meeting occurrences and durations, time atoms, rooms and laboratories, availability profiles, room capabilities, curriculum-specific subject classifications, offering-unit policies, and existing assignments that have been explicitly locked. The optimizer decides only the time and room for each required meeting.

The decisions are combinatorial. Every meeting can have multiple individually legal room-time placements, but choosing one consumes a room, section, and one or more instructors across all occupied time atoms. As the number of meetings and candidates increases, locally chosen placements interact globally. A complete schedule can therefore appear acceptable while containing instructor, section, room, availability, duration, laboratory, or authorization conflicts.

The project brief identifies a USM-specific policy requirement that major offerings must use rooms or laboratories explicitly authorized for their college or academic unit, while minor and general-education offerings must follow the applicable offering-unit rules. This requirement will be modeled as configurable authorization data, not assumed from public sources and not inferred from room ownership. Shared or borrowed facilities require explicit authorization. The completed room-policy matrix remains a formal data and thesis gate.

Constraint programming is suited to large discrete feasibility spaces with interacting constraints. Google’s documentation presents CP-SAT as an integer-based constraint solver and distinguishes `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, and `UNKNOWN` outcomes ([Google OR-Tools constraint optimization](https://developers.google.com/optimization/cp); [CP-SAT solver documentation](https://developers.google.com/optimization/cp/cp_solver)). Genetic algorithms provide a contrasting population-based search approach and have been applied to university course timetabling, including work by Yu and Sung ([DOI: 10.1111/1475-3995.00383](https://doi.org/10.1111/1475-3995.00383)). The International Timetabling Competition also demonstrates the importance of shared instances and controlled benchmarking when comparing methods ([ITC 2007](https://www.unitime.org/itc2007/); [curriculum-based course timetabling technical report](https://www.eeecs.qub.ac.uk/itc2007/curriculmcourse/report/curriculumtechreport.pdf)).

The research problem is therefore not merely how to store or display class records. It is how to formalize the defined USM rules, produce independently valid schedules, compare fundamentally different search strategies fairly, and preserve accountable human review.

## Research Questions

1. How can a university timetabling model represent the authorized USM semester data and stakeholder-validated constraints involving sections, fixed instructors, meeting duration and recurrence, time atoms, room and instructor availability, specialized-room capabilities, locks, and college- or offering-unit room authorization?
2. How do CP-SAT and the Genetic Algorithm compare in terms of independently validated hard-constraint satisfaction, feasible-schedule generation rate, common schedule-quality penalty, time to first feasible schedule, execution time, and consistency across repeated runs?
3. Which algorithm, if either, demonstrates a practically and statistically meaningful advantage under the same frozen USM problem instance, objective profile, hardware allocation, random-seed set, and wall-clock budget?
4. Can the prototype functionally support semester change by cloning a term, modifying sections, offerings, instructor availability, and room availability, and regenerating a schedule without weakening the defined hard constraints or human approval workflow?

The fourth question evaluates functional adaptability. With one real term, the study will not claim longitudinal reliability across future semesters.

## Objectives of the Study

### General objective

To design, implement, and evaluate a college-boundary-aware university scheduling decision-support system that compares CP-SAT and a Genetic Algorithm using a common, independently validated USM timetabling model.

### Specific objectives

1. Formalize stakeholder-approved hard and soft scheduling rules in a versioned data and constraint model.
2. Build immutable semester revisions, legal candidate placement generation, and reproducible problem snapshots.
3. Implement CP-SAT and GA behind the same solver input/output contract.
4. Develop an algorithm-independent validator and common objective scorer.
5. Generate candidate schedules that either contain zero independently detected hard violations or honestly report that no feasible result was found.
6. Compare both engines using prespecified feasibility, quality, timing, reliability, and descriptive room-time metrics.
7. Support scoped college review, central approval, locks, regeneration, version history, and approved-schedule export.
8. Demonstrate semester adaptability using a cloned term with controlled input changes.
9. Produce a reproducible experiment report, user guide, technical documentation, and de-identified demonstration dataset.

## Significance of the Study

**University scheduling personnel.** The system may reduce repeated manual conflict checking, make room-policy assumptions explicit, and provide traceable alternatives without removing human authority.

**College and department reviewers.** Scoped review, comments, endorsements, locks, and changed-assignment views can make unit validation more systematic.

**Faculty and sections.** Correct representation of fixed instructor assignments, availability, and section conflicts can reduce avoidable timetable collisions when the input data are complete and accurate.

**USM administration.** Versioned inputs, audit records, independent validation, and reproducibility manifests can support accountable evaluation of a scheduling prototype.

**Computer Science.** The study contributes a formal institution-specific constraint model, two distinct search implementations, an independent correctness oracle, and a controlled empirical comparison. These elements distinguish the work from an ordinary CRUD scheduling application.

**Future researchers.** The problem contract, synthetic fixtures, metric definitions, and disclosed limitations may support later work on additional terms or carefully bounded extensions.

The study does not claim that the prototype replaces the scheduling office, guarantees a feasible schedule for every input, or establishes universal superiority of one algorithm.

## Scope and Delimitations

The baseline study covers:

- one USM campus: Kabacan Main Campus;
- one complete authorized, de-identified real semester, subject to institutional permission and data availability;
- deterministic 25%, 50%, 75%, and 100% nested demand instances derived from that term;
- active incoming, continuing, and graduating sections represented for the selected term;
- fixed subjects, instructors, teaching loads, meeting occurrences, and durations;
- section-level conflicts, including shared offerings attached to every affected section;
- expected enrollment from 1 through 50 for every section and a fixed maximum of 50 students across the unique sections attached to any meeting;
- configurable 30-minute time atoms;
- classrooms, laboratories, room capabilities, explicit room authorization, availability, and locks;
- CP-SAT and one transparent custom GA; and
- administrative review, timetable versioning, approval, and export.

The study excludes:

- instructor assignment and teaching-load construction;
- variable room seating capacity, chair counting, and floor-space optimization;
- individual student elective and cross-enrollment conflict optimization;
- examination timetabling;
- campus walking distance and multi-campus travel;
- real-time enrollment;
- automated policy relaxation;
- a hybrid CP-SAT/GA algorithm; and
- an AI chatbot as the scheduling solution.

Student records are optional, pseudonymous administrative data and do not enter
the solver or scorer. The frozen section headcount is sufficient. The fixed
50-student limit applies uniformly to classrooms, laboratories, and
special-purpose rooms; exactly 50 is valid and 51 or more blocks snapshot
creation. It is an academic scheduling rule, not a variable physical-capacity
model. Participating rooms must be administratively prevalidated as suitable.

If USM authorization, a complete term dataset, or approved room-policy definitions are unavailable, the output must be reframed as a synthetic-data prototype evaluation. It must not be presented as evidence of real USM operational performance.

## Conceptual Framework

```text
INPUT
Authorized term revision
Sections, offerings, fixed instructors, meetings, rooms, time atoms,
availability, capabilities, authorizations, locks, objective profile
        ↓
PROCESS A — COMMON MODEL
Validation → event expansion → legal candidate generation → immutable hash
        ↓
PROCESS B — PARALLEL METHODS
CP-SAT search                         Genetic Algorithm search
        \                              /
         \                            /
          ↓                          ↓
PROCESS C — COMMON CORRECTNESS LAYER
Independent hard-constraint validator + common soft-objective scorer
        ↓
OUTPUT AND EVALUATION
Feasible candidate schedules, statuses, quality components, timing,
success rate, consistency, statistical comparison, and reproducibility data
        ↓
HUMAN GOVERNANCE
College review → locks/change requests → regeneration → central approval
```

The central fairness principle is that both algorithms receive the same frozen problem and neither algorithm validates itself.

## Proposed System

The prototype uses a modular-monolith architecture:

- a responsive server-rendered Django interface for scheduling personnel;
- Django authentication, role-based permissions, services, REST endpoints, and database migrations;
- PostgreSQL as the institutional data store;
- Redis and a one-concurrency Celery worker for long solver jobs;
- OR-Tools CP-SAT and a project-owned seeded GA;
- an independent validation and scoring package; and
- Docker Compose for reproducible local deployment.

The main workflow is:

`validated XLSX → committed term revision → approved objective profile → immutable problem snapshot → solver run → independent validation/scoring → timetable version → college review → central approval → export`

Committed revisions, approved objective profiles, problem snapshots, approved schedules, and audit records are immutable. Corrections create a new revision or child schedule.

## Methodology

### Research design

The study will use design-and-development research combined with a controlled computational experiment. Software correctness testing, functional workflow evaluation, and the CP-SAT/GA experiment answer different questions and will be reported separately.

### Research setting and data

The primary case will be one authorized semester from USM Kabacan Main Campus. The dataset should include all participating colleges within the approved study coverage. The final manuscript will disclose the actual term, colleges represented, counts of offerings, meetings, sections, instructors, rooms by kind, time atoms, candidate placements, and locks.

The source data will be previewed through a versioned XLSX schema. Invalid rows, missing references, duplicates, incomplete availability, invalid duration patterns, unauthorized room rules, and conflicting locks will block transactional commit. A clean revision will be serialized and hashed before solving.

Synthetic data will be used for development, constraint tests, infeasibility tests, stress cases, and equal-budget CP-SAT/GA pilot tuning. Synthetic results will not be mixed with claims about actual USM performance.

### Common problem representation

Each weekly `MeetingRequirement` becomes one `MeetingEvent`. Preprocessing produces the same legal placement list for both engines:

```text
candidate[event] = (room, start_atom, occupied_atoms)
```

Candidates that violate a local hard rule are removed before search. Candidate legality covers campus, active atoms, contiguous duration, day and break boundaries, room and instructor availability, required room capability, room authorization, and active lock placement. An event with no candidate is a shared preflight failure, not a loss assigned to either algorithm.

### Hard constraints

Every returned schedule must satisfy all of the following:

1. Every required meeting is assigned exactly once.
2. An instructor cannot teach overlapping meetings.
3. A section cannot attend overlapping meetings.
4. A room cannot contain overlapping meetings.
5. Instructor and room unavailability is respected.
6. A multi-atom meeting is contiguous and cannot cross a break or day boundary.
7. Laboratory or specialized meetings use rooms with all required capabilities.
8. Major offerings use rooms explicitly authorized for the applicable college or department.
9. Minor and GE offerings follow the approved offering-unit authorization rules.
10. Repeated sessions in a distinct-day group use different days.
11. Every active locked assignment is preserved exactly.
12. Every section and every combined meeting has at most 50 students.
13. Approved recurring teaching blocks are respected at their declared scope.
14. Every instructor remains within the approved maximum daily teaching load, or has an approved explicit no-limit acknowledgement.

An independent validator will recheck both common candidate membership and the raw evidence behind availability, duration, capabilities, room kind, authorizations, locks, and resource conflicts.

### Soft objectives

Feasibility always takes priority. The default shared soft components are:

- instructor preferred or avoided time atoms, when authorized preference data exist;
- internal vacant atoms within each section-day;
- internal vacant atoms within each instructor-day; and
- daily-load imbalance relative to computed weekly-load targets.

One approved `ObjectiveProfile` stores nonnegative integer weights, definitions, normalizers, and a SHA-256 hash. Missing faculty-preference data sets that component’s approved weight to zero for both algorithms. The primary quality value is the lower-is-better raw weighted penalty with its component breakdown. A normalized 0–100 score is secondary and will not be reported alone.

### CP-SAT model

For meeting `e` and legal placement `p`, CP-SAT creates a Boolean decision variable:

```text
x[e,p] = 1 if and only if event e uses placement p
```

Exactly one placement is selected per event. At-most-one constraints protect every room, instructor, and section time atom. Distinct-day groups and active locks become hard constraints. The model minimizes the same integer soft penalty used by the independent scorer.

Every run records the status, stopping reason, first-feasible time, total solver time, objective, best bound, relative gap, branches, conflicts, seed, and worker count. `FEASIBLE` will not be called optimal, `UNKNOWN` will not be called infeasible, and only a CP-SAT `INFEASIBLE` result may constitute a proof for the encoded frozen instance.

### Genetic Algorithm model

The GA uses one gene per ordered meeting and one legal candidate index as its allele. Its main components are:

- seeded randomized-greedy population initialization;
- tournament selection;
- uniform, offering-aware crossover;
- candidate-replacement mutation;
- elite preservation;
- bounded conflict repair; and
- time-limit termination.

Fitness is lexicographic:

```text
(independent hard-violation count, common soft penalty)
```

Any candidate with fewer hard violations outranks one with a better soft score. No arbitrary large hard-penalty constant is used. The GA may find a feasible schedule but cannot prove optimality or infeasibility.

Before real-data testing, both engines receive the same 30-minute maximum
synthetic tuning budget: six configurations × seeds `2001–2005` × 60 seconds.
The CP-SAT grid is presolve on/off × linearization level `0/1/2`. The GA grid is
population 100/200/400 × mutation `1/N` or `2/N`, while tournament size 3,
crossover 0.90, elite fraction 0.05, and 20 repair attempts remain fixed.
Selection priority is feasibility rate, median feasible raw penalty, RMST to
feasibility, then configuration hash. Both profiles are frozen before authorized
term outcomes are inspected, and objective weights are never tuned to favor an
engine. All 30 runs per algorithm (60 total) in this pilot are exploratory and
excluded from formal denominators and inference.

### Controlled experiment

The primary full instance will contain all active authorized offerings in the selected term. Nested 25%, 50%, and 75% instances will be selected deterministically within college/classification strata, with the 100% instance retained as the primary case. Referenced instructors, sections, authorizations, capabilities, availability, and locks will remain consistent.

For each frozen scale instance:

- run one unmeasured warm-up per engine;
- run CP-SAT and GA with seeds `1001–1030`;
- use a 300-second wall-clock limit per trial;
- allocate one CPU, one solver worker, and 2048 MB of memory per fresh-process run;
- execute algorithms sequentially, never concurrently;
- randomize algorithm order within seed blocks using order seed `20260824`;
- measure shared preprocessing separately;
- include algorithm-specific construction in the 300-second budget; and
- preserve all timeouts, no-solution outcomes, and failures.

The formal matrix contains 240 measured runs across the 25%, 50%, 75%, and 100%
instances, plus eight persisted but excluded warm-ups, four excluded
1,800-second CP-SAT feasibility diagnostics, and eight excluded trace runs using
seed `9001`. All other configurable, synthetic, pilot, and diagnostic batches are
permanently labelled exploratory and cannot produce the thesis winner.

The preregistered comparison preserves each scale-and-seed CP-SAT/GA block as a
deterministic pair. This blocking controls the frozen problem instance and run
order; it does not assume that the two algorithms transform a numeric seed into
equivalent random behavior.

### Measures

| Measure | Operational definition |
|---|---|
| Hard violations | Independent vector by type; a collision counts one unique conflicting event pair per resource |
| Feasible generation | Complete assignment with zero hard violations |
| Feasible-generation rate | Feasible measured trials divided by all eligible trials, with counts and 95% Wilson interval |
| Time to feasibility | Monotonic time to the first independently valid incumbent; unsuccessful trials censored at 300 seconds |
| Execution time | Monotonic solver-boundary time, excluding queue/UI delay and shared preprocessing |
| Schedule quality | Common raw weighted penalty, per-meeting penalty, component breakdown, and secondary normalized score |
| Consistency | Pairwise normalized placement Hamming distance plus quality/time dispersion |
| Room-time utilization | Occupied usable room-time atoms divided by available usable room-time atoms; descriptive and unrelated to chairs, floor area, or physical capacity |
| CP-SAT certificate | Best bound, gap, and whether feasibility, optimality, or infeasibility was proven |

### Statistical analysis and decision rule

Results will report exact sample sizes, medians, interquartile ranges, median
absolute deviations, Wilson intervals, deterministic bootstrap confidence
intervals, Vargha–Delaney effect sizes for feasible quality, and Holm correction
across the three primary outcomes within each scale. The preregistered
feasibility comparison uses a two-sided exact paired binary test across complete
seed blocks. Time to feasibility uses a censor-aware restricted mean through
300 seconds, a deterministic within-seed swap permutation, and a paired
bootstrap interval for the RMST difference. Because schedule quality is defined
only among independently feasible outputs, its comparison uses the declared
deterministic two-sided label-permutation test of medians rather than discarding
unmatched feasible schedules.

A preferred engine may be declared only for the complete, protocol-valid 100%
instance. The decision order is:

1. independently validated feasibility and feasible-generation rate;
2. common feasible-schedule quality; and
3. censor-aware time to feasibility.

The practical thresholds are a five-percentage-point feasibility difference, a 5% median raw-penalty improvement, and a 10% reduction in restricted mean time to feasibility, subject to the prespecified statistical conditions. If neither engine meets the rule, the conclusion will state that no overall advantage was demonstrated and will report the trade-offs rather than force a winner.

Consistency and operational behavior remain secondary descriptive evidence.
Incomplete, exploratory, invalid, cancelled, or unclassified studies must show
“No formal conclusion available.”

### Software and user validation

The implementation will undergo:

- positive and negative unit fixtures for every hard rule;
- property-based conflict tests;
- known-feasible and known-infeasible solver cases;
- equality checks between solver objective encoding and independent rescoring;
- import, permission, state-transition, and immutability tests;
- full import-to-export workflow tests;
- browser-based critical-path tests; and
- task-based usability evaluation with authorized scheduling personnel, subject to approval.

The semester-adaptability demonstration will clone one term, modify sections, subjects, instructor availability, and room availability, then rebuild the snapshot and regenerate a child timetable.

## Ethics, Privacy, and Data Governance

USM maintains a University Data Protection Office and publishes notices concerning compliance with the Philippine Data Privacy Act and protection of student information ([USM University Data Protection Office](https://www.usm.edu.ph/administration/university-data-protection-office/); [USM student privacy notice](https://www.usm.edu.ph/privacy-notice-for-students-alumni-and-prospective-students/)). The study will therefore treat authorization and data minimization as research gates rather than post-development tasks.

The study will:

- obtain written university and adviser-approved data authority before real-data use;
- collect only fields necessary for the defined scheduling problem;
- exclude individual students from the solver and use pseudonymous codes only if membership records are necessary;
- de-identify instructor identity in shared research extracts;
- store source workbooks, database dumps, backups, and research outputs outside the public repository;
- restrict access by role and college scope;
- avoid logging workbook content, passwords, tokens, and direct identifiers;
- retain or destroy data according to an approved policy; and
- publish only institution-approved, de-identified aggregate artifacts.

Hashes establish integrity and reproducibility but do not anonymize personal data.

## Expected Outputs

1. A functional, role-based university scheduling decision-support prototype.
2. A formal college-boundary-aware constraint and room-authorization model.
3. CP-SAT and custom GA implementations using one immutable problem contract.
4. An independent validator and shared objective scorer.
5. Versioned term imports, snapshots, runs, timetable review, locks, approval, and export.
6. A controlled comparison report with complete trial-level provenance and statistical summaries.
7. A synthetic, de-identified demonstration dataset and acceptance tests.
8. Architecture, data-dictionary, experiment, administrator, user, and defense documentation.
9. A conclusion that identifies an engine only when supported by the prespecified evidence, or reports a conditional trade-off/no demonstrated winner.

## Development Plan

| Phase | Principal work | Exit evidence |
|---|---|---|
| 1. Requirements | Constraint catalogue, authorization matrix, privacy plan, objectives | Stakeholder/adviser sign-off |
| 2. Data and platform | Database, roles, imports, audit, deployment | Sample term imported transactionally |
| 3. Shared model | Candidate builder, snapshots, validator, scorer, locks | Every hard rule has positive/negative tests |
| 4. CP-SAT | Variables, constraints, objective, statuses, diagnostics | Known cases independently validated |
| 5. GA | Operators, repair, reproducibility, synthetic tuning | Frozen configuration and repeatable seeds |
| 6. Evaluation | Controlled batches, statistics, sensitivity, reports | Every comparison traces to matching hashes |
| 7. Final validation | UAT, accessibility, recovery, documentation, defense artifacts | Tested release and approved non-sensitive evidence |

## Limitations and Threats to Validity

- **One-term external validity:** findings are a USM one-term case study, not a universal algorithm ranking.
- **Institutional-data quality:** missing or incorrect authorization and availability can make the encoded model misleading.
- **Objective subjectivity:** soft weights are normative and require preapproval plus sensitivity analysis.
- **GA configuration sensitivity:** tuning uses separate synthetic data and frozen settings.
- **Different guarantees:** CP-SAT may prove optimality or infeasibility; GA cannot. The report must preserve this distinction.
- **Hardware and software effects:** results apply to the recorded build, machine, worker count, and deadline.
- **Section aggregation:** individual elective conflicts are not represented.
- **Physical-capacity exclusion:** the fixed 50-student rule is not chair, floor-area, or variable room-capacity validation; room-time utilization is not seat utilization or proof of physical suitability.
- **Prototype governance:** the interface and logo do not imply official adoption.

## References and Official Context Sources

Google. (n.d.). *Constraint optimization*. [https://developers.google.com/optimization/cp](https://developers.google.com/optimization/cp)

Google. (n.d.). *CP-SAT solver*. [https://developers.google.com/optimization/cp/cp_solver](https://developers.google.com/optimization/cp/cp_solver)

International Timetabling Competition. (2007). *ITC 2007*. [https://www.unitime.org/itc2007/](https://www.unitime.org/itc2007/)

University of Southern Mindanao. (n.d.). *About USM*. [https://www.usm.edu.ph/about-usm/](https://www.usm.edu.ph/about-usm/)

University of Southern Mindanao. (n.d.). *Location map*. [https://www.usm.edu.ph/about-usm/location-map/](https://www.usm.edu.ph/about-usm/location-map/)

University of Southern Mindanao. (n.d.). *Mandates, vision and mission*. [https://www.usm.edu.ph/about-usm/mandates-vision-mission/](https://www.usm.edu.ph/about-usm/mandates-vision-mission/)

University of Southern Mindanao. (n.d.). *University Data Protection Office*. [https://www.usm.edu.ph/administration/university-data-protection-office/](https://www.usm.edu.ph/administration/university-data-protection-office/)

University of Southern Mindanao. (n.d.). *Privacy notice for students, alumni and prospective students*. [https://www.usm.edu.ph/privacy-notice-for-students-alumni-and-prospective-students/](https://www.usm.edu.ph/privacy-notice-for-students-alumni-and-prospective-students/)

Yu, E., & Sung, K.-S. (2002). A genetic algorithm for a university weekly courses timetabling problem. *International Transactions in Operational Research, 9*(6), 703–717. [https://doi.org/10.1111/1475-3995.00383](https://doi.org/10.1111/1475-3995.00383)

> Public web sources were checked on August 24, 2026. The final manuscript should use the citation style prescribed by the program and add the approved institutional/interview sources that validate actual scheduling practice and room policy.
