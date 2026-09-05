# USM Scheduler User Guide

> **Thesis prototype — not an official published scheduling system.**
>
> This application supports authorized scheduling decisions for the University of Southern Mindanao (USM). A solver result is never an automatic university schedule. College review and central approval remain required, and institutional data may be used only with formal authorization.

**System:** USM University Scheduling Optimization System

**Research title:** *A Comparative Evaluation of CP-SAT and Genetic Algorithm for University Timetabling and College-Boundary-Aware Room Assignment at the University of Southern Mindanao*

**Researchers:** Ruby Jean B. Solomon and Edgardo Gabriel L. Paclibar

**Intended users:** system administrators, central scheduling personnel, and authorized college reviewers

**Guide status:** prototype operating guide, version 1.0

## 1. What the system does

USM Scheduler assigns each predeclared class meeting to a valid time and room. Sections, subjects, instructors, teaching loads, meeting durations, and required weekly sessions are inputs. The system does **not** assign instructors.

Both optimization engines use the same frozen input:

- Google OR-Tools Constraint Programming–SAT (CP-SAT);
- a project-owned Genetic Algorithm (GA);
- one shared set of legal room-time candidates;
- one approved soft-objective profile; and
- one independent validator and scorer.

The system checks instructor, section, and room conflicts; availability; contiguous duration; laboratory capabilities; college or offering-unit room authorization; distinct-day rules; and active locks. Faculty preferences, internal vacant periods, and daily-load balance may be scored as soft objectives.

Every section and combined meeting uses a fixed maximum of 50 students; exactly
50 is accepted and 51 or more blocks formal snapshot creation. This applies to
classrooms, laboratories, and special-purpose rooms. It is not chair, floor-area,
or variable room-capacity validation, so participating rooms must be
administratively prevalidated. Individual student elective conflicts,
examinations, walking distance, inter-campus travel, and instructor assignment
remain outside the thesis baseline.

## 2. Roles and authority

| Role | Main responsibilities | Important limits |
|---|---|---|
| System administrator | Accounts, roles, academic master data, platform configuration, emergency administration | Must not bypass validation or institutional authorization |
| Central scheduler | Terms, imports, snapshots, solver runs, experiments, timetable versions, locks, and final approval | Can approve only an independently validated feasible schedule |
| College reviewer | Review complete timetables and endorse or request changes for assigned college scope | Cannot approve the university schedule or decide for another college |

Use a named account for normal work. Do not share administrator credentials. Sign out when leaving a shared workstation.

## 3. Workflow at a glance

```text
Authorized semester data
        ↓
Preview and validate XLSX import
        ↓
Commit immutable term revision
        ↓
Approve objective profile and run preflight
        ↓
Freeze problem snapshot and hash
        ↓
Run CP-SAT and/or GA
        ↓
Independent validation and scoring
        ↓
Review timetable → lock/regenerate if needed
        ↓
College endorsements → central approval → export
```

Never skip from solver output directly to publication. A result can be mathematically feasible while still requiring authorized personnel to confirm that the source data and institutional interpretation are correct.

## 4. Start the local demonstration

### 4.1 Docker setup (recommended)

Requirements: Docker Desktop with Compose v2.

Open PowerShell:

```powershell
cd "D:\USM Scheduler"
Copy-Item .env.example .env
docker compose up --build -d
```

Create deterministic synthetic data and demo accounts:

```powershell
docker compose exec web python manage.py seed_demo `
  --admin-password "AdminPassword123!" `
  --central-password "SchedulerPassword123!" `
  --reviewer-password "ReviewerPassword123!"
```

Open <http://127.0.0.1:8000/accounts/login/>.

| Demonstration role | Username | Password |
|---|---|---|
| Central scheduler | `demo-scheduler` | `SchedulerPassword123!` |
| College reviewer | `demo-reviewer` | `ReviewerPassword123!` |
| System administrator | `demo-admin` | `AdminPassword123!` |

These credentials are intentionally public test credentials. Never reuse them for an institutional deployment.

Check the services when the site does not open:

```powershell
docker compose ps
docker compose logs -f web worker
```

The health endpoint is <http://127.0.0.1:8000/healthz/>.

### 4.2 Direct Python setup when Docker is unavailable

Python 3.12 is recommended.

```powershell
cd "D:\USM Scheduler"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
$env:CELERY_TASK_ALWAYS_EAGER = "true"
python manage.py migrate
python manage.py seed_demo `
  --admin-password "AdminPassword123!" `
  --central-password "SchedulerPassword123!" `
  --reviewer-password "ReviewerPassword123!"
python manage.py runserver
```

With `POSTGRES_HOST` unset, this mode uses an untracked local SQLite database. Eager task execution is suitable for a demonstration, while PostgreSQL, Redis, and a Celery worker are the intended deployment topology.

## 5. Practice data supplied with the system

The repository provides two complementary, wholly synthetic datasets. Neither
contains official USM rooms, instructors, subjects, or schedules.

### 5.1 Fast seeded demonstration

The `seed_demo` command creates synthetic, de-identified data for the Kabacan demonstration term:

- one college: College of Science and Mathematics (`CSM`);
- one department: Department of Computer Science (`DCS`);
- one program and section: BS Computer Science, `BSCS-1A`;
- two subjects: an introductory computing lecture and a programming laboratory;
- two fictional instructors;
- one classroom and one computer laboratory;
- explicit major-subject room authorizations;
- a computer-laboratory capability required by the laboratory meeting;
- eight 30-minute atoms across Monday and Tuesday, from 8:00 a.m. to 10:00 a.m.;
- one approved demonstration objective profile; and
- one committed immutable dataset revision.

Expected checks:

- both required meetings are assigned exactly once;
- the laboratory meeting uses `CSM-LAB`;
- neither the section nor either room is double-booked;
- every final feasible result has zero independent hard violations; and
- the exact day and start time may differ by solver or seed without being incorrect.

All demonstration names and codes are fictional research fixtures. They are not evidence of an official USM timetable, room inventory, or faculty assignment.

### 5.2 Full synthetic trial workbook

For a more realistic hands-on test, sign in as `demo-scheduler`, open **Data
import**, select the target term under **Practice workbook**, and choose
**Download practice workbook**. The schema 1.1 file freezes that term's existing
approved policy hashes; import it into the same term. The downloader never
creates or approves policies. After `seed_demo`, it can also be generated with:

```powershell
python manage.py create_trial_workbook `
  --term-id 1 --output .\USM-Scheduler-Synthetic-Trial-v2.xlsx
```

The tracked `examples/USM-Scheduler-Synthetic-Trial-v1.xlsx` remains a legacy
exploratory fixture. It lacks thesis-v2 enrollment and policy evidence and must
not be used for the formal pilot. The v2 fixture requires approved v1 policies
`FIXED_STUDENT_LIMIT_50`, `INSTRUCTOR_DAILY_LOAD`, and `RECURRING_RESERVED_BLOCKS`
for the selected term; the demo setup labels these policies as synthetic.

The workbook contains 14 required meetings across five sections and 11 course
offerings. It deliberately exercises:

- incoming, continuing, and graduating section statuses;
- contextual major, minor, and general-education classifications;
- fixed instructors, one team-taught offering, and one shared-section offering;
- lecture and computer-laboratory meetings;
- explicit department room grants and shared/borrowed room grants;
- configurable Monday-to-Friday 30-minute atoms with a lunch break;
- a restricted instructor profile and a restricted laboratory profile;
- instructor preferences;
- a twice-weekly distinct-day requirement; and
- one valid locked meeting.

All codes begin with `SYN-` or `TEST-`, student sheets are intentionally empty,
and workbook metadata states that the records are synthetic. The automated test
suite confirms that this workbook previews and commits without errors, builds a
14-event problem, and produces independently feasible CP-SAT and GA results.

To try it:

1. Select the active demonstration term on **Prepare scheduling data**.
2. Upload the synthetic trial workbook and confirm the authorization/data-
   minimization statement. For this file, the confirmation means you understand
   that it is safe fictional practice data.
3. Select **Check workbook**. Confirm that the preview reports zero errors.
4. Select **Commit clean revision**.
5. Open **Generate schedule**, create a frozen snapshot from the new committed
   revision and approved objective profile, then run both algorithms.
6. Compare the two independently validated results and open **Timetables** to
   inspect the weekly view.

## 6. Sign in and navigate

After signing in, the left navigation contains:

- **Home:** current term, data counts, recent runs, and pending reviews;
- **Academic Terms:** term registry, term cloning, and revision finalization;
- **Generate Schedule:** preflight, individual solver runs, and run history;
- **Timetables:** versioned assignments, validation, locking, review, approval, and export;
- **Prepare Data:** XLSX template, term-specific synthetic trial download, preview, errors, and transactional commit (central roles only);
- **Reviews:** college endorsement and change-request queue;
- **Help and user guide:** role guidance, solver-status explanations, downloads, and troubleshooting; and
- **Research tools:** separate Formal Study and Exploratory Analysis workflows, outcome tables, excluded diagnostic traces, and evidence downloads; and
- **Django administration:** visible to staff administrators through the user menu.

The interface supports keyboard navigation and responsive layouts. Use the “Skip to main content” link when navigating by keyboard.

## 7. Configure institutional data

An administrator should complete the following before an authorized real-data import:

1. Confirm the academic term, campus, start and end dates.
2. Confirm colleges, departments, programs, and curriculum versions.
3. Confirm subject catalog records and program-specific `MAJOR`, `MINOR`, or `GE` classifications.
4. Confirm fixed offering-to-section and offering-to-instructor links.
5. Confirm room kind, owner, laboratory profile, and capabilities.
6. Enter explicit room authorization by college or department and classification. Ownership alone is not authorization.
7. Configure active time atoms and breaks.
8. Complete instructor and room availability profiles. If a resource is fully available, record the named acknowledgement rather than leaving availability ambiguous.
9. Record optional instructor preferences separately from hard availability.
10. Review and approve an objective profile before building a research snapshot.

The room-policy matrix and objective weights require authorized USM scheduling-personnel sign-off. Public university webpages are not substitutes for this internal approval.

## 8. Import semester data

### 8.1 Prepare a workbook

1. Open **Data import**.
2. Select **Download blank template**.
3. Populate only the documented sheets and values.
4. Remove direct student identifiers. Student rows are optional; the solver operates on sections.
5. Keep the original authorized workbook in protected storage, outside Git.

To generate a blank template from PowerShell:

```powershell
python manage.py create_import_template .\usm-semester-template.xlsx
```

### 8.2 Preview and correct

1. Choose the target academic term.
2. Select or drag the `.xlsx` workbook into **Validate a completed workbook**.
3. Select **Check workbook**.
4. Review diagnostics by sheet, row, column, error code, and message.
5. Correct the source workbook and upload it again. Invalid batches never partially change solver data.

Typical blocking errors include missing references, duplicate codes, invalid durations, incomplete availability, incompatible laboratory definitions, unauthorized rooms, and stale or conflicting locks.

### 8.3 Commit

When the preview has no blocking errors, a central scheduler selects **Commit clean revision**. The resulting revision is immutable and receives a content hash. Later corrections require a new revision; historical research inputs are never silently overwritten.

## 9. Clone and update a semester

Use **Academic terms → Clone a semester planning base** when preparing a later term.

1. Select a committed source revision.
2. Enter the new academic year, semester, dates, and campus.
3. Select **Clone term inputs**.
4. In administration or through an authorized import, add incoming sections, retain or update continuing sections, deactivate graduated/inactive sections, and update offerings and availability.
5. Review and approve the cloned term’s objective profile.
6. Under **Validate and freeze an edited clone**, select the draft and objective profile.
7. Select **Validate and commit revision**.

This demonstrates functional semester adaptability. A single real term does not establish longitudinal reliability across future semesters.

## 10. Run preflight and freeze a snapshot

Open **Generate schedule**.

1. Under **Step 1 · Data check — Check and freeze the scheduling data**, select the committed term revision.
2. Select a term-matching approved objective profile.
3. Select **Check data and create snapshot**.

Preflight converts each meeting into legal `(room, start time, occupied atoms)` candidates. It rejects local violations before either solver runs. If a meeting has no legal candidate, correct the input or obtain an explicitly approved policy change in a new revision. Do not weaken a hard rule merely to make a run start.

Record the snapshot hash when producing thesis results. CP-SAT and GA are comparable only when the snapshot and objective hashes match.

## 11. Generate a test schedule

1. Open **Generate Schedule** and choose your **Checked semester data** in step 2.
2. Keep **Constraint solver (recommended)**, or choose **Genetic Algorithm**.
3. Keep **Find a valid timetable (recommended)** as the generation goal. Both methods stop once a complete timetable passes independent validation. This goal does not optimize the quality score.
4. Keep **300 seconds** (5 minutes) and random seed **42** to start.
5. Select **Generate timetable**. In local demonstration mode, keep the page open until the result appears. With a separate worker, refresh the result page to check progress.
6. When the result says **Schedule found**, select **Open timetable** to review it.

To keep searching for fewer gaps and better preferences, choose **Use the full time to improve timetable quality**. A valid result found within the time limit remains a success even when the solver cannot prove that it is best.

If the limit ends before a timetable is found, select **Try again with more time**. This copies the checked data, method, and random seed into a new attempt and increases the limit (up to 3600 seconds). An impossible combination of rules needs corrected data, not just more time. A system error has separate guidance and a run identifier for the administrator.

Routine first-feasible runs are excluded from research analysis. Use **Research tools** for full-budget comparisons with frozen objectives, seeds, worker counts, and equal time limits. Neither practice timings nor a quick timetable establishes a thesis result.

### Status reference

| Status | Meaning | User response |
|---|---|---|
| Queued | Waiting for the worker | Wait; inspect worker logs if it remains queued |
| Running | Solver search is active | Avoid launching competing benchmark work |
| Schedule found (FEASIBLE) | Zero-hard solution found; optimality not proven | Review quality and institutional correctness |
| Best schedule found (OPTIMAL) | CP-SAT proved the objective optimal for this run | Still requires human review |
| Conflicting scheduling rules (INFEASIBLE) | CP-SAT proved the frozen model infeasible | Diagnose data, policy, and locks in a new revision |
| No timetable found (NO_SOLUTION) | Search returned no feasible solution within its conditions | Do not call the instance infeasible |
| Time limit reached (TIMEOUT) | No validated timetable was found before the limit | Try more time or revise restrictive availability and locks |
| Generation failed (FAILED) | Infrastructure or application error | Inspect logs and correct the infrastructure cause |
| Canceled | Authorized user stopped the job | Preserve it as a canceled record |

GA must never be described as proving optimality or infeasibility.

## 12. Interpret and compare results

In **Run details**, interpret evidence in this order:

1. **Hard violations:** must equal zero.
2. **Status and stopping reason:** distinguish feasibility, proof, timeout, and failure.
3. **Raw objective penalty:** lower is better among feasible schedules.
4. **Component penalties:** faculty preference, section gaps, instructor gaps, and daily-load imbalance.
5. **Normalized quality:** a secondary 0–100 display, never the only reported value.
6. **Time to first feasible:** time until the first independently valid schedule.
7. **Execution time:** total measured solver time.
8. **Hashes and configuration:** evidence that inputs were comparable.

Open **Compare runs** and choose a CP-SAT and GA result from the same snapshot. The system should reject or clearly distinguish unmatched inputs.

For thesis analysis, create the **controlled 30-seed comparison** and queue it once the data, solver settings, and objective profile are frozen. Thirty seeds across two algorithms produce 60 sequential trials. At the default 300-second upper bound, the worst-case search budget can reach five hours, excluding setup and reporting. Plan the run on a stable, plugged-in machine.

## 13. Review, lock, regenerate, and approve

Every independently feasible run creates a candidate timetable version.

### Central scheduler

1. Open **Timetables** and select a version.
2. Search by subject, section, instructor, room, or college.
3. Select **Validate independently**.
4. Confirm zero violations and inspect the full assignments.
5. Select **Submit for review**.
6. If reviewers accept particular meetings, select their **Carry forward** checkboxes and choose **Lock selected assignments**.
7. Select **Regenerate child** when changes are required. Locks become hard inputs to both solvers.
8. After required endorsements and final verification, select **Approve schedule**.

### College reviewer

1. Open **Reviews**.
2. Open the relevant timetable and inspect the complete schedule.
3. For a college within your assigned scope, choose **Endorse** or **Request changes**.
4. Enter a specific required comment and save the decision.

The most recent decision for a college is authoritative. Reviewers cannot endorse another college’s scope, and a central scheduler cannot impersonate a college endorsement.

### Approval and export

Only a central scheduler or system administrator can approve. Approval requires an independently feasible schedule and the required review state. Approved versions are immutable; further work creates a child version. CSV and XLSX export becomes available only for approved schedules.

## 14. Troubleshooting

| Symptom | Likely cause | Safe action |
|---|---|---|
| No term is available | No active term or committed revision | Seed the demo or configure/import an authorized term |
| Preflight button is disabled | Missing committed revision or approved objective profile | Complete both prerequisites |
| Workbook cannot commit | Blocking import errors | Correct and re-upload; do not edit the database around validation |
| No legal candidate for a meeting | Availability, duration, capability, authorization, campus, or lock conflict | Inspect the diagnostic and correct the source policy/data |
| Run stays queued | Celery worker or Redis is unavailable | Check `docker compose ps` and worker logs |
| Run times out | Search budget was exhausted | Preserve the result; use the approved benchmark limit rather than ad hoc retries |
| GA found no schedule | Heuristic search did not find feasibility | Do not claim proof of infeasibility; compare with CP-SAT diagnostics |
| Approval is unavailable | Schedule is invalid, in the wrong state, or lacks required review | Validate and complete the review workflow |
| Export is unavailable | Version is not approved | Complete approval; draft exports are intentionally restricted |
| Health endpoint returns 503 | Database is unavailable | Restore database connectivity before accepting work |

## 15. Data protection and responsible use

- Obtain written authority before importing real semester data.
- Follow the University Data Protection Office’s applicable requirements and retention decisions.
- Prefer aggregate section records. Do not place names, student numbers, passwords, or raw workbooks in source control.
- De-identify instructor identifiers in shared thesis extracts.
- Keep raw uploads, database dumps, backups, secrets, and experiment outputs in approved protected storage.
- Treat SHA-256 hashes as integrity identifiers, not anonymization.
- Record every assumption, policy exception, objective profile, software build, and dataset revision used for research claims.
- Use an official high-resolution seal and institutional branding only after confirming authorization. USM publishes an [official seal description](https://www.usm.edu.ph/about-usm/usm-seal/), but publication on the website does not by itself grant project-use permission. The thesis interface must remain identifiable as a research prototype until formally adopted.

USM’s official privacy context is available from the [University Data Protection Office](https://www.usm.edu.ph/administration/university-data-protection-office/) and the [student privacy notice](https://www.usm.edu.ph/privacy-notice-for-students-alumni-and-prospective-students/).

## 16. Before publishing a timetable

Confirm all of the following:

- the term revision and objective profile were formally approved;
- every required meeting is present exactly once;
- independent hard violations equal zero;
- laboratory capabilities and room authorization were reviewed;
- instructor and room availability assumptions were acknowledged;
- college reviews were recorded by authorized reviewers;
- locks and regeneration lineage are understood;
- the central approver reviewed the final version;
- only the approved version is exported; and
- no protected data appear in research/public artifacts.

## 17. Further documentation

- [Complete system plan](system-plan.md)
- [System architecture](architecture.md)
- [Data dictionary](data-dictionary.md)
- [Controlled experiment protocol](experiment-protocol.md)
- [Development and acceptance roadmap](development-roadmap.md)
- [Thesis defense notes](thesis-defense.md)
