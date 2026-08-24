"""Create a deterministic, fully de-identified demonstration semester."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from django.core.management.base import BaseCommand, CommandError
from openpyxl import load_workbook

from scheduler import models
from scheduler.services.imports import build_import_template, commit_import, preview_workbook

DEMO_OBJECTIVE_NAME = "USM Demo Default"
DEMO_COLLEGE_CODE = "CSM"


def _canonicalize_xlsx(content: bytes) -> bytes:
    """Normalize ZIP member order/timestamps so equal demo data yield equal bytes."""

    source = ZipFile(BytesIO(content), "r")
    output = BytesIO()
    with source, ZipFile(output, "w", ZIP_DEFLATED, compresslevel=9) as target:
        for name in sorted(source.namelist()):
            original = source.getinfo(name)
            info = ZipInfo(filename=name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = original.external_attr
            info.internal_attr = original.internal_attr
            info.create_system = 0
            payload = source.read(name)
            if name == "docProps/core.xml":
                payload = re.sub(
                    rb"(<dcterms:modified[^>]*>).*?(</dcterms:modified>)",
                    lambda match: (
                        match.group(1)
                        + b"2000-01-01T00:00:00Z"
                        + match.group(2)
                    ),
                    payload,
                )
            target.writestr(info, payload)
    return output.getvalue()


def build_demo_workbook_bytes(*, campus: str = "Kabacan") -> bytes:
    """Build the canonical semester workbook entirely in memory."""

    workbook = load_workbook(BytesIO(build_import_template()))
    fixed_timestamp = datetime(2000, 1, 1)
    workbook.properties.created = fixed_timestamp
    workbook.properties.modified = fixed_timestamp
    rows: dict[str, list[list[object]]] = {
        "Colleges": [[DEMO_COLLEGE_CODE, "College of Science and Mathematics", True]],
        "Departments": [["DCS", "Department of Computer Science", DEMO_COLLEGE_CODE, True]],
        "Programs": [["BSCS", "BS Computer Science", "DCS", "2026", True]],
        "Subjects": [
            ["CS101", "Introduction to Computing", "De-identified demonstration subject", True],
            ["CS102", "Computer Programming Laboratory", "De-identified laboratory subject", True],
        ],
        "ProgramSubjects": [
            ["BSCS", "CS101", "2026", "MAJOR", DEMO_COLLEGE_CODE, "DCS", True],
            ["BSCS", "CS102", "2026", "MAJOR", DEMO_COLLEGE_CODE, "DCS", True],
        ],
        "Sections": [["BSCS-1A", "BSCS", 1, "INCOMING", True]],
        "Instructors": [
            ["DEMO-FAC-001", "Demo Faculty A", "DCS", True, False],
            ["DEMO-FAC-002", "Demo Faculty B", "DCS", True, True],
        ],
        "Rooms": [
            ["CSM-101", "Demo Classroom", campus, "CLASSROOM", DEMO_COLLEGE_CODE, "", True, False],
            ["CSM-LAB", "Demo Computer Laboratory", campus, "LABORATORY", "", "DCS", True, True],
        ],
        "Capabilities": [["COMPUTER_LAB", "Computer laboratory", "Demo capability"]],
        "RoomCapabilities": [["CSM-LAB", "COMPUTER_LAB"]],
        "RoomAuthorizations": [
            ["CSM-101", "MAJOR", DEMO_COLLEGE_CODE, "", "College major-subject room"],
            ["CSM-LAB", "MAJOR", "", "DCS", "Department major-subject laboratory"],
        ],
        "TimeSlots": [],
        "CourseOfferings": [
            ["DEMO-CS101-1A", "CS101", "DCS", True],
            ["DEMO-CS102-1A", "CS102", "DCS", True],
        ],
        "OfferingSections": [
            ["DEMO-CS101-1A", "BSCS-1A", "BSCS", "CS101", "2026"],
            ["DEMO-CS102-1A", "BSCS-1A", "BSCS", "CS102", "2026"],
        ],
        "OfferingInstructors": [
            ["DEMO-CS101-1A", "DEMO-FAC-001"],
            ["DEMO-CS102-1A", "DEMO-FAC-002"],
        ],
        "MeetingRequirements": [
            ["DEMO-CS101-LEC-1", "DEMO-CS101-1A", "LECTURE", 1, 2, "", True],
            ["DEMO-CS102-LAB-1", "DEMO-CS102-1A", "LAB", 1, 2, "", True],
        ],
        "MeetingCapabilities": [["DEMO-CS102-LAB-1", "COMPUTER_LAB"]],
        "Students": [["demo-anon-001", "ACTIVE"]],
        "StudentSections": [["demo-anon-001", "BSCS-1A"]],
        "InstructorPreferences": [
            ["DEMO-FAC-001", 0, 0, "PREFERRED", 1],
            ["DEMO-FAC-002", 1, 2, "AVOID", 1],
        ],
        "InstructorAvailability": [
            ["DEMO-FAC-001", 0, 0, True],
            ["DEMO-FAC-001", 0, 1, True],
        ],
        "RoomAvailability": [
            ["CSM-101", 0, 0, True],
            ["CSM-101", 0, 1, True],
        ],
        "LaboratoryProfiles": [["CSM-LAB", "Computer", "Demo teaching laboratory"]],
    }
    for day in (0, 1):
        for sequence, (starts_at, ends_at) in enumerate(
            (("08:00", "08:30"), ("08:30", "09:00"), ("09:00", "09:30"), ("09:30", "10:00"))
        ):
            rows["TimeSlots"].append([day, sequence, starts_at, ends_at, False, True])
    for sheet_name, sheet_rows in rows.items():
        for row in sheet_rows:
            workbook[sheet_name].append(row)
    output = BytesIO()
    workbook.save(output)
    return _canonicalize_xlsx(output.getvalue())


def _configure_user(
    *,
    username: str,
    role: str,
    password: str | None,
    is_staff: bool = False,
    is_superuser: bool = False,
) -> models.User:
    user, created = models.User.objects.get_or_create(username=username)
    user.role = role
    user.is_active = True
    user.is_staff = is_staff
    user.is_superuser = is_superuser
    if password:
        user.set_password(password)
    elif created:
        user.set_unusable_password()
    user.save()
    return user


class Command(BaseCommand):
    help = "Seed a deterministic, de-identified semester using the validated in-memory XLSX pipeline."

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("--academic-year", default="2026-2027")
        parser.add_argument("--semester", choices=models.Semester.values, default=models.Semester.FIRST)
        parser.add_argument("--campus", default="Kabacan")
        parser.add_argument("--admin-username", default="demo-admin")
        parser.add_argument("--central-username", default="demo-scheduler")
        parser.add_argument("--reviewer-username", default="demo-reviewer")
        parser.add_argument("--admin-password", default=None)
        parser.add_argument("--central-password", default=None)
        parser.add_argument("--reviewer-password", default=None)

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        usernames = {
            options["admin_username"],
            options["central_username"],
            options["reviewer_username"],
        }
        if len(usernames) != 3:
            raise CommandError("Admin, central scheduler, and reviewer usernames must be distinct.")

        admin = _configure_user(
            username=options["admin_username"],
            role=models.UserRole.SYSTEM_ADMIN,
            password=options["admin_password"],
            is_staff=True,
            is_superuser=True,
        )
        central = _configure_user(
            username=options["central_username"],
            role=models.UserRole.CENTRAL_SCHEDULER,
            password=options["central_password"],
        )
        reviewer = _configure_user(
            username=options["reviewer_username"],
            role=models.UserRole.COLLEGE_REVIEWER,
            password=options["reviewer_password"],
        )
        term, _ = models.AcademicTerm.objects.get_or_create(
            academic_year=options["academic_year"],
            semester=options["semester"],
            campus=options["campus"],
            defaults={
                "starts_on": date(2026, 8, 1),
                "ends_on": date(2026, 12, 20),
                "status": models.TermStatus.ACTIVE,
            },
        )
        content = build_demo_workbook_bytes(campus=term.campus)
        batch = preview_workbook(content, term=term, user=central)
        if batch.status == models.ImportStatus.INVALID:
            diagnostics = "; ".join(
                f"{error.sheet_name}:{error.row_number or '-'} {error.code} {error.message}"
                for error in batch.errors.all()[:10]
            )
            raise CommandError(f"Canonical demo workbook unexpectedly failed validation: {diagnostics}")
        if batch.committed_revision_id:
            revision = batch.committed_revision
        else:
            expected_content_hash = models.canonical_sha256(
                {
                    "schema_version": batch.summary["schema_version"],
                    "term_id": term.pk,
                    "sheets": batch.summary["sheets"],
                }
            )
            revision = models.TermDatasetRevision.objects.filter(
                term=term,
                content_hash=expected_content_hash,
            ).first()
            if revision is None:
                revision = commit_import(batch, user=central)
            else:
                # The one-to-one source link remains with the original committed
                # batch. This duplicate normalized dataset is retained only as a
                # cancelled preview for auditability.
                batch.status = models.ImportStatus.CANCELLED
                batch.save(update_fields=["status", "updated_at"])

        college = models.College.objects.get(code=DEMO_COLLEGE_CODE)
        models.UserCollegeScope.objects.get_or_create(user=reviewer, college=college)
        objective = models.ObjectiveProfile.objects.filter(
            name=DEMO_OBJECTIVE_NAME,
            version=1,
            term=term,
        ).first()
        if objective is None:
            objective = models.ObjectiveProfile(
                name=DEMO_OBJECTIVE_NAME,
                version=1,
                term=term,
                is_approved=True,
                approved_by=central,
            )
            objective.save()

        identifiers = {
            "admin_user_id": admin.pk,
            "central_user_id": central.pk,
            "reviewer_user_id": reviewer.pk,
            "term_id": term.pk,
            "import_batch_id": batch.pk,
            "revision_id": revision.pk,
            "objective_profile_id": objective.pk,
            "review_college_id": college.pk,
        }
        self.stdout.write(json.dumps(identifiers, sort_keys=True))


__all__ = ["DEMO_COLLEGE_CODE", "DEMO_OBJECTIVE_NAME", "build_demo_workbook_bytes"]
