# USM Kabacan research and gap assessment

## Purpose and evidence standard

This desk review records what can be verified from publicly accessible University of Southern Mindanao (USM), Philippine regulatory, and web-standards sources as of 24 August 2026. It is intended to guide the Kabacan-campus thesis prototype; it is not a substitute for interviews, official memoranda, the USM Code, faculty workload rules, room inventories, or written approval from authorized university offices.

In this document:

- **Verified** means the cited source directly supports the statement.
- **Not publicly verified** means no authoritative public source was located during this review. It does not mean the policy or practice does not exist.
- **Proposed control** means a project safeguard or acceptance criterion, not an existing USM rule.

The concept paper and application should preserve these distinctions. In particular, they should not present a proposed workflow, a stakeholder interview, an old timetable, or a prototype default as university policy.

## Verified institutional context

### Institutional identity and Kabacan scope

USM describes itself as a land-grant university in Southern Philippines. Its official history says the institution began operations in 1954 as the Mindanao Institute of Technology and obtained university status through Presidential Decree No. 1312 in 1978. The same page identifies Kabacan as the main campus, gives its land area as 1,024 hectares, and lists the university address as Bai Matabay Plang Avenue, Poblacion, Kabacan, Cotabato 9407 ([USM About](https://www.usm.edu.ph/about-usm/)).

This supports describing the study as a **one-campus Kabacan case study**. It does not support assumptions about the ownership, authorization, suitability, accessibility, or availability of individual rooms. Those details require a signed institutional inventory.

USM's published vision emphasizes quality and relevant education that is globally competitive, culture-sensitive, and morally responsive. Its mission emphasizes socioeconomic development, harmony among diverse communities, improved quality of life, and the four functions of instruction, research, extension, and resource generation. Its five published core values are Goodness, Responsiveness, Excellence, Assertion of Right, and Truth (GREAT) ([Mandates, Vision and Mission](https://www.usm.edu.ph/about-usm/mandates-vision-mission/); [Resource Generation and Entrepreneurial Services](https://www.usm.edu.ph/resource-generation-entrepreneurial-services/)).

The visual design and user guidance may reflect these values through clear language, restrained green-and-gold styling, respectful terminology, and transparent decision records. The values do not, by themselves, define optimization constraints.

### Quality-management context

USM's published Quality Policy calls for key result areas and performance indicators, continual improvement, stakeholder relationships, good governance, culture sensitivity, and compliance with customer, regulatory, and statutory requirements ([Quality Policy](https://www.usm.edu.ph/about-usm/quality-policy/)). These commitments align with the prototype's measurable solver outcomes, immutable problem snapshots, independent validation, approval history, and reproducibility manifests.

The official ISO/QMS page identifies colleges and the Director for Instruction Office as core processes and identifies the Admission and Records Office and Physical Plant and Development Services among support units ([ISO 9001 Quality Management System](https://www.usm.edu.ph/administration/iso-9001-quality-management-system/)). This establishes those offices as relevant stakeholders. It does not establish which office owns timetable generation or gives final approval.

### Academic data changes and ownership

USM's public course page states that the undergraduate degree listing is produced by the Admission and Records Office through the collective work of colleges, deans, academic assistants, department chairs, vice chairs, and secretaries ([USM Courses](https://www.usm.edu.ph/courses/)). This supports treating academic catalog data as shared institutional master data rather than data owned by the optimization engine.

An official July 2026 announcement states that BS Information Technology would begin in Academic Year 2026-2027 ([USM Adds BS Information Technology](https://www.usm.edu.ph/usm-adds-bs-information-technology-to-degree-offerings-secures-copcs-for-three-academic-programs/)). This is direct evidence that program offerings change over time. Colleges, departments, programs, curricula, subjects, classifications, and aliases should therefore be effective-dated or revision-bound; they should not be hard-coded in application code.

The public website's course list and academic navigation are useful starting points, but they are not an adequate production master-data source. Labels and scope can change, and undergraduate lists do not necessarily enumerate graduate or professional units. ARO and the Office of the Director for Instruction should provide or approve the term-specific master list used in a real scheduling run.

### Published timetable shape

Officially hosted historical timetable PDFs for Academic Year 2021-2022 show schedules organized by program, year, and section, with fields for subject code and title, lecture/laboratory units, class time, room, and assigned faculty. They also demonstrate recurring patterns such as MW and TTh, single-day meetings, separate lecture and laboratory components, and shared spaces ([historical CEIT schedule](https://www.usm.edu.ph/wp-content/uploads/2022/04/ceit.pdf); [historical College of Education schedule](https://www.usm.edu.ph/wp-content/uploads/2022/04/ced.pdf)).

These documents are appropriate references for the **shape of synthetic test data only**. Their dates, time ranges, room labels, meeting patterns, faculty assignments, and program structures must not be treated as current policy or copied into a public research dataset. Synthetic fixtures should use invented faculty identifiers and clearly state that they do not represent a real USM semester.

### Academic-term variation

USM publishes term-specific enrollment windows and class-start dates. For example, the published Academic Year 2025-2026 notice distinguished online and face-to-face enrollment periods and identified the start of classes ([Updated Enrollment Schedule and Start of Classes](https://www.usm.edu.ph/updated-enrollment-schedule-and-start-of-classes-a-y-2025-2026/)). USM also maintains a current announcements page for term notices and changes ([Announcements](https://www.usm.edu.ph/usmians/announcements/)).

This supports revisioning academic terms, availability, and calendar blocks. It does not identify every holiday, examination period, university hour, break, or college reservation that must be enforced by the class scheduler.

### Privacy and security context

USM has a University Data Protection Office directly under the Office of the President. Its published responsibilities include monitoring compliance with Republic Act No. 10173, developing privacy procedures, advising university units, training personnel, responding to data-subject requests, and conducting regular Privacy Impact Assessments (PIAs) ([University Data Protection Office](https://www.usm.edu.ph/administration/university-data-protection-office/)).

The same official page publishes privacy notices and a security-incident policy. Its student notice says USM processes data for admission, enrollment, registration, curriculum validation, and other academic purposes; limits disclosure to authorized recipients; and requires appropriate safeguards and data-sharing arrangements when third parties need access. The page also demonstrates that retention differs by record category. The scheduling project must therefore obtain a project-specific retention decision rather than assume that every scheduling record is permanent.

The Data Privacy Act classifies information about an identifiable person's education as sensitive personal information and requires transparency, legitimate purpose, proportionality, accuracy, data minimization, retention limits, and appropriate security ([Republic Act No. 10173](https://privacy.gov.ph/data-privacy-act/)). National Privacy Commission Circular No. 2016-01 requires government agencies to conduct a proportionate PIA for programs and processes involving personal data and to address risks through organizational, physical, and technical controls ([NPC Circular No. 2016-01](https://privacy.gov.ph/npc-circular-16-01-security-of-personal-data-in-government-agencies/)). The NPC's current registration guidance includes government agencies and instrumentalities among the mandatory registration conditions for covered data-processing systems ([NPC registration FAQ](https://privacy.gov.ph/pips-and-pics/faqs/)). The USM Data Protection Officer, not the development team, should determine the project's registration and compliance actions.

Because individual students do not enter the solver, student import should be optional and disabled by default. Section codes are sufficient for the baseline optimizer. Research exports should use synthetic or de-identified data and exclude names, email addresses, employee identifiers, upload files, authentication data, and free-text comments that could identify a person.

### Accessibility context

The Web Content Accessibility Guidelines (WCAG) 2.2 are the current W3C Recommendation for accessible web content. Relevant requirements include text alternatives, keyboard access, semantic names and roles, visible focus, sufficient contrast, reflow and zoom, non-color-only communication, accessible authentication, and programmatically exposed status messages ([WCAG 2.2](https://www.w3.org/TR/WCAG22/)). WCAG 2.2 Level AA is a proposed product acceptance target; this review did not locate a public USM policy declaring WCAG 2.2 AA as an institutional requirement.

Batas Pambansa Blg. 344 requires accessibility features in educational institutions and other covered buildings ([Accessibility Law, National Council on Disability Affairs](https://ncda.gov.ph/disability-laws/batas-pambansa/batas-pambansa-blg-344/)). The scheduler may therefore store verified room-accessibility attributes and allow an authorized scheduler to require or lock an accessible room. It should not collect or expose a student's diagnosis to accomplish that placement.

### Seal, identity, and trademark context

USM publishes an official explanation of its seal and a university-hosted downloadable image ([USM Seal](https://www.usm.edu.ph/about-usm/usm-seal/); [official seal asset](https://www.usm.edu.ph/wp-content/uploads/2024/09/usm_logo_Aug-2024.png)). That hosted asset is preferable to an unverified or low-resolution copy when permission has been obtained.

USM also reports that its Intellectual Property, Technology Transfer, and Business Development Office discussed proper logo use, trademark protection, licensing requirements, university policies, and legal implications with logo users ([USM trademark and logo consultation](https://www.usm.edu.ph/usm-ipttbdo-convenes-consultation-meeting-on-proper-use-of-university-trademark-and-logo-2/)). Public availability of the image is not proof that a thesis application is authorized to imply official endorsement.

Until USM grants written brand approval, the application, guidebook, screenshots, and concept paper should carry a visible statement such as: **"BSCS thesis prototype; not an official USM scheduling system."** No public official hexadecimal color palette was located in this review. Any extracted green-and-gold palette should be described as a prototype palette pending a university brand-kit review.

### Research-ethics context

USM has a Research Ethics Committee (REC). An official report on its 2025 university-wide orientation for Undergraduate Thesis I students says the sessions covered elements and types of ethical review, forms, procedures, and ethics certification ([USM REC undergraduate orientation](https://www.usm.edu.ph/usm-research-ethics-committee-conducts-university-wide-orientation-for-undergraduate-researchers/)).

The official RDE Link Center publishes a research-ethics procedure, new-protocol application, study template, informed-consent forms, undergraduate thesis process and templates, academic-integrity statement, and AI disclosure materials ([USM RDE Link Center](https://www.usm.edu.ph/rde-link-center/)). The researchers should ask the REC to determine the required review category before accessing real institutional data, interviewing scheduling personnel, or conducting usability testing. The project should not declare itself exempt.

## What is not publicly verified

The following items require documentary confirmation or signed validation from authorized USM personnel before a real-data pilot:

1. **Scheduling process owner and final approver.** Public pages establish relevant offices but do not establish the actual timetable preparation, endorsement, approval, publication, and amendment chain.
2. **College-boundary room rules.** No authoritative public room-authorization matrix was found for major, minor, general-education, shared, or borrowed rooms.
3. **Current room inventory.** Room codes, ownership, capabilities, laboratory types, accessibility features, operating hours, maintenance status, and effective availability were not publicly verified.
4. **Current academic master data.** The exact active colleges, departments, programs, curriculum versions, sections, offering units, and subject classifications for the target term require ARO/ODI confirmation.
5. **Time-grid and calendar rules.** Permitted teaching days/hours, atom size, lunch periods, university-wide reserved time, holidays, examinations, and exception handling were not found as a complete public policy.
6. **Faculty workload policy.** A USM quality-assurance accreditation page mentions preparation time and a limit of six continuous teaching hours, but it may be program self-survey evidence rather than a university-wide rule ([USM UQAC faculty evidence](https://uqac.usm.edu.ph/course/view.php?id=839)). It must not become a hard constraint without the current approved workload document and ODI/HR confirmation.
7. **Availability semantics.** It is not publicly established whether a missing faculty or room availability profile means fully available, unavailable, or incomplete data.
8. **Operational problem magnitude.** No public audit located in this review quantifies current timetable conflicts, out-of-unit room assignments, staff effort, retries, or delays. The concept paper should call these reported risks or study motivations until interviews or a baseline audit substantiate them.
9. **Brand permission and official palette.** The official seal is downloadable, but permission for this prototype's use and an authoritative digital palette were not publicly established.
10. **Real-data and participant authorization.** Public sources do not grant the thesis team access to semester records, faculty availability, room data, or human participants.

## Prioritized gap assessment

Priorities indicate what is needed for an institutional pilot, not the order in which every feature must be coded.

### P0 — required before a real-data pilot

| Gap / proposed control | Rationale | Acceptance criteria |
|---|---|---|
| Rule provenance and approval registry | The most important local constraints are not publicly documented. A solver rule without ownership and an effective term can silently misrepresent policy. | Every hard and soft rule has an identifier, plain-language definition, type, owner, approving office, source/reference, effective term, version, and approval date. A snapshot stores the exact approved rule/profile hash. Unapproved rules are visibly marked and cannot be used for a production-labelled run. |
| Authoritative, effective-dated academic master data | Official announcements show that programs change, and public listings are not a production source. | ARO/ODI signs off the target term's colleges, departments, programs, curricula, subjects, classifications, offering units, and aliases. Import rejects unknown or inactive references. No academic unit or program name is hard-coded in solver or UI logic. |
| Data-owner and responsibility matrix | Relevant offices are identifiable, but the scheduling workflow is not publicly verified. | A signed RACI or equivalent names owners for catalog, offerings, faculty assignments/availability, rooms/capabilities, authorizations, calendar, review, final approval, publication, and incident response. Application roles and permissions match the approved matrix. |
| Academic calendar and reserved-block import | Enrollment and class dates vary by term; incomplete calendars can make a mathematically feasible schedule operationally invalid. | Each revision declares teaching dates/days, allowed hours, breaks, holidays, institution-wide and college reserved blocks, and exceptions. Preflight detects gaps and conflicts. A calendar change creates a new revision and identifies affected schedule versions. |
| Section headcount and fixed scheduling limit | The approved scope uses one 50-student maximum rather than uncertain room-by-room chair data. Missing or duplicated section counts would make shared-meeting validation unreliable. | Every active section has an authorized expected enrollment from 1–50. A combined meeting sums each unique attached section once; exactly 50 is valid and 51 or more blocks snapshot creation. The same rule applies to every participating room type, while rooms are administratively prevalidated. No chair, floor-area, or variable-capacity claim is made. |
| Verified room and authorization inventory | College-boundary and laboratory eligibility are central hard constraints but no public matrix was found. | PDS and academic owners approve active rooms, campus, owning unit, capabilities, lab profile, accessibility attributes, availability, and explicit authorizations by subject classification/offering unit/effective term. Ownership alone never implies authorization. |
| Data minimization and privacy compliance pack | Faculty availability and education records may be personal or sensitive; students are unnecessary for the baseline solver. | USM UDPO records its PIA decision, lawful basis, data inventory/flow, privacy notice, access rules, retention/disposal schedule, sharing restrictions, incident path, and NPC registration determination. Student import is off by default. Research exports pass a documented disclosure review and contain no direct identifiers. |
| Security and recovery baseline | The scheduler holds authoritative inputs, approvals, and potentially personal data; availability and integrity matter as much as confidentiality. | Production configuration enforces unique accounts, least privilege, secure cookies/transport, password policy or institutional SSO readiness, session expiry, audit logging, protected secrets, encrypted backups, and a successful restore test. Access reviews and incident contacts are documented. |
| Research authorization and REC determination | Real data, interviews, and usability testing may require institutional and ethics review. | The repository or research file records data-owner authorization, adviser approval, REC determination/certification, approved protocol, current consent materials where applicable, and permitted reporting/de-identification conditions before collection begins. |
| Brand authorization and prototype status | USM publicly treats its logo as protected institutional identity. | Written IPTTBDO or other formally designated university approval is retained for the intended use. Until approved, every app page, guidebook, concept-paper rendering, and public demo states that it is a thesis prototype and not an official USM system. The seal has meaningful alt text and is not distorted or recolored. |
| WCAG 2.2 AA product gate | Professors and staff need a reliable interface across keyboard, zoom, low vision, and assistive technologies. | Automated and manual checks cover keyboard-only workflows, focus order/visibility, labels and errors, timetable table semantics, 200% zoom/reflow, contrast, non-color status cues, accessible authentication, and live status messages. Critical workflows have no unresolved Level A/AA defects. |
| Data-readiness report | Solver failures caused by missing policy data should not be counted as algorithm failures. | Before snapshot creation, the system reports completeness and provenance for offerings, assigned instructors, sections, meeting durations/patterns, availability acknowledgements, rooms, capabilities, authorizations, calendar, locks, and objective profile. Blocking issues prevent both solvers consistently. |

### P1 — required for a controlled operational pilot

| Gap / proposed control | Rationale | Acceptance criteria |
|---|---|---|
| Configurable review, change-request, and publication workflow | A schedule remains a decision-support result until authorized personnel accept it; post-publication changes need accountability. | Workflow stages and scoped reviewers come from the approved responsibility matrix. Every decision has actor, scope, timestamp, reason/comment, and source/child version. Only one active approved version exists per term; published exports display version, status, and generation time. |
| Room outage and calendar-change impact handling | Maintenance or emergency closures can invalidate approved assignments. | An authorized change creates a new resource/calendar revision, lists affected assignments, prevents reuse of stale approvals, and offers locked child regeneration. Historical versions remain unchanged. |
| Approved faculty daily-load rule | Daily teaching limits can improve usability but are not publicly verified as university-wide rules. | Every participating instructor has either a positive approved maximum daily teaching-atom count or an explicit approved no-limit acknowledgement. The source, owner, effective term, and hard/soft classification are recorded, and both solvers plus the independent validator use the same policy. |
| Manual-baseline audit and schedule difference report | A baseline supports the problem statement and lets users understand regeneration impact without adding a third optimizer. | An authorized historical/manual schedule is imported separately, independently validated, and reported with the same violation definitions. Version comparison identifies moved unlocked meetings without claiming the baseline is an optimization algorithm. |
| Explainable preflight and infeasibility support | Users need actionable causes, not a generic failure status. | Local failures identify the meeting and exhausted eligibility filters. If CP-SAT proves infeasibility, the report distinguishes that proof from timeout/unknown and, where implemented, presents a reviewed minimal or small conflicting policy set. GA reports only that no feasible solution was found within budget. |
| Approved schedule distribution | Staff need dependable printable and calendar views without exposing research or personal data. | Approved-only XLSX/CSV/print/ICS outputs include term, campus, scope, version, approval status, timestamp, and privacy-filtered fields. Exports are tested for timetable readability, timezone/day correctness, and accessible print contrast. |

### P2 — bounded improvements after the comparison is stable

| Gap / proposed control | Rationale | Acceptance criteria |
|---|---|---|
| Privacy-filtered read-only schedule portal | A simple publication view can reduce duplicate files while preserving central control. | Only the approved version is visible; fields are approved by the data owner/UDPO; no solver internals, draft comments, uploads, or hidden identifiers are exposed. |
| What-if scenario workspace | Controlled clones can answer practical questions without changing official data. | A scenario always references its parent revision, records changed inputs, receives a distinct hash, and cannot be mistaken for an approved term schedule. |
| Task-based usability and accessibility study | The intended users are professors, reviewers, and scheduling personnel; their performance and comprehension should be evaluated. | REC requirements are satisfied first. The study uses predefined tasks and measures completion, errors, time, and feedback; findings are reported with sample limitations and no claim of institution-wide representativeness. |
| Data-exchange interfaces | ARO or HR integration could reduce re-entry, but it expands security and governance scope. | Integration begins only with a named owner, documented schema, least-privilege service identity, error/reconciliation path, data-sharing approval, and rollback plan. Manual XLSX remains available. |

These priorities add only the fixed maximum of 50 students per section and
combined meeting. They do not add variable room-capacity, chair, floor-space,
or seat-utilization optimization, walking distance, individual enrollment
conflict optimization, examinations, instructor assignment, multi-campus travel,
or an AI chatbot. Those remain outside the agreed thesis baseline.

## Institutional gates

The following are stop/go gates for claims and pilots. Passing a software test does not satisfy an institutional gate.

| Gate | Required evidence | Stop condition |
|---|---|---|
| Governance and rule validation | Signed constraint catalog, objective profile, room-authorization matrix, responsibility/approval workflow, and target-term scope from authorized USM owners | Use only synthetic data and describe the result as a prototype if owners or approvals are missing. |
| Real-data authorization | Written authorization specifying dataset, users, purpose, environment, reporting, retention, and disposal | Do not import, copy, or benchmark real semester data without authorization. Do not commit raw data or uploads to Git. |
| Privacy and security | UDPO-reviewed PIA/compliance record, processing/registration determination, access model, retention schedule, incident contacts, and successful backup/restore evidence | Do not conduct an institutional pilot or expose the service beyond the approved environment. |
| Accessibility | WCAG 2.2 AA test record for critical workflows and verified handling of accessible-room metadata | Do not label the product accessible or production-ready while critical A/AA defects remain. |
| Brand and identity | Written authorization for seal/name presentation and approved asset/palette/usage rules | Retain the thesis-prototype disclaimer and avoid implying endorsement. |
| Research ethics | REC determination/certification and approved protocol/consent materials where applicable; current USM thesis and AI-disclosure forms | Do not recruit participants, conduct interviews/usability tests, or use protected research data before the required determination. |
| Experiment readiness | Frozen snapshot/objective/configuration hashes, authorized de-identified dataset, preprocessing validation, hardware manifest, seeds, time budget, and preregistered analysis | Do not claim an algorithm winner from development runs, synthetic fixtures, unmatched instances, or incomplete trials. |

## Official and primary source list

### University of Southern Mindanao

- [About USM](https://www.usm.edu.ph/about-usm/)
- [Mandates, Vision and Mission](https://www.usm.edu.ph/about-usm/mandates-vision-mission/)
- [Quality Policy](https://www.usm.edu.ph/about-usm/quality-policy/)
- [Resource Generation and Entrepreneurial Services — GREAT values](https://www.usm.edu.ph/resource-generation-entrepreneurial-services/)
- [USM Courses](https://www.usm.edu.ph/courses/)
- [USM Adds BS Information Technology beginning AY 2026-2027](https://www.usm.edu.ph/usm-adds-bs-information-technology-to-degree-offerings-secures-copcs-for-three-academic-programs/)
- [ISO 9001 Quality Management System](https://www.usm.edu.ph/administration/iso-9001-quality-management-system/)
- [Citizen's Charter landing page](https://www.usm.edu.ph/citizens-charter/)
- [Updated Enrollment Schedule and Start of Classes, AY 2025-2026](https://www.usm.edu.ph/updated-enrollment-schedule-and-start-of-classes-a-y-2025-2026/)
- [Announcements](https://www.usm.edu.ph/usmians/announcements/)
- [Historical CEIT schedule, AY 2021-2022](https://www.usm.edu.ph/wp-content/uploads/2022/04/ceit.pdf)
- [Historical College of Education schedule, AY 2021-2022](https://www.usm.edu.ph/wp-content/uploads/2022/04/ced.pdf)
- [University Data Protection Office, notices, and incident policy](https://www.usm.edu.ph/administration/university-data-protection-office/)
- [USM Seal](https://www.usm.edu.ph/about-usm/usm-seal/)
- [Official USM seal asset](https://www.usm.edu.ph/wp-content/uploads/2024/09/usm_logo_Aug-2024.png)
- [USM consultation on proper trademark and logo use](https://www.usm.edu.ph/usm-ipttbdo-convenes-consultation-meeting-on-proper-use-of-university-trademark-and-logo-2/)
- [USM REC orientation for undergraduate researchers](https://www.usm.edu.ph/usm-research-ethics-committee-conducts-university-wide-orientation-for-undergraduate-researchers/)
- [USM RDE Link Center — ethics and thesis resources](https://www.usm.edu.ph/rde-link-center/)
- [USM UQAC faculty evidence page](https://uqac.usm.edu.ph/course/view.php?id=839) — contextual evidence only; not treated here as a university-wide workload policy

### Philippine government privacy, legal, and accessibility sources

- [Republic Act No. 10173, Data Privacy Act of 2012 — National Privacy Commission](https://privacy.gov.ph/data-privacy-act/)
- [National Privacy Commission Circular No. 2016-01 — Security of Personal Data in Government Agencies](https://privacy.gov.ph/npc-circular-16-01-security-of-personal-data-in-government-agencies/)
- [National Privacy Commission registration FAQ](https://privacy.gov.ph/pips-and-pics/faqs/)
- [Batas Pambansa Blg. 344, Accessibility Law — National Council on Disability Affairs](https://ncda.gov.ph/disability-laws/batas-pambansa/batas-pambansa-blg-344/)
- [2024 revised implementing rules for Batas Pambansa Blg. 344 — National Council on Disability Affairs](https://ncda.gov.ph/revised-2024-rules-and-regulations-implementing-batas-pambansa-344/)

### Web accessibility standard

- [W3C Web Content Accessibility Guidelines 2.2](https://www.w3.org/TR/WCAG22/)

Sources should be rechecked before final submission because web pages, office responsibilities, program names, and policies can change. The signed institutional documents used in the actual study should take precedence over this public desk review and should be versioned in the private research record.
