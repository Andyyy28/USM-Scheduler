# USM Scheduler usability validation kit

Use this kit only under the approved adviser/REC protocol and only with the synthetic trial workbook or another approved, de-identified dataset. It is designed for three to five representative scheduling staff. Do not record names, employee numbers, voices, or screens unless the approved consent materials explicitly permit that collection.

## Session controls

- Assign participant IDs sequentially as `U01` through `U05`. Keep any identity key outside this repository.
- Use the same release-candidate commit, browser version, device class, and synthetic dataset for every participant.
- Reset the database to the documented starting state before each session.
- Read the task prompts exactly as written. Do not demonstrate the interface first.
- Record assistance only after the participant has attempted the task. Note the prompt given, not an interpretation of the participant.
- Stop the session if a participant attempts to enter real institutional or personal data.

## Starting state

1. Check out the `ui/simplified-workflow` release-candidate commit and record its full hash.
2. Run the documented local setup and `python manage.py seed_demo` using study-only passwords.
3. Confirm that the browser contains no saved credentials, extensions that alter pages, or previous session data.
4. Give the participant the assigned role account and the synthetic workbook. Use a central scheduler account for Tasks 1–4 and 7, a college reviewer account for Task 5, and the central scheduler account for Task 6.
5. Start timing when the facilitator finishes reading each task. Stop when the participant states that the task is complete or abandons it.

## Participant tasks

| ID | Prompt | Success condition | Critical |
|---|---|---|---|
| T1 | “Find the active academic term and tell me what the system recommends doing next.” | Correctly identifies both the active term and recommended action from Home. | Yes |
| T2 | “Use the supplied synthetic workbook to prepare the semester data. Resolve any message that prevents it from being saved.” | Workbook is checked and committed as prepared data; no real data are entered. | Yes |
| T3 | “Generate a schedule using the recommended method.” | Data check completes and a CP-SAT candidate reaches a successful terminal state. | Yes |
| T4 | “Find the assigned room and time for the named synthetic class.” | Correct class, room, day, and time are reported from the timetable. | Yes |
| T5 | “As the college reviewer, check the timetable and record the decision described on this task card.” | Correct college decision and a meaningful required comment are saved. | Yes |
| T6 | “As the central scheduler, approve the endorsed timetable and obtain an Excel copy suitable for authorized use.” | Timetable is approved and the XLSX export starts successfully. | Yes |
| T7 | “Find help for a failed workbook check and explain what you would do next.” | Opens the relevant Help troubleshooting content and states the correct recovery step. | Yes |

Use only fictional class names and decisions on task cards. Counterbalance the named class and requested reviewer decision when the approved study design requires it.

## Observation record

Copy one row per participant/task into `usability-results-template.csv`.

- `completed`: `yes` or `no` based only on the success condition.
- `time_seconds`: elapsed task time rounded to the nearest second.
- `errors`: count of observable actions that move away from the task goal or require recovery.
- `assistance`: `none`, `general_prompt`, or `direct_instruction`.
- `severity`: `blocker`, `major`, `minor`, `cosmetic`, or `none`.
- `feedback`: one concise de-identified paraphrase. Do not transcribe unrelated conversation.
- `evidence_ref`: approved screenshot/note identifier, if captured; otherwise leave blank.

After each session, ask only these concise questions:

1. Which step, if any, was hardest to understand?
2. Was any label different from the words you normally use for this work?
3. What information did you need but could not find?
4. How confident would you feel repeating the workflow without assistance?

## Decision rules

- Every critical task must succeed for all but at most one participant.
- Fix every blocker, permission misunderstanding, and issue repeated by two or more participants.
- Re-run affected tasks after a fix using the approved protocol; do not silently replace the original result.
- Record isolated cosmetic preferences as observations without restarting broad visual redesign.
- Do not declare the release candidate accepted while a critical blocker, permission leak, or unresolved repeated issue remains.

## Session closeout

Sign out, remove downloaded exports, clear the browser profile, reset synthetic state, and store approved research records in the ethics-approved location outside this public repository. Add only aggregate, de-identified results to the validation report.
