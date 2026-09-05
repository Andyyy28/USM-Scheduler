# Schedule generation reliability fix

## Reproduced failure

The generated 14-meeting practice workbook reproduced a GA result failure through
`POST /api/v1/runs/`. The solver found a valid timetable, but its combinatorial
`search_space_size` exceeded the 18 integer digits supported by
`RunMetric.value` (`NUMERIC(24,6)`). SQLite accepted the oversized number and then
raised `decimal.InvalidOperation` when Django read the metric for the response.
The old conversion through `float()` could also overflow for larger integers.
Small two-meeting demo tests did not expose this defect.

The original screenshot's saved run and dataset were not present in this
checkout. The saved practice fixture solved with both original engines at a
30-second budget. Therefore the exact reported CP-SAT timeout was not reproduced;
the routine workflow now offers a search focused on producing a valid timetable.

## Resulting behavior

- Large numeric diagnostics have a null numeric value and an exact decimal text
  value in metric metadata. The original solver result and diagnostics retain
  their evidence. Migration 0008 repairs oversized historical SQLite metric rows
  without deleting the runs, timetables, or metric records.
- Routine generation defaults to **Find a valid timetable**. CP-SAT builds and
  solves the hard-constraint model without quality-optimization auxiliaries; GA
  stops after an independently verified feasible candidate. Both still validate
  and score the complete timetable under the original rules and objective policy.
- Early stopping reports FEASIBLE, never quality optimality. It is explicit in
  the configuration hash and excluded from research analysis. Research batches
  reject this option. Full-budget optimization remains the domain/API default
  when the option is omitted.
- Local eager solver failures return their saved run result. Worker dispatch
  failures return readable JSON. The browser rejects HTML/debug responses instead
  of printing them inside the form, and opens the result after submission.
- Result pages distinguish schedule success, time limits, conflicting rules,
  cancelled attempts, and system errors. Retry copies the checked data, method,
  and seed, and increases the timeout up to 3600 seconds. Missing candidates show
  “Not evaluated” rather than implying that zero violations were checked.

Implementation identifiers are `ga-v7` and `cp-sat-v5`. Historical reports refer
to their recorded source versions; this update does not replace their evidence.

## Verification and limits

The final non-browser suite passed **328 tests**, with 3 opt-in diagnostics
deselected. Ruff, Django checks, migration drift, JavaScript syntax, static asset
collection, and `git diff --check` passed. The test log is retained locally at
`experiment-results/run-recovery/final-tests.txt`.

The integration regressions cover actual practice-workbook import, both solvers,
independent result reconstruction, all 14 persisted assignments, readable list
and detail APIs, full-budget GA operation, simulated solver exceptions, a worker
outage, retry settings, large-integer conversion, and recovery of old SQLite
metrics. Solver tests retain conflict and deadline checks.

The in-app browser verified CP-SAT and GA generation, automatic result navigation,
and opening the 14-assignment timetable on a disposable synthetic database.
Both had zero independently validated hard violations. The timeout retry retained
the snapshot and seed (including seed 0) and increased 300 seconds to 600. The
390-pixel form had no horizontal overflow or clipped generation controls, and
the inspected browser log contained no warnings or errors.

The standalone Playwright test could not start Chromium on this Windows host
(`BrowserType.launch: spawn UNKNOWN`), before application assertions. Its new
HTML-error regression remains available for the browser-capable CI environment.
This live check does not establish full browser-suite, PostgreSQL, Redis/worker,
production, or authorized-semester acceptance.

A short equal-budget regression comparison used the previous GA-v6 source and
the final GA-v7 source, seed 5001, three synthetic scenarios, and 3 seconds per
observation. The report independently revalidated all six observations: both
versions found feasible schedules in all three cases and tied on penalties
(moderate mixed: 286; tight contention: 580; daily-load stress: 37). These small,
host-dependent observations are a regression check, not evidence of superiority
or a replacement for formal tuning. Source copies, hashes, inputs, witnesses,
raw observations, and the verified summary remain under
`experiment-results/run-recovery/final-comparison/` and `final-summary.json`.

The local SQLite database was backed up before migration 0008 was applied.
Existing installations need `python manage.py migrate` and a restart of the web
process and any Celery workers. A true shortage of available rooms or times can
still prevent a timetable; more search time does not guarantee feasibility.
