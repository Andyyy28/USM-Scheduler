# USM Scheduler thesis validation report

## Release candidate

- Branch: `thesis/experimental-platform-v2`
- UI baseline: `f115a010ae2d64f2d31706bf26144788ba1b29bf`
- Working checkout base: `c11afa3fa481e980909fd51a9c2e1dad56fc5e37`; the implementation changes are uncommitted and have not been pushed.
- Final QA commit: pending; record the final full hash and CI URL in the signed thesis evidence copy.
- Latest local verification date: 30 August 2026
- Scope: thesis-defense readiness for the staff workflow and CP-SAT/GA experimental platform, not institutional production deployment.
- Data: deterministic synthetic/de-identified workbooks only.

## Environment

Local validation uses Windows, Python 3.12.2, Django's live test server, Playwright 1.62.0, bundled Chromium, and Playwright Firefox. GitHub Actions repeats the suite on Linux with PostgreSQL 18 and Redis 7.4, production security checks, migrations, static collection, coverage, and a container build.

## Current experimental-platform verification — 30 August 2026

This is implementation verification on Windows with the repository Python 3.12
environment and local SQLite. It is not a formal experiment, deployment, or
evidence that either algorithm performs better for an authorized USM term.

| Check | Current evidence | Result |
|---|---|---|
| Non-browser regression suite after GA-v5 | `pytest -q --ignore=tests/e2e` | **295 passed, 3 diagnostic tests deselected; 123.63 seconds** |
| Both engines on the schema 1.1 synthetic trial workbook | `pytest -q -m diagnostic tests/integration/test_trial_data.py::test_trial_workbook_performance_exercise_is_feasible_for_both_engines` | Passed; 68.48 seconds for the software test, not a comparative benchmark |
| Lint and patch whitespace | `ruff check .`; `git diff --check` | Passed; Git reported line-ending normalization notices only |
| Django and migration drift | `manage.py check`; `manage.py makemigrations --check --dry-run` | Passed; no changes detected |
| Dependency consistency and vulnerability audit | `pip check`; `pip_audit -r requirements-lock.txt --no-deps --disable-pip` | Passed; no broken requirements or known vulnerabilities |
| Hash-enforced Linux dependency resolution | Binary-only CPython 3.12 manylinux installation dry run against `requirements-hashed.txt` | Passed; all 37 exact runtime packages verified, no installation performed |
| Static assets | Supported Django `STORAGES` configuration and `collectstatic --noinput` | Passed; production HTML uses fingerprinted CSS/JS URLs |
| Targeted rendered reflow | In-app browser, Prepare Data and draft Formal Study at 1440, 768, 390, 320 pixels | Passed; document scroll width equals client width, including scrollbar space; no captured warning/error logs |
| Formal conclusion disclosure | Rendered draft study | Correctly shows “No formal conclusion available” |
| Full Playwright/browser/axe suite | Local Chromium launch | Blocked by host browser-launch failure before application assertions; not passed |
| PostgreSQL/Redis/Celery Compose acceptance | Docker executable unavailable on this host | Not run; mocked runtime tests do not prove real worker-loss recovery, cgroup limits, or fresh-process execution |
| Word manuscript/guide | Regenerated deterministic DOCX files; OOXML reopened successfully | Structural check only for this pass; current render attempt could not run because LibreOffice/soffice is unavailable |
| Final-commit CI and container build | No commit, push, or remote workflow dispatch in this pass | Pending |

This pass adds persisted-pilot authentication, terminal worker provenance checks,
snapshot consistency checks, per-scale GA mutation-formula resolution, and
deadline-qualified incumbents. Both solvers now reject improvements whose shared
validation/scoring finishes after the deadline. Only excluded diagnostic runs
record bounded convergence traces. The evidence bundle contains numeric traces,
printable figures, objective outcomes, resource-period metrics, and checksums.

The subsequent `ga-v4` algorithm continuation adds daily-limit-aware construction,
hard-first bounded repair, and interrupted-initialization safeguards. Its
[synthetic tuning report](algorithm-tuning-2026-08-30.md) records the matched
development measurements and the remaining fully occupied-grid failures. Those
shortened runs do not replace the approved equal-budget pilot or formal study.

The subsequent `ga-v5` continuation adds prepared lookup contexts, independent
incumbent rechecks, a 128-request repair cap, and a 64-trial feasible improvement
pass after completed generations. Its [development report](algorithm-tuning-ga-v5.md)
records all 80 matched synthetic observations and all 11 unsuccessful searches.
At 30 seconds, GA-v5 solved 10/10 dense observations versus GA-v4's 2/10; both
kept all 15 easier observations feasible. Two mixed-case seeds had worse
penalties under GA-v5, which the report retains. These local measurements do not
establish a formal comparative conclusion, and CP-SAT source and the formal
equal-budget grid are unchanged by this continuation.

The new enrollment/policy-aware practice workbook never approves institutional
policies automatically. Historical snapshots/results and the legacy practice
workbook remain preserved. All automated pilot/study fixtures are synthetic
software tests, not research observations.

Still required: the approved equal-budget synthetic pilot on the final benchmark
build, one authorized de-identified term, all formal trials, actual container
acceptance, 200% zoom, manual keyboard/screen-reader/reduced-motion/print checks,
updated axe scans, current Word layout review, and approved 3–5 usability sessions.

## Historical UI-baseline automated validation

The results below describe the previously recorded UI release-candidate baseline.
They are retained as regression evidence, but they do not automatically validate
the experimental-platform branch. Every changed check must be rerun and the
final commit and CI URL added before thesis acceptance. A result is never marked
passed until its command exits successfully.

| Check | Command/evidence | Result |
|---|---|---|
| Clean dependency consistency | Isolated Python environment, `python -m pip check` | Passed; no broken requirements |
| Locked dependency vulnerability audit | `pip-audit==2.10.1`; `python -m pip_audit --requirement requirements-lock.txt --no-deps --disable-pip` | Passed locally on Python 3.12.14 after updating `sqlparse` to 0.6.0; no known vulnerabilities found. Repeat in final CI. |
| Python lint | `ruff check .` | Passed |
| Django configuration | `python manage.py check` | Passed; no issues |
| Production security configuration | `python manage.py check --deploy --fail-level WARNING` with production environment values | Passed; no issues |
| Migration drift | `python manage.py makemigrations --check --dry-run` | Passed; no changes detected |
| Static collection | `python manage.py collectstatic --noinput --dry-run` | Passed |
| Complete pytest suite | `pytest --cov=scheduler --cov-report=term-missing --cov-fail-under=70` | **142 passed, 3 diagnostic tests deselected, 87.41% coverage; 4m43s** |
| Chromium browser suite | Included in complete pytest suite | Passed |
| Firefox workflow smoke | `test_firefox_login_navigation_and_principal_workflow_smoke` | Passed |
| WCAG A/AA automation | `test_critical_pages_have_no_automated_wcag_a_or_aa_violations` | Passed; zero detected violations on scanned pages |
| Linux/PostgreSQL/Redis CI | `CI` workflow for the final thesis branch commit | Pending final-branch dispatch |
| Container build | `container` job for the final thesis branch commit | Pending final-branch dispatch |

The CI coverage threshold is 70%. Coverage is a regression guard, not a substitute for the permission, workflow, and state-transition assertions below.

## Formal experiment acceptance

The following evidence is required before the system may state a comparative
conclusion. Empty cells are intentional and must be completed only from an
authorized, protocol-valid execution.

| Gate | Required evidence | Current result |
|---|---|---|
| Study classification | Formal study identifier; exploratory batches visibly excluded | Implemented and locally tested; authorized formal study still pending |
| Constraint provenance | Approved constraint-policy manifest, objective profile, and hashes | Pending institutional approval |
| Fixed student limit | Every section and combined meeting has a frozen headcount of 1–50; no variable room/chair/space model | Pending authorized dataset |
| Scaling | Nested 25%, 50%, 75%, and 100% snapshot hashes with retained-lock disclosures | Pending final study |
| Equal tuning budget | Six CP-SAT and six GA profiles over seeds 2001–2005 at 60 seconds, synthetic only | Pending pilot execution |
| Formal matrix | 240 measured runs, 8 excluded warm-ups, 4 excluded feasibility diagnostics, and 8 excluded trace runs | Pending final study |
| Execution controls | One CPU, 2 GB, fresh child process, sequential randomized order, worker-side manifest | Pending benchmark-host verification |
| Primary analysis | Feasibility, feasible raw penalty, and censor-aware time to feasibility with Holm correction | Pending final study |
| Protocol integrity | No missing, unclassified, stale, or provenance-mismatched trial; audited replacement pairs retained | Pending final study |
| Evidence bundle | Deterministic de-identified ZIP, checksums, trial data, figures, definitions, and claim boundaries | Pending final study |

Until every formal gate passes, the research interface and manuscript must say
**No formal conclusion available**. Synthetic or exploratory results may validate
software behavior but cannot establish which algorithm performs better for USM.

## Risk-based browser matrix

The browser suite verifies:

- Central scheduler, college reviewer, and system administrator navigation, account controls, role-specific actions, read-only pages, and direct API denial for reviewer-only access.
- Principal routes at 1440, 1184, 768, 390, and 320 pixels.
- HTTP success, meaningful rendered content, JavaScript console/page errors, viewport-level horizontal overflow, and clipped interactive controls.
- A 105-class timetable, 50 review rows with lengthy comments and institutional labels, and 50 additional long academic-term rows.
- The existing deterministic scaling suite at 25%, 50%, 75%, and 100% demand projections.
- Chromium's full workflow and Firefox login, navigation, generation, timetable, reviews, and Help smoke coverage.

The complete synthetic browser journey exercises an invalid workbook followed by a valid workbook, commit, data check, running and failed run displays, successful CP-SAT generation, draft timetable inspection, submission, changes requested, endorsement, central approval, XLSX/CSV export, print media, and archived rendering.

## Screenshot evidence

The screenshots below were captured from the local release candidate with the reproducible `scripts/capture_validation_screenshots.py` utility and a visibly synthetic `qa-evidence-central` account. The underlying workbook is the repository's deterministic demonstration data.

![USM Scheduler login at 1440 pixels](evidence/ui/login-1440.png)

![Guided scheduling dashboard at 1440 pixels](evidence/ui/home-1440.png)

![Synthetic timetable at 1440 pixels](evidence/ui/timetable-1440.png)

![Help workflow at 390 pixels](evidence/ui/help-390.png)

Additional evidence captures: [Generate Schedule](evidence/ui/generate-1440.png), [Reviews](evidence/ui/reviews-1440.png), and [Help at 1440 pixels](evidence/ui/help-1440.png).

## Accessibility

Automated scans use `axe-playwright-python==0.1.8` against WCAG 2.0/2.1 A and AA plus WCAG 2.2 AA tags on Login, Home, Prepare Data, Generate Schedule, Timetables, Reviews, and Help at desktop and mobile widths. Automated scanning cannot establish full conformance.

The thesis evidence copy must also record these manual checks:

| Manual check | Tester/date | Result | Evidence/notes |
|---|---|---|---|
| Keyboard-only login, navigation drawer, forms, details, tables, timetable, reviews, and sign-out |  | Pending |  |
| Visible focus and logical focus order |  | Pending |  |
| 200% zoom and responsive reflow |  | Pending |  |
| Text/non-text contrast, including focus and status colors |  | Pending |  |
| Loading, success, and error status announcements |  | Pending |  |
| Screen-reader names, headings, landmarks, form instructions, and table captions |  | Pending |  |
| Reduced-motion preference |  | Pending |  |
| Print output for approved timetable and Help |  | Pending |  |

Release acceptance requires zero unresolved automated A/AA violations and no blocker in the manual checks.

## Usability validation

Participant testing cannot be simulated by an automated agent. The approved study team must run the sessions using [the usability validation kit](usability-test-kit.md) and store task observations in a protected copy of [the results template](usability-results-template.csv).

| Measure | Acceptance rule | Current result |
|---|---|---|
| Participants | 3–5 representative users, IDs only (`U01`–`U05`) | Pending field sessions |
| Critical task completion | Every task succeeds for all but at most one participant | Pending field sessions |
| Blockers | None unresolved | Pending field sessions |
| Permission misunderstandings | All fixed and re-tested | Pending field sessions |
| Repeated issues | Fix issues observed by two or more participants | Pending field sessions |

No participant outcome, completion time, quotation, or acceptance claim is fabricated in this report. Aggregate de-identified findings should replace the pending cells only after the approved sessions.

## Defects and limitations

- The focused automated cycle found no unresolved application UI, permission, overflow, browser-console, or automated WCAG A/AA defect. No additional broad visual changes were made.
- The first PostgreSQL CI run identified five pre-existing experiment-test fixtures whose generated college codes exceeded the model's 20-character limit. SQLite had not enforced that length. The fixtures now use compact deterministic hashes; all 14 experiment tests pass locally, with no runtime model or migration change.
- WebKit/Safari is outside the thesis browser target.
- Automated axe results cover detectable rules only; the manual checks and participant study remain necessary.
- Local SQLite evidence is supplemented by, not equivalent to, the dispatched Linux/PostgreSQL/Redis CI run.
- Thesis results apply to the tested synthetic/approved datasets and roles; they do not establish institution-wide production readiness.
- The fixed maximum of 50 is an academic section/meeting rule. The platform does not validate chairs, floor area, or variable physical-room capacity; participating rooms require administrative prevalidation.
- The UI baseline's automated browser and accessibility evidence must be rerun after experimental-platform changes. Manual accessibility and participant evidence remain pending regardless of automated results.

## Acceptance decision

The branch is technically eligible for thesis evaluation only after all updated
automated checks and the final-commit CI workflow pass. A formal algorithm
conclusion additionally requires every formal experiment gate above. Final
thesis-validation acceptance remains pending until institutional rule/data
authorization, the manual accessibility checklist, and approved 3–5 participant
usability sessions satisfy the stated rules.
