# Development and acceptance roadmap

The roadmap uses seven thesis phases. Each phase ends with evidence and an exit gate; unfinished hard-rule work cannot be hidden behind UI progress.

## Phase 1 — Planning and requirements analysis

**Activities**

- Interview the university scheduling office and designated college representatives.
- Obtain written authorization for the exact term data and define retention/de-identification.
- Create an authoritative rule catalogue for major/minor/GE classification, offering unit, room authorization, shared laboratories, availability, meeting duration/recurrence, breaks, and locks.
- Confirm the system boundary: section-level timetabling, no walking distance, no seat capacity, no automated final approval.
- Write acceptance examples for every hard rule and approve the experimental protocol.

**Deliverables:** signed requirements/rule matrix, data-sharing approval, sample de-identified workbook, scope statement, risk register, wireframes, preregistered metrics.

**Exit gate:** scheduling personnel can classify every sample assignment as legal/illegal using the documented rules; no unresolved high-impact policy interpretation remains.

## Phase 2 — Database and basic system

**Activities**

- Implement academic hierarchy, curricula, terms/revisions, sections, instructors, unified rooms/labs/capabilities, authorization, time atoms, availability, offerings, meetings, imports, locks, runs, versions, reviews, approvals, and audit events.
- Add role/college scope, model/database invariants, immutable committed artifacts, Django administration, authentication, and protected media handling.
- Build staged workbook import with stable row-level errors and completeness summaries.
- Establish PostgreSQL/Redis containers, CI, backups, and seed fixtures.

**Deliverables:** migrations, data dictionary, admin workflow, import template/validator, automated model tests, deployment scaffold.

**Exit gate:** an authorized user can create a term, stage/correct/commit a representative dataset, and retrieve the same hashed revision without manual database edits.

## Phase 3 — Shared scheduling model

**Activities**

- Convert a committed revision to an immutable, versioned problem contract.
- Enumerate legal candidate placements using duration, availability, room kind/capability, authorization, campus, breaks, and locks.
- Implement algorithm-independent validation and one shared soft scorer.
- Produce explainable diagnostics when a meeting has no legal placement.

**Deliverables:** problem builder, canonical hash, validator, scorer, synthetic fixtures for every constraint, property/integration tests.

**Exit gate:** both trivial valid and intentionally invalid schedules receive the expected deterministic validation; snapshot rebuilds from identical input have identical hashes.

## Phase 4 — CP-SAT implementation

**Activities**

- Build exactly-one placement variables, resource/time at-most-one constraints, distinct-day and lock constraints.
- Encode the common soft objective and record first feasible time, bounds, conflicts, branches, runtime, seed, and stopping reason.
- Revalidate/rescore every returned assignment and reject any solver/common-model disagreement.

**Deliverables:** CP-SAT solver, service/task integration, run persistence, unit/property/benchmark tests, infeasibility diagnostics.

**Exit gate:** all synthetic hard-rule cases pass; known infeasible fixtures are proven infeasible; known feasible fixtures return zero independent violations and matching objective totals.

## Phase 5 — Genetic Algorithm implementation

**Activities**

- Use one gene per meeting and legal-candidate allele domains.
- Implement seeded initialization, tournament selection, offering-aware crossover blocks, mutation, elitism, lock preservation, and bounded repair.
- Use lexicographic `(hard violations, common soft penalty)` fitness and prohibit unsupported proof claims.
- Select one configuration on separate pilot instances, then freeze it.

**Deliverables:** GA solver, reproducibility tests, operator invariant tests, pilot tuning report, run/task integration.

**Exit gate:** identical seed/problem/config reproduces the placement map; all genes remain legal and locked genes remain fixed; independent validator and scorer match recorded fitness.

## Phase 6 — Comparison and evaluation

**Activities**

- Freeze authorized full/scaling snapshots, objective profile, configurations, seeds, execution order, machine/build, and analysis scripts.
- Run all comparison-block trials sequentially; retain timeouts, failures, and no-solution results.
- Calculate Wilson intervals, RMST, preregistered unpaired tests, effect sizes, Holm corrections, consistency, retries, and descriptive utilization.
- Apply the preregistered primary-engine decision rule and sensitivity analysis.

**Deliverables:** immutable experiment batches, trial-level de-identified export, tables/figures, statistical notebook/script, interpretation and threats-to-validity section.

**Exit gate:** every table value traces to a persisted run and hashes/configurations prove fair pairing; independent rerun of the report produces the same output.

## Phase 7 — Final testing and documentation

**Activities**

- Conduct requirements-based tests, role/authorization tests, accessibility checks, performance checks, restore rehearsal, and scheduling-office user acceptance.
- Exercise import → snapshot → run → validate → compare → version → review → approve end to end.
- Document installation, operations, failure recovery, data retention, schema, architecture, experiment, and limitations.
- Rehearse a deterministic defense demonstration with backup screenshots/video and precomputed runs.

**Deliverables:** test/acceptance report, defect closure log, operations guide, final manuscript, defense deck/demo dataset, repository release tag.

**Exit gate:** no open critical/high defects; hard-constraint acceptance is 100%; approved schedules cannot be mutated; fresh setup passes CI and documented deployment; adviser accepts evidence package.

## Cross-phase quality gates

- Every institutional rule has a source/owner, model location, fixture, validator code, and user-facing diagnostic.
- Domain changes require migration, tests, and documentation update.
- Solver output is never labeled feasible without independent validation.
- Committed revisions/snapshots/configurations/results remain traceable by hashes and code version.
- Direct identifiers and workbooks never enter source control or screenshots.
- Accessibility target is WCAG 2.2 AA for the thesis workflow: keyboard operation, visible focus, semantic labels/tables, contrast, responsive reflow, and reduced-motion support.

## Suggested thesis schedule

| Work block | Recommended duration | Dependency |
|---|---:|---|
| Requirements/data authorization | 2–3 weeks | stakeholder availability |
| Data layer/import/admin | 3 weeks | approved sample records |
| Shared model/validator/scorer | 3 weeks | stable institutional rules |
| CP-SAT | 2–3 weeks | shared model complete |
| GA and pilot tuning | 3–4 weeks | shared model complete |
| Controlled experiments/analysis | 2–3 weeks | frozen implementations/data |
| UAT, manuscript, defense | 3 weeks | results complete |

Keep at least two calendar weeks of contingency for data correction and experiment reruns.

## Small-scope improvements after the baseline

These add thesis value without changing the central question:

1. **Constraint explanation report:** list meetings with zero candidates and the eliminating rules/resources.
2. **Scenario copies:** branch a committed term revision for “room unavailable” or “faculty changed” what-if analysis without altering history.
3. **Locked partial regeneration:** preserve reviewed assignments and reschedule only remaining meetings; locks remain common hard input to both solvers.
4. **Import quality dashboard:** completeness and duplicate/error trends by sheet/unit.
5. **Reproducible result export:** de-identified CSV/JSON and charts containing hashes, seeds, timings, statuses, violations, and quality components.
6. **Calendar export:** ICS/PDF output only after an approved schedule exists.

Defer walking-distance optimization, capacity/seat optimization, real-time student registration, automated faculty negotiation, predictive AI, and multi-campus travel until after the thesis baseline.
