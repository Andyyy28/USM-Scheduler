from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

SHA256_VALIDATOR = RegexValidator(
    regex=r"^[0-9a-f]{64}$",
    message="Enter a lowercase 64-character SHA-256 digest.",
)


def canonical_sha256(value: Any) -> str:
    """Return a reproducible digest for JSON-compatible research artifacts."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def default_objective_weights() -> dict[str, int]:
    return {
        "instructor_preference": 1,
        "section_internal_gaps": 1,
        "instructor_internal_gaps": 1,
        "daily_load_imbalance": 1,
    }


def default_objective_definitions() -> dict[str, str]:
    return {
        "instructor_preference": "Penalty for avoid atoms and for missing declared preferred atoms.",
        "section_internal_gaps": "Unoccupied schedulable atoms inside each section-day span.",
        "instructor_internal_gaps": "Unoccupied schedulable atoms inside each instructor-day span.",
        "daily_load_imbalance": "Integer absolute deviation from each resource's daily load target.",
    }


def default_objective_normalizers() -> dict[str, int]:
    return {name: 1 for name in default_objective_weights()}


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


IMMUTABLE_REVISION_STATUSES = ("COMMITTED", "SUPERSEDED")


class RevisionBoundModel(TimestampedModel):
    """Reject writes to rows that form a committed revision's source data."""

    revision_path = "revision"

    class Meta:
        abstract = True

    def _related_revision(self):
        value: Any = self
        for part in self.revision_path.split("."):
            value = getattr(value, part, None)
            if value is None:
                return None
        return value

    def _assert_revision_mutable(self) -> None:
        candidates = [self]
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous is not None:
                candidates.append(previous)
        for candidate in candidates:
            revision = candidate._related_revision()
            if revision is not None and revision.status in IMMUTABLE_REVISION_STATUSES:
                raise ValidationError(
                    "Rows belonging to a committed or superseded dataset revision are immutable."
                )

    def save(self, *args: Any, **kwargs: Any) -> None:
        self._assert_revision_mutable()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        self._assert_revision_mutable()
        return super().delete(*args, **kwargs)


class HistoricalCatalogModel(TimestampedModel):
    """Freeze catalog semantics after a committed revision references a row."""

    committed_revision_lookups: tuple[str, ...] = ()
    historical_field_names: tuple[str, ...] | None = None

    class Meta:
        abstract = True

    def _has_committed_reference(self) -> bool:
        if not self.pk or not self.committed_revision_lookups:
            return False
        reference_query = Q()
        for lookup in self.committed_revision_lookups:
            reference_query |= Q(
                **{f"{lookup}__status__in": IMMUTABLE_REVISION_STATUSES}
            )
        return type(self).objects.filter(pk=self.pk).filter(reference_query).exists()

    def _historical_content(self) -> tuple[Any, ...]:
        names = self.historical_field_names
        fields = [
            field
            for field in self._meta.concrete_fields
            if not field.primary_key and field.name not in {"created_at", "updated_at"}
        ]
        if names is not None:
            fields = [field for field in fields if field.name in names]
        return tuple(getattr(self, field.attname) for field in fields)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk and self._has_committed_reference():
            previous = type(self).objects.get(pk=self.pk)
            if self._historical_content() != previous._historical_content():
                raise ValidationError(
                    "Catalog data referenced by a committed dataset revision cannot be changed."
                )
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if self._has_committed_reference():
            raise ValidationError(
                "Catalog data referenced by a committed dataset revision cannot be deleted."
            )
        return super().delete(*args, **kwargs)


class UserRole(models.TextChoices):
    SYSTEM_ADMIN = "SYSTEM_ADMIN", "System administrator"
    CENTRAL_SCHEDULER = "CENTRAL_SCHEDULER", "Central scheduler"
    COLLEGE_REVIEWER = "COLLEGE_REVIEWER", "College reviewer"


class User(AbstractUser):
    role = models.CharField(max_length=24, choices=UserRole.choices, default=UserRole.COLLEGE_REVIEWER)

    class Meta(AbstractUser.Meta):
        indexes = [models.Index(fields=["role", "is_active"])]

    def __str__(self) -> str:
        return self.get_full_name() or self.username


class College(HistoricalCatalogModel):
    committed_revision_lookups = (
        "room_authorizations__revision",
        "authoritative_program_subjects__offering_section_links__offering__revision",
        "departments__course_offerings__revision",
        "departments__programs__sections__revision",
        "owned_rooms__availability_profiles__revision",
    )
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        indexes = [models.Index(fields=["is_active", "code"])]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class UserCollegeScope(TimestampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="college_scopes")
    college = models.ForeignKey(College, on_delete=models.CASCADE, related_name="user_scopes")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "college"], name="uniq_user_college_scope"),
        ]
        ordering = ["college__code", "user__username"]

    def clean(self) -> None:
        super().clean()
        if self.user_id and self.user.role != UserRole.COLLEGE_REVIEWER:
            raise ValidationError({"user": "Only college reviewers require college scope rows."})

    def __str__(self) -> str:
        return f"{self.user} / {self.college.code}"


class Department(HistoricalCatalogModel):
    committed_revision_lookups = (
        "room_authorizations__revision",
        "authoritative_program_subjects__offering_section_links__offering__revision",
        "course_offerings__revision",
        "programs__sections__revision",
        "instructors__availability_profiles__revision",
        "owned_rooms__availability_profiles__revision",
    )
    college = models.ForeignKey(College, on_delete=models.PROTECT, related_name="departments")
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["college__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["college", "name"], name="uniq_department_name_per_college"),
        ]
        indexes = [models.Index(fields=["college", "is_active"])]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class Program(HistoricalCatalogModel):
    committed_revision_lookups = (
        "sections__revision",
        "program_subjects__offering_section_links__offering__revision",
    )
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="programs")
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=200)
    curriculum_label = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["department", "name"], name="uniq_program_name_per_department"),
        ]
        indexes = [models.Index(fields=["department", "is_active"])]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class SubjectClassification(models.TextChoices):
    MAJOR = "MAJOR", "Major"
    MINOR = "MINOR", "Minor"
    GENERAL_EDUCATION = "GE", "General education"


class Subject(HistoricalCatalogModel):
    committed_revision_lookups = ("course_offerings__revision",)
    code = models.CharField(max_length=30, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        indexes = [models.Index(fields=["is_active", "code"])]

    def __str__(self) -> str:
        return f"{self.code} - {self.title}"


class ProgramSubject(HistoricalCatalogModel):
    committed_revision_lookups = ("offering_section_links__offering__revision",)
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="program_subjects")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="program_subjects")
    curriculum_version = models.CharField(max_length=80)
    classification = models.CharField(max_length=10, choices=SubjectClassification.choices)
    authoritative_college = models.ForeignKey(
        College,
        on_delete=models.PROTECT,
        related_name="authoritative_program_subjects",
    )
    authoritative_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="authoritative_program_subjects",
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["program__code", "curriculum_version", "subject__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "subject", "curriculum_version"],
                name="uniq_program_subject_curriculum",
            ),
        ]
        indexes = [
            models.Index(fields=["classification", "authoritative_college"]),
            models.Index(fields=["program", "is_active"]),
        ]

    def clean(self) -> None:
        super().clean()
        if (
            self.authoritative_department_id
            and self.authoritative_college_id
            and self.authoritative_department.college_id != self.authoritative_college_id
        ):
            raise ValidationError(
                {"authoritative_department": "The authoritative department must belong to the college."}
            )

    def __str__(self) -> str:
        return f"{self.program.code}: {self.subject.code} ({self.classification})"


class Semester(models.TextChoices):
    FIRST = "FIRST", "First semester"
    SECOND = "SECOND", "Second semester"
    MIDYEAR = "MIDYEAR", "Midyear"


class TermStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    CLOSED = "CLOSED", "Closed"
    ARCHIVED = "ARCHIVED", "Archived"


class AcademicTerm(HistoricalCatalogModel):
    committed_revision_lookups = ("dataset_revisions",)
    historical_field_names = (
        "academic_year",
        "semester",
        "campus",
        "starts_on",
        "ends_on",
    )
    academic_year = models.CharField(max_length=9, help_text="For example: 2026-2027")
    semester = models.CharField(max_length=10, choices=Semester.choices)
    campus = models.CharField(max_length=120)
    starts_on = models.DateField()
    ends_on = models.DateField()
    status = models.CharField(max_length=10, choices=TermStatus.choices, default=TermStatus.DRAFT)

    class Meta:
        ordering = ["-starts_on", "campus"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "semester", "campus"],
                name="uniq_academic_term",
            ),
            models.CheckConstraint(condition=Q(ends_on__gt=F("starts_on")), name="term_end_after_start"),
        ]
        indexes = [models.Index(fields=["campus", "status"])]

    def clean(self) -> None:
        super().clean()
        if self.starts_on and self.ends_on and self.ends_on <= self.starts_on:
            raise ValidationError({"ends_on": "The term end date must be after its start date."})

    def __str__(self) -> str:
        return f"{self.academic_year} {self.get_semester_display()} - {self.campus}"


class RevisionStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    VALIDATED = "VALIDATED", "Validated"
    COMMITTED = "COMMITTED", "Committed"
    SUPERSEDED = "SUPERSEDED", "Superseded"


class TermDatasetRevision(TimestampedModel):
    term = models.ForeignKey(AcademicTerm, on_delete=models.PROTECT, related_name="dataset_revisions")
    revision_number = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=RevisionStatus.choices, default=RevisionStatus.DRAFT)
    label = models.CharField(max_length=160, blank=True)
    content_hash = models.CharField(max_length=64, validators=[SHA256_VALIDATOR], blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_term_revisions",
    )
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["term", "-revision_number"]
        constraints = [
            models.UniqueConstraint(fields=["term", "revision_number"], name="uniq_term_revision_number"),
            models.UniqueConstraint(
                fields=["term", "content_hash"],
                condition=~Q(content_hash=""),
                name="uniq_term_revision_content_hash",
            ),
        ]
        indexes = [models.Index(fields=["term", "status"])]

    @property
    def is_immutable(self) -> bool:
        return self.status in {RevisionStatus.COMMITTED, RevisionStatus.SUPERSEDED}

    def clean(self) -> None:
        super().clean()
        if self.status in {RevisionStatus.COMMITTED, RevisionStatus.SUPERSEDED} and not self.content_hash:
            raise ValidationError({"content_hash": "Committed revisions require a content hash."})
        if self.status == RevisionStatus.COMMITTED and not self.committed_at:
            self.committed_at = timezone.now()

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and previous.is_immutable:
                content_snapshot = (self.term_id, self.revision_number, self.label, self.content_hash)
                previous_content = (
                    previous.term_id,
                    previous.revision_number,
                    previous.label,
                    previous.content_hash,
                )
                allowed_statuses = {previous.status}
                if previous.status == RevisionStatus.COMMITTED:
                    allowed_statuses.add(RevisionStatus.SUPERSEDED)
                if content_snapshot != previous_content or self.status not in allowed_statuses:
                    raise ValidationError("Committed or superseded dataset revisions are immutable.")
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if self.is_immutable:
            raise ValidationError("Committed or superseded dataset revisions cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.term} / revision {self.revision_number}"


class CohortStatus(models.TextChoices):
    INCOMING = "INCOMING", "Incoming"
    CONTINUING = "CONTINUING", "Continuing"
    GRADUATING = "GRADUATING", "Graduating"


class Section(RevisionBoundModel):
    revision = models.ForeignKey(TermDatasetRevision, on_delete=models.CASCADE, related_name="sections")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="sections")
    code = models.CharField(max_length=60)
    year_level = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    cohort_status = models.CharField(max_length=12, choices=CohortStatus.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["program__code", "year_level", "code"]
        constraints = [
            models.UniqueConstraint(fields=["revision", "code"], name="uniq_section_code_per_revision"),
            models.CheckConstraint(
                condition=Q(year_level__gte=1) & Q(year_level__lte=10),
                name="section_year_level_range",
            ),
        ]
        indexes = [models.Index(fields=["revision", "program", "is_active"])]

    @property
    def term(self) -> AcademicTerm:
        return self.revision.term

    def __str__(self) -> str:
        return f"{self.code} ({self.program.code})"


class StudentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    GRADUATED = "GRADUATED", "Graduated"


class Student(HistoricalCatalogModel):
    committed_revision_lookups = ("section_memberships__section__revision",)
    pseudonymous_code = models.CharField(max_length=80, unique=True)
    status = models.CharField(max_length=10, choices=StudentStatus.choices, default=StudentStatus.ACTIVE)

    class Meta:
        ordering = ["pseudonymous_code"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self) -> str:
        return self.pseudonymous_code


class StudentSectionMembership(RevisionBoundModel):
    revision_path = "section.revision"
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="section_memberships")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="student_memberships")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "section"], name="uniq_student_section_membership"),
        ]

    def __str__(self) -> str:
        return f"{self.student} / {self.section}"


class Instructor(HistoricalCatalogModel):
    committed_revision_lookups = (
        "availability_profiles__revision",
        "offering_links__offering__revision",
    )
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="instructors")
    employee_code = models.CharField(max_length=80, unique=True)
    display_name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_name"]
        indexes = [models.Index(fields=["department", "is_active"])]

    def __str__(self) -> str:
        return f"{self.employee_code} - {self.display_name}"


class RoomKind(models.TextChoices):
    CLASSROOM = "CLASSROOM", "Classroom"
    LABORATORY = "LABORATORY", "Laboratory"
    SPECIAL = "SPECIAL", "Special-purpose room"


class Room(HistoricalCatalogModel):
    committed_revision_lookups = (
        "availability_profiles__revision",
        "authorizations__revision",
    )
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160, blank=True)
    campus = models.CharField(max_length=120)
    kind = models.CharField(max_length=12, choices=RoomKind.choices, default=RoomKind.CLASSROOM)
    owning_college = models.ForeignKey(
        College,
        on_delete=models.PROTECT,
        related_name="owned_rooms",
        null=True,
        blank=True,
    )
    owning_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="owned_rooms",
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["campus", "code"]
        constraints = [
            models.UniqueConstraint(fields=["campus", "code"], name="uniq_room_code_per_campus"),
            models.CheckConstraint(
                condition=(Q(owning_college__isnull=False) & Q(owning_department__isnull=True))
                | (Q(owning_college__isnull=True) & Q(owning_department__isnull=False)),
                name="room_exactly_one_owner",
            ),
        ]
        indexes = [models.Index(fields=["campus", "kind", "is_active"])]

    @property
    def owner_college(self) -> College:
        if self.owning_college_id:
            return self.owning_college
        return self.owning_department.college

    def clean(self) -> None:
        super().clean()
        if bool(self.owning_college_id) == bool(self.owning_department_id):
            raise ValidationError("A room must have exactly one college or department owner.")

    def __str__(self) -> str:
        return f"{self.code} - {self.name}" if self.name else self.code


class LaboratoryProfile(HistoricalCatalogModel):
    committed_revision_lookups = ("room__availability_profiles__revision",)
    room = models.OneToOneField(Room, on_delete=models.CASCADE, related_name="laboratory_profile")
    laboratory_type = models.CharField(max_length=100)
    notes = models.TextField(blank=True)

    def clean(self) -> None:
        super().clean()
        if self.room_id and self.room.kind != RoomKind.LABORATORY:
            raise ValidationError({"room": "Only a laboratory room can have a laboratory profile."})

    def __str__(self) -> str:
        return f"{self.room.code} / {self.laboratory_type}"


class Capability(HistoricalCatalogModel):
    committed_revision_lookups = (
        "meeting_requirement_links__meeting_requirement__offering__revision",
        "room_links__room__availability_profiles__revision",
    )
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class RoomCapability(HistoricalCatalogModel):
    committed_revision_lookups = ("room__availability_profiles__revision",)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="capability_links")
    capability = models.ForeignKey(Capability, on_delete=models.PROTECT, related_name="room_links")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["room", "capability"], name="uniq_room_capability"),
        ]

    def __str__(self) -> str:
        return f"{self.room.code}: {self.capability.code}"


class RoomAuthorization(RevisionBoundModel):
    revision = models.ForeignKey(
        TermDatasetRevision,
        on_delete=models.CASCADE,
        related_name="room_authorizations",
    )
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="authorizations")
    classification = models.CharField(max_length=10, choices=SubjectClassification.choices)
    college = models.ForeignKey(
        College,
        on_delete=models.CASCADE,
        related_name="room_authorizations",
        null=True,
        blank=True,
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="room_authorizations",
        null=True,
        blank=True,
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(college__isnull=False) & Q(department__isnull=True))
                | (Q(college__isnull=True) & Q(department__isnull=False)),
                name="room_auth_exactly_one_unit",
            ),
            models.UniqueConstraint(
                fields=["revision", "room", "classification", "college"],
                condition=Q(department__isnull=True),
                name="uniq_room_auth_college",
            ),
            models.UniqueConstraint(
                fields=["revision", "room", "classification", "department"],
                condition=Q(college__isnull=True),
                name="uniq_room_auth_department",
            ),
        ]
        indexes = [models.Index(fields=["revision", "classification", "room"])]

    @property
    def authorized_unit(self) -> College | Department:
        return self.college or self.department

    def clean(self) -> None:
        super().clean()
        if bool(self.college_id) == bool(self.department_id):
            raise ValidationError("An authorization must target exactly one college or department.")
        if self.revision_id and self.room_id and self.room.campus != self.revision.term.campus:
            raise ValidationError({"room": "The room must be on the academic term's campus."})

    def __str__(self) -> str:
        return f"{self.room.code} -> {self.authorized_unit} ({self.classification})"


class Weekday(models.IntegerChoices):
    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class TimeSlot(RevisionBoundModel):
    revision = models.ForeignKey(TermDatasetRevision, on_delete=models.CASCADE, related_name="time_slots")
    day = models.PositiveSmallIntegerField(choices=Weekday.choices)
    sequence = models.PositiveSmallIntegerField(help_text="Zero-based order of the atom within the day.")
    starts_at = models.TimeField()
    ends_at = models.TimeField()
    is_break = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["revision", "day", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["revision", "day", "sequence"], name="uniq_timeslot_sequence"),
            models.UniqueConstraint(fields=["revision", "day", "starts_at"], name="uniq_timeslot_start"),
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="timeslot_end_after_start"),
            models.CheckConstraint(condition=Q(day__gte=0) & Q(day__lte=6), name="timeslot_valid_day"),
        ]
        indexes = [models.Index(fields=["revision", "day", "is_active", "is_break"])]

    def clean(self) -> None:
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The slot end time must be after its start time."})

    def __str__(self) -> str:
        return f"{self.get_day_display()} {self.starts_at:%H:%M}-{self.ends_at:%H:%M}"


class InstructorAvailabilityProfile(RevisionBoundModel):
    revision = models.ForeignKey(
        TermDatasetRevision,
        on_delete=models.CASCADE,
        related_name="instructor_availability_profiles",
    )
    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.CASCADE,
        related_name="availability_profiles",
    )
    assume_fully_available = models.BooleanField(default=False)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="acknowledged_instructor_availability",
        null=True,
        blank=True,
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["revision", "instructor"],
                name="uniq_instructor_availability_profile",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.assume_fully_available and not self.acknowledged_by_id:
            raise ValidationError(
                {"acknowledged_by": "An explicit acknowledgement is required for full availability."}
            )
        if self.acknowledged_by_id and not self.acknowledged_at:
            self.acknowledged_at = timezone.now()

    def __str__(self) -> str:
        return f"{self.instructor} / revision {self.revision.revision_number}"


class InstructorAvailability(RevisionBoundModel):
    revision_path = "profile.revision"
    profile = models.ForeignKey(
        InstructorAvailabilityProfile,
        on_delete=models.CASCADE,
        related_name="availability_rows",
    )
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE, related_name="instructor_availability_rows")
    is_available = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profile", "time_slot"], name="uniq_instructor_availability_slot"),
        ]
        indexes = [models.Index(fields=["time_slot", "is_available"])]

    def clean(self) -> None:
        super().clean()
        if (
            self.profile_id
            and self.time_slot_id
            and self.profile.revision_id != self.time_slot.revision_id
        ):
            raise ValidationError({"time_slot": "Availability and time slot must use the same revision."})

    def __str__(self) -> str:
        return f"{self.profile.instructor} / {self.time_slot}: {self.is_available}"


class RoomAvailabilityProfile(RevisionBoundModel):
    revision = models.ForeignKey(
        TermDatasetRevision,
        on_delete=models.CASCADE,
        related_name="room_availability_profiles",
    )
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="availability_profiles")
    assume_fully_available = models.BooleanField(default=False)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="acknowledged_room_availability",
        null=True,
        blank=True,
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["revision", "room"], name="uniq_room_availability_profile"),
        ]

    def clean(self) -> None:
        super().clean()
        if self.revision_id and self.room_id and self.room.campus != self.revision.term.campus:
            raise ValidationError({"room": "The room must be on the term's campus."})
        if self.assume_fully_available and not self.acknowledged_by_id:
            raise ValidationError(
                {"acknowledged_by": "An explicit acknowledgement is required for full availability."}
            )
        if self.acknowledged_by_id and not self.acknowledged_at:
            self.acknowledged_at = timezone.now()

    def __str__(self) -> str:
        return f"{self.room} / revision {self.revision.revision_number}"


class RoomAvailability(RevisionBoundModel):
    revision_path = "profile.revision"
    profile = models.ForeignKey(
        RoomAvailabilityProfile,
        on_delete=models.CASCADE,
        related_name="availability_rows",
    )
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE, related_name="room_availability_rows")
    is_available = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profile", "time_slot"], name="uniq_room_availability_slot"),
        ]
        indexes = [models.Index(fields=["time_slot", "is_available"])]

    def clean(self) -> None:
        super().clean()
        if (
            self.profile_id
            and self.time_slot_id
            and self.profile.revision_id != self.time_slot.revision_id
        ):
            raise ValidationError({"time_slot": "Availability and time slot must use the same revision."})

    def __str__(self) -> str:
        return f"{self.profile.room} / {self.time_slot}: {self.is_available}"


class PreferenceLevel(models.TextChoices):
    PREFERRED = "PREFERRED", "Preferred"
    AVOID = "AVOID", "Avoid"


class InstructorPreference(RevisionBoundModel):
    revision_path = "profile.revision"
    profile = models.ForeignKey(
        InstructorAvailabilityProfile,
        on_delete=models.CASCADE,
        related_name="preferences",
    )
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE, related_name="instructor_preferences")
    level = models.CharField(max_length=10, choices=PreferenceLevel.choices)
    weight = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profile", "time_slot"], name="uniq_instructor_preference_slot"),
            models.CheckConstraint(condition=Q(weight__gte=1), name="preference_weight_positive"),
        ]

    def clean(self) -> None:
        super().clean()
        if (
            self.profile_id
            and self.time_slot_id
            and self.profile.revision_id != self.time_slot.revision_id
        ):
            raise ValidationError({"time_slot": "Preference and time slot must use the same revision."})

    def __str__(self) -> str:
        return f"{self.profile.instructor} / {self.time_slot}: {self.level}"


class CourseOffering(RevisionBoundModel):
    revision = models.ForeignKey(TermDatasetRevision, on_delete=models.CASCADE, related_name="course_offerings")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="course_offerings")
    offering_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="course_offerings",
    )
    external_key = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    sections = models.ManyToManyField(Section, through="OfferingSection", related_name="offerings")
    instructors = models.ManyToManyField(Instructor, through="OfferingInstructor", related_name="offerings")

    class Meta:
        ordering = ["subject__code", "external_key"]
        constraints = [
            models.UniqueConstraint(fields=["revision", "external_key"], name="uniq_offering_external_key"),
        ]
        indexes = [models.Index(fields=["revision", "is_active", "subject"])]

    def __str__(self) -> str:
        return f"{self.external_key}: {self.subject.code}"


class OfferingSection(RevisionBoundModel):
    revision_path = "offering.revision"
    offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE, related_name="section_links")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="offering_links")
    program_subject = models.ForeignKey(
        ProgramSubject,
        on_delete=models.PROTECT,
        related_name="offering_section_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["offering", "section"], name="uniq_offering_section"),
        ]
        indexes = [models.Index(fields=["section", "offering"])]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.offering_id and self.section_id and self.offering.revision_id != self.section.revision_id:
            errors["section"] = "The offering and section must use the same term revision."
        if self.program_subject_id and self.section_id and self.program_subject.program_id != self.section.program_id:
            errors["program_subject"] = "The curriculum row must belong to the section's program."
        if self.program_subject_id and self.offering_id and self.program_subject.subject_id != self.offering.subject_id:
            errors["program_subject"] = "The curriculum row must refer to the offered subject."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.offering} / {self.section}"


class OfferingInstructor(RevisionBoundModel):
    revision_path = "offering.revision"
    offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE, related_name="instructor_links")
    instructor = models.ForeignKey(Instructor, on_delete=models.PROTECT, related_name="offering_links")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["offering", "instructor"], name="uniq_offering_instructor"),
        ]
        indexes = [models.Index(fields=["instructor", "offering"])]

    def __str__(self) -> str:
        return f"{self.offering} / {self.instructor}"


class MeetingComponent(models.TextChoices):
    LECTURE = "LECTURE", "Lecture"
    LABORATORY = "LAB", "Laboratory"
    TUTORIAL = "TUTORIAL", "Tutorial"
    PRACTICUM = "PRACTICUM", "Practicum"
    OTHER = "OTHER", "Other"


class MeetingRequirement(RevisionBoundModel):
    revision_path = "offering.revision"
    offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE, related_name="meeting_requirements")
    stable_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    component = models.CharField(max_length=10, choices=MeetingComponent.choices)
    occurrence_number = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    duration_atoms = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    distinct_day_group = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)
    required_capabilities = models.ManyToManyField(
        Capability,
        through="MeetingRequiredCapability",
        related_name="meeting_requirements",
    )

    class Meta:
        ordering = ["offering", "component", "occurrence_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "component", "occurrence_number"],
                name="uniq_meeting_occurrence",
            ),
            models.CheckConstraint(condition=Q(duration_atoms__gte=1), name="meeting_duration_positive"),
            models.CheckConstraint(condition=Q(occurrence_number__gte=1), name="meeting_occurrence_positive"),
        ]
        indexes = [models.Index(fields=["offering", "is_active"])]

    @property
    def revision(self) -> TermDatasetRevision:
        return self.offering.revision

    def __str__(self) -> str:
        return f"{self.offering.external_key} {self.component} #{self.occurrence_number}"


class MeetingRequiredCapability(RevisionBoundModel):
    revision_path = "meeting_requirement.offering.revision"
    meeting_requirement = models.ForeignKey(
        MeetingRequirement,
        on_delete=models.CASCADE,
        related_name="capability_links",
    )
    capability = models.ForeignKey(
        Capability,
        on_delete=models.PROTECT,
        related_name="meeting_requirement_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["meeting_requirement", "capability"],
                name="uniq_meeting_required_capability",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.meeting_requirement}: {self.capability.code}"


class ImportStatus(models.TextChoices):
    UPLOADED = "UPLOADED", "Uploaded"
    PREVIEWED = "PREVIEWED", "Previewed"
    INVALID = "INVALID", "Invalid"
    COMMITTED = "COMMITTED", "Committed"
    CANCELLED = "CANCELLED", "Cancelled"


class ImportBatch(TimestampedModel):
    term = models.ForeignKey(AcademicTerm, on_delete=models.PROTECT, related_name="import_batches")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="import_batches",
    )
    original_filename = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64, validators=[SHA256_VALIDATOR])
    status = models.CharField(max_length=12, choices=ImportStatus.choices, default=ImportStatus.UPLOADED)
    total_rows = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    summary = models.JSONField(default=dict, blank=True)
    committed_revision = models.OneToOneField(
        TermDatasetRevision,
        on_delete=models.PROTECT,
        related_name="source_import_batch",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["term", "file_hash"], name="uniq_import_file_per_term"),
        ]
        indexes = [models.Index(fields=["term", "status", "created_at"])]

    def clean(self) -> None:
        super().clean()
        if self.status == ImportStatus.COMMITTED and not self.committed_revision_id:
            raise ValidationError({"committed_revision": "A committed import requires a dataset revision."})
        if (
            self.committed_revision_id
            and self.term_id
            and self.committed_revision.term_id != self.term_id
        ):
            raise ValidationError({"committed_revision": "The revision must belong to the imported term."})

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.status})"


class ImportError(TimestampedModel):
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="errors")
    sheet_name = models.CharField(max_length=100)
    row_number = models.PositiveIntegerField(null=True, blank=True)
    column_name = models.CharField(max_length=100, blank=True)
    code = models.CharField(max_length=80)
    message = models.TextField()

    class Meta:
        ordering = ["sheet_name", "row_number", "column_name"]
        indexes = [models.Index(fields=["batch", "sheet_name", "row_number"])]

    def __str__(self) -> str:
        location = f"{self.sheet_name}:{self.row_number or '-'}"
        return f"{location} {self.code}"


class ObjectiveProfile(TimestampedModel):
    name = models.CharField(max_length=160)
    version = models.PositiveIntegerField(default=1)
    term = models.ForeignKey(
        AcademicTerm,
        on_delete=models.PROTECT,
        related_name="objective_profiles",
        null=True,
        blank=True,
    )
    weights = models.JSONField(default=default_objective_weights)
    definitions = models.JSONField(default=dict, blank=True)
    normalization_denominators = models.JSONField(default=dict, blank=True)
    profile_hash = models.CharField(
        max_length=64,
        validators=[SHA256_VALIDATOR],
        editable=False,
        db_index=True,
    )
    is_approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_objective_profiles",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["name", "version", "term"], name="uniq_objective_profile_version"),
        ]
        indexes = [models.Index(fields=["term", "is_approved"])]

    def hash_payload(self) -> dict[str, Any]:
        return {
            "weights": self.weights,
            "definitions": self.definitions,
            "normalization_denominators": self.normalization_denominators,
        }

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        required_components = set(default_objective_weights())
        if not isinstance(self.weights, dict) or not self.weights:
            errors["weights"] = "Weights must be a non-empty JSON object."
        elif any(type(value) is not int or value < 0 for value in self.weights.values()):
            errors["weights"] = "Every objective weight must be a non-negative integer."
        elif self.is_approved and set(self.weights) != required_components:
            errors["weights"] = "Approved profiles require exactly the four documented objective components."
        if self.is_approved:
            self.definitions = {**default_objective_definitions(), **(self.definitions or {})}
            self.normalization_denominators = {
                **default_objective_normalizers(),
                **(self.normalization_denominators or {}),
            }
            if any(
                not isinstance(self.definitions.get(name), str)
                or not self.definitions[name].strip()
                for name in required_components
            ):
                errors["definitions"] = "Every approved objective component requires a definition."
            if any(
                type(self.normalization_denominators.get(name)) is not int
                or self.normalization_denominators[name] <= 0
                for name in required_components
            ):
                errors["normalization_denominators"] = (
                    "Every approved objective component requires a positive integer denominator."
                )
        if self.is_approved and not self.approved_by_id:
            errors["approved_by"] = "Approved profiles require an approver."
        if errors:
            raise ValidationError(errors)
        if self.is_approved and not self.approved_at:
            self.approved_at = timezone.now()

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and previous.is_approved:
                previous_payload = (
                    previous.hash_payload(),
                    previous.name,
                    previous.version,
                    previous.term_id,
                    previous.is_approved,
                    previous.approved_by_id,
                    previous.approved_at,
                )
                current_payload = (
                    self.hash_payload(),
                    self.name,
                    self.version,
                    self.term_id,
                    self.is_approved,
                    self.approved_by_id,
                    self.approved_at,
                )
                if previous_payload != current_payload:
                    raise ValidationError("Approved objective profiles are immutable.")
        self.clean()
        self.profile_hash = canonical_sha256(self.hash_payload())
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


class ProblemSnapshot(TimestampedModel):
    revision = models.ForeignKey(TermDatasetRevision, on_delete=models.PROTECT, related_name="problem_snapshots")
    objective_profile = models.ForeignKey(
        ObjectiveProfile,
        on_delete=models.PROTECT,
        related_name="problem_snapshots",
    )
    schema_version = models.CharField(max_length=30, default="1.0")
    input_data = models.JSONField()
    candidate_map = models.JSONField()
    snapshot_hash = models.CharField(max_length=64, validators=[SHA256_VALIDATOR], unique=True, editable=False)
    event_count = models.PositiveIntegerField(default=0)
    candidate_count = models.PositiveIntegerField(default=0)
    preprocessing_seconds = models.FloatField(default=0, validators=[MinValueValidator(0)])
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_problem_snapshots",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["revision", "created_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(preprocessing_seconds__gte=0), name="snapshot_preprocess_nonnegative"),
        ]

    def hash_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision_id": self.revision_id,
            "objective_profile_hash": self.objective_profile.profile_hash,
            "input_data": self.input_data,
            "candidate_map": self.candidate_map,
        }

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Problem snapshots are immutable; create a new snapshot instead.")
        self.snapshot_hash = canonical_sha256(self.hash_payload())
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Problem snapshots are immutable and cannot be deleted.")

    def __str__(self) -> str:
        return f"{self.revision} / {self.snapshot_hash[:12]}"


class ExperimentStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"
    FAILED = "FAILED", "Failed"


class ExperimentBatch(TimestampedModel):
    name = models.CharField(max_length=200)
    snapshot = models.ForeignKey(ProblemSnapshot, on_delete=models.PROTECT, related_name="experiment_batches")
    status = models.CharField(max_length=10, choices=ExperimentStatus.choices, default=ExperimentStatus.DRAFT)
    seeds = models.JSONField(default=list)
    order_seed = models.PositiveIntegerField(default=0)
    time_limit_seconds = models.PositiveIntegerField(default=300, validators=[MinValueValidator(1)])
    cpu_limit = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    memory_limit_mb = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    configuration = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_experiment_batches",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["snapshot", "status"])]
        constraints = [
            models.CheckConstraint(condition=Q(time_limit_seconds__gte=1), name="experiment_time_positive"),
            models.CheckConstraint(condition=Q(cpu_limit__gte=1), name="experiment_cpu_positive"),
        ]

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.seeds, list) or any(type(seed) is not int or seed < 0 for seed in self.seeds):
            raise ValidationError({"seeds": "Seeds must be a JSON list of non-negative integers."})
        if len(self.seeds) != len(set(self.seeds)):
            raise ValidationError({"seeds": "Experiment seeds must be unique."})

    def _protocol_content(self) -> tuple[Any, ...]:
        return (
            self.name,
            self.snapshot_id,
            self.seeds,
            self.order_seed,
            self.time_limit_seconds,
            self.cpu_limit,
            self.memory_limit_mb,
            self.configuration,
            self.created_by_id,
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if (
                previous
                and previous.runs.exists()
                and self._protocol_content() != previous._protocol_content()
                and not getattr(self, "_allow_protocol_update", False)
            ):
                raise ValidationError(
                    "An experiment protocol is immutable after its run matrix is created."
                )
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if self.pk and (self.runs.exists() or self.status != ExperimentStatus.DRAFT):
            raise ValidationError("Experiment batches with planned runs are immutable.")
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class SolverAlgorithm(models.TextChoices):
    CP_SAT = "CP_SAT", "CP-SAT"
    GENETIC_ALGORITHM = "GA", "Genetic Algorithm"


class RunStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    FEASIBLE = "FEASIBLE", "Feasible"
    OPTIMAL = "OPTIMAL", "Optimal"
    INFEASIBLE = "INFEASIBLE", "Proven infeasible"
    NO_SOLUTION = "NO_SOLUTION", "No feasible solution found"
    TIMEOUT = "TIMEOUT", "Timed out"
    CANCELLED = "CANCELLED", "Cancelled"
    FAILED = "FAILED", "Failed"


class ScheduleRun(TimestampedModel):
    experiment_batch = models.ForeignKey(
        ExperimentBatch,
        on_delete=models.PROTECT,
        related_name="runs",
        null=True,
        blank=True,
    )
    snapshot = models.ForeignKey(ProblemSnapshot, on_delete=models.PROTECT, related_name="runs")
    algorithm = models.CharField(max_length=10, choices=SolverAlgorithm.choices)
    seed = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=RunStatus.choices, default=RunStatus.QUEUED)
    configuration = models.JSONField(default=dict, blank=True)
    task_id = models.CharField(max_length=255, blank=True, db_index=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_schedule_runs",
    )
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    first_feasible_seconds = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    execution_seconds = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    objective_value = models.BigIntegerField(null=True, blank=True)
    best_bound = models.FloatField(null=True, blank=True)
    relative_gap = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    hard_violation_count = models.PositiveIntegerField(default=0)
    stopping_reason = models.CharField(max_length=255, blank=True)
    diagnostics = models.JSONField(default=dict, blank=True)
    result_data = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["snapshot", "algorithm", "status"]),
            models.Index(fields=["experiment_batch", "seed", "algorithm"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment_batch", "algorithm", "seed"],
                condition=Q(experiment_batch__isnull=False),
                name="uniq_experiment_algorithm_seed",
            ),
            models.CheckConstraint(
                condition=Q(execution_seconds__isnull=True) | Q(execution_seconds__gte=0),
                name="run_execution_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(first_feasible_seconds__isnull=True) | Q(first_feasible_seconds__gte=0),
                name="run_first_feasible_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(relative_gap__isnull=True) | Q(relative_gap__gte=0),
                name="run_gap_nonnegative",
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status not in {RunStatus.QUEUED, RunStatus.RUNNING}

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.experiment_batch_id and self.experiment_batch.snapshot_id != self.snapshot_id:
            errors["snapshot"] = "The run must use its experiment batch's snapshot."
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            errors["finished_at"] = "Finish time cannot precede start time."
        if self.algorithm == SolverAlgorithm.GENETIC_ALGORITHM and self.status in {
            RunStatus.OPTIMAL,
            RunStatus.INFEASIBLE,
        }:
            errors["status"] = "The Genetic Algorithm cannot prove optimality or infeasibility."
        if errors:
            raise ValidationError(errors)

    def _result_content(self) -> tuple[Any, ...]:
        return tuple(
            getattr(self, field.attname)
            for field in self._meta.concrete_fields
            if not field.primary_key and field.name not in {"created_at", "updated_at"}
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and previous.is_terminal and self._result_content() != previous._result_content():
                raise ValidationError("Terminal schedule-run evidence is immutable.")
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if self.is_terminal:
            raise ValidationError("Terminal schedule-run evidence cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.get_algorithm_display()} seed {self.seed} ({self.status})"


class ValidationResult(TimestampedModel):
    run = models.OneToOneField(
        ScheduleRun,
        on_delete=models.CASCADE,
        related_name="validation_result",
        null=True,
        blank=True,
    )
    schedule_version = models.OneToOneField(
        "ScheduleVersion",
        on_delete=models.CASCADE,
        related_name="validation_result",
        null=True,
        blank=True,
    )
    is_feasible = models.BooleanField(default=False)
    hard_violation_count = models.PositiveIntegerField(default=0)
    violations = models.JSONField(default=dict)
    raw_soft_penalty = models.BigIntegerField(default=0)
    objective_breakdown = models.JSONField(default=dict)
    normalized_quality_score = models.FloatField(null=True, blank=True)
    validator_version = models.CharField(max_length=30, default="1.0")
    validated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(run__isnull=False) & Q(schedule_version__isnull=True))
                | (Q(run__isnull=True) & Q(schedule_version__isnull=False)),
                name="validation_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=Q(normalized_quality_score__isnull=True)
                | (Q(normalized_quality_score__gte=0) & Q(normalized_quality_score__lte=100)),
                name="validation_quality_range",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if bool(self.run_id) == bool(self.schedule_version_id):
            raise ValidationError("A validation result must target exactly one run or schedule version.")
        if self.is_feasible and self.hard_violation_count != 0:
            raise ValidationError({"hard_violation_count": "A feasible result cannot contain hard violations."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk:
            previous = type(self).objects.select_related("run", "schedule_version").get(pk=self.pk)
            immutable = bool(
                (previous.run_id and previous.run.is_terminal)
                or (
                    previous.schedule_version_id
                    and previous.schedule_version.is_immutable
                )
            )
            if immutable:
                raise ValidationError("Validation evidence for a finalized result is immutable.")
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if (self.run_id and self.run.is_terminal) or (
            self.schedule_version_id and self.schedule_version.is_immutable
        ):
            raise ValidationError("Validation evidence for a finalized result cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        target = self.run or self.schedule_version
        return f"Validation for {target}: {'feasible' if self.is_feasible else 'invalid'}"


class RunMetric(TimestampedModel):
    run = models.ForeignKey(ScheduleRun, on_delete=models.CASCADE, related_name="metrics")
    name = models.CharField(max_length=100)
    value = models.DecimalField(max_digits=24, decimal_places=6)
    unit = models.CharField(max_length=40, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["run", "name"]
        constraints = [
            models.UniqueConstraint(fields=["run", "name"], name="uniq_run_metric"),
        ]
        indexes = [models.Index(fields=["name", "value"])]

    def __str__(self) -> str:
        return f"{self.run}: {self.name}={self.value} {self.unit}".strip()

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk and self.run.is_terminal:
            raise ValidationError("Metrics for terminal schedule runs are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if self.run.is_terminal:
            raise ValidationError("Metrics for terminal schedule runs cannot be deleted.")
        return super().delete(*args, **kwargs)


class ScheduleSource(models.TextChoices):
    IMPORTED = "IMPORTED", "Imported"
    MANUAL = "MANUAL", "Manual"
    CP_SAT = "CP_SAT", "CP-SAT"
    GENETIC_ALGORITHM = "GA", "Genetic Algorithm"


class ScheduleStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    UNDER_REVIEW = "UNDER_REVIEW", "Under review"
    APPROVED = "APPROVED", "Approved"
    ARCHIVED = "ARCHIVED", "Archived"


class ScheduleVersion(TimestampedModel):
    term = models.ForeignKey(AcademicTerm, on_delete=models.PROTECT, related_name="schedule_versions")
    revision = models.ForeignKey(TermDatasetRevision, on_delete=models.PROTECT, related_name="schedule_versions")
    snapshot = models.ForeignKey(
        ProblemSnapshot,
        on_delete=models.PROTECT,
        related_name="schedule_versions",
        null=True,
        blank=True,
    )
    run = models.OneToOneField(
        ScheduleRun,
        on_delete=models.PROTECT,
        related_name="schedule_version",
        null=True,
        blank=True,
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    version_number = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    source = models.CharField(max_length=10, choices=ScheduleSource.choices)
    status = models.CharField(max_length=12, choices=ScheduleStatus.choices, default=ScheduleStatus.DRAFT)
    objective_value = models.BigIntegerField(null=True, blank=True)
    objective_breakdown = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_schedule_versions",
    )
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["term", "-version_number"]
        constraints = [
            models.UniqueConstraint(fields=["term", "version_number"], name="uniq_schedule_version_number"),
            models.UniqueConstraint(
                fields=["term"],
                condition=Q(status=ScheduleStatus.APPROVED),
                name="uniq_approved_schedule_per_term",
            ),
        ]
        indexes = [models.Index(fields=["term", "status", "version_number"])]

    @property
    def is_immutable(self) -> bool:
        return self.status in {ScheduleStatus.APPROVED, ScheduleStatus.ARCHIVED}

    @property
    def assignments_are_immutable(self) -> bool:
        return self.status != ScheduleStatus.DRAFT

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.revision_id and self.term_id and self.revision.term_id != self.term_id:
            errors["revision"] = "The dataset revision must belong to the schedule's term."
        if self.snapshot_id and self.snapshot.revision_id != self.revision_id:
            errors["snapshot"] = "The snapshot must belong to the schedule's dataset revision."
        if self.run_id and self.snapshot_id and self.run.snapshot_id != self.snapshot_id:
            errors["run"] = "The run must use the schedule's problem snapshot."
        if self.parent_id:
            if self.parent_id == self.pk:
                errors["parent"] = "A schedule cannot be its own parent."
            elif self.parent.term_id != self.term_id:
                errors["parent"] = "Parent and child schedules must belong to the same term."
        if self.status == ScheduleStatus.APPROVED and not self.finalized_at:
            self.finalized_at = timezone.now()
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if self.status == ScheduleStatus.APPROVED and (
            previous is None or previous.status != ScheduleStatus.APPROVED
        ) and not getattr(self, "_allow_approval_transition", False):
            raise ValidationError(
                "Schedules may enter APPROVED status only through the approval workflow."
            )
        if previous and previous.is_immutable:
            current_content = (
                self.term_id,
                self.revision_id,
                self.snapshot_id,
                self.run_id,
                self.parent_id,
                self.version_number,
                self.name,
                self.source,
                self.objective_value,
                self.objective_breakdown,
                self.created_by_id,
                self.finalized_at,
            )
            previous_content = (
                previous.term_id,
                previous.revision_id,
                previous.snapshot_id,
                previous.run_id,
                previous.parent_id,
                previous.version_number,
                previous.name,
                previous.source,
                previous.objective_value,
                previous.objective_breakdown,
                previous.created_by_id,
                previous.finalized_at,
            )
            allowed_statuses = {previous.status}
            if previous.status == ScheduleStatus.APPROVED:
                allowed_statuses.add(ScheduleStatus.ARCHIVED)
            if current_content != previous_content or self.status not in allowed_statuses:
                raise ValidationError("Approved or archived schedule versions are immutable.")
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if self.is_immutable:
            raise ValidationError("Approved or archived schedule versions cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.term} / v{self.version_number} {self.name}"


class ScheduleAssignment(TimestampedModel):
    schedule = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name="assignments")
    meeting_requirement = models.ForeignKey(
        MeetingRequirement,
        on_delete=models.PROTECT,
        related_name="schedule_assignments",
    )
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="schedule_assignments")
    start_time_slot = models.ForeignKey(
        TimeSlot,
        on_delete=models.PROTECT,
        related_name="starting_assignments",
    )
    placement_data = models.JSONField(default=dict, blank=True)
    objective_contribution = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["schedule", "start_time_slot__day", "start_time_slot__sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "meeting_requirement"],
                name="uniq_schedule_meeting_assignment",
            ),
        ]
        indexes = [models.Index(fields=["schedule", "room", "start_time_slot"])]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.schedule_id and self.schedule.assignments_are_immutable:
            errors["schedule"] = "Assignments can be changed only while a schedule is in DRAFT status."
        if (
            self.schedule_id
            and self.meeting_requirement_id
            and self.meeting_requirement.offering.revision_id != self.schedule.revision_id
        ):
            errors["meeting_requirement"] = "The meeting must belong to the schedule's dataset revision."
        if (
            self.schedule_id
            and self.start_time_slot_id
            and self.start_time_slot.revision_id != self.schedule.revision_id
        ):
            errors["start_time_slot"] = "The time slot must belong to the schedule's dataset revision."
        if self.schedule_id and self.room_id and self.room.campus != self.schedule.term.campus:
            errors["room"] = "The room must be on the schedule's campus."
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if self.schedule.assignments_are_immutable:
            raise ValidationError("Assignments can be deleted only while a schedule is in DRAFT status.")
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.meeting_requirement} @ {self.room.code}, {self.start_time_slot}"


class ScheduleRoomAllocation(models.Model):
    schedule = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name="room_allocations")
    assignment = models.ForeignKey(ScheduleAssignment, on_delete=models.CASCADE, related_name="room_allocations")
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="atom_allocations")
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT, related_name="room_allocations")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["assignment", "time_slot"], name="uniq_assignment_room_atom"),
            models.UniqueConstraint(fields=["schedule", "room", "time_slot"], name="uniq_schedule_room_atom"),
        ]
        indexes = [models.Index(fields=["schedule", "time_slot", "room"])]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.assignment_id and self.schedule_id and self.assignment.schedule_id != self.schedule_id:
            errors["assignment"] = "The allocation must use its assignment's schedule."
        if self.assignment_id and self.room_id and self.assignment.room_id != self.room_id:
            errors["room"] = "The allocation must use its assignment's room."
        if self.schedule_id and self.time_slot_id and self.schedule.revision_id != self.time_slot.revision_id:
            errors["time_slot"] = "The allocation slot must use the schedule's revision."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.schedule} / {self.room.code} / {self.time_slot}"


class ScheduleInstructorAllocation(models.Model):
    schedule = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name="instructor_allocations")
    assignment = models.ForeignKey(
        ScheduleAssignment,
        on_delete=models.CASCADE,
        related_name="instructor_allocations",
    )
    instructor = models.ForeignKey(Instructor, on_delete=models.PROTECT, related_name="atom_allocations")
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT, related_name="instructor_allocations")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "instructor", "time_slot"],
                name="uniq_assignment_instructor_atom",
            ),
            models.UniqueConstraint(
                fields=["schedule", "instructor", "time_slot"],
                name="uniq_schedule_instructor_atom",
            ),
        ]
        indexes = [models.Index(fields=["schedule", "time_slot", "instructor"])]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.assignment_id and self.schedule_id and self.assignment.schedule_id != self.schedule_id:
            errors["assignment"] = "The allocation must use its assignment's schedule."
        if self.schedule_id and self.time_slot_id and self.schedule.revision_id != self.time_slot.revision_id:
            errors["time_slot"] = "The allocation slot must use the schedule's revision."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.schedule} / {self.instructor} / {self.time_slot}"


class ScheduleSectionAllocation(models.Model):
    schedule = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name="section_allocations")
    assignment = models.ForeignKey(ScheduleAssignment, on_delete=models.CASCADE, related_name="section_allocations")
    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name="atom_allocations")
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT, related_name="section_allocations")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "section", "time_slot"],
                name="uniq_assignment_section_atom",
            ),
            models.UniqueConstraint(
                fields=["schedule", "section", "time_slot"],
                name="uniq_schedule_section_atom",
            ),
        ]
        indexes = [models.Index(fields=["schedule", "time_slot", "section"])]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.assignment_id and self.schedule_id and self.assignment.schedule_id != self.schedule_id:
            errors["assignment"] = "The allocation must use its assignment's schedule."
        if self.schedule_id and self.time_slot_id and self.schedule.revision_id != self.time_slot.revision_id:
            errors["time_slot"] = "The allocation slot must use the schedule's revision."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.schedule} / {self.section} / {self.time_slot}"


class LockedAssignment(TimestampedModel):
    meeting_requirement = models.ForeignKey(
        MeetingRequirement,
        on_delete=models.PROTECT,
        related_name="locks",
    )
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="locked_assignments")
    start_time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT, related_name="locked_assignments")
    source_schedule = models.ForeignKey(
        ScheduleVersion,
        on_delete=models.PROTECT,
        related_name="locks",
        null=True,
        blank=True,
    )
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="locked_assignments",
    )
    reason = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["meeting_requirement"]
        constraints = [
            models.UniqueConstraint(
                fields=["meeting_requirement"],
                condition=Q(is_active=True),
                name="uniq_active_lock_per_meeting",
            ),
        ]
        indexes = [models.Index(fields=["is_active", "meeting_requirement"])]

    @property
    def revision(self) -> TermDatasetRevision:
        return self.meeting_requirement.offering.revision

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if (
            self.meeting_requirement_id
            and self.start_time_slot_id
            and self.meeting_requirement.offering.revision_id != self.start_time_slot.revision_id
        ):
            errors["start_time_slot"] = "The lock slot must belong to the meeting's dataset revision."
        if self.meeting_requirement_id and self.room_id:
            term = self.meeting_requirement.offering.revision.term
            if self.room.campus != term.campus:
                errors["room"] = "The lock room must be on the meeting term's campus."
        if (
            self.source_schedule_id
            and self.meeting_requirement_id
            and self.source_schedule.revision_id != self.meeting_requirement.offering.revision_id
        ):
            errors["source_schedule"] = "The source schedule must use the meeting's dataset revision."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"Locked {self.meeting_requirement} @ {self.room.code}, {self.start_time_slot}"


class ReviewStatus(models.TextChoices):
    COMMENT = "COMMENT", "Comment"
    CHANGES_REQUESTED = "CHANGES_REQUESTED", "Changes requested"
    ENDORSED = "ENDORSED", "Endorsed"


class ScheduleReview(TimestampedModel):
    schedule = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name="reviews")
    college = models.ForeignKey(College, on_delete=models.PROTECT, related_name="schedule_reviews")
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="schedule_reviews",
    )
    status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.COMMENT)
    comment = models.TextField(blank=True)
    is_resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ["schedule", "college", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "college"],
                condition=Q(status=ReviewStatus.ENDORSED),
                name="uniq_schedule_college_endorsement",
            ),
        ]
        indexes = [models.Index(fields=["schedule", "college", "status"])]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if (
            self.reviewer_id
            and self.reviewer.role == UserRole.COLLEGE_REVIEWER
            and not UserCollegeScope.objects.filter(
                user_id=self.reviewer_id,
                college_id=self.college_id,
            ).exists()
        ):
            errors["reviewer"] = "The reviewer is not scoped to this college."
        if self.status in {ReviewStatus.ENDORSED, ReviewStatus.CHANGES_REQUESTED} and not self.comment:
            errors["comment"] = "Endorsements and change requests require a comment."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.schedule} / {self.college.code}: {self.status}"


class ScheduleApproval(TimestampedModel):
    schedule = models.OneToOneField(ScheduleVersion, on_delete=models.PROTECT, related_name="approval")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="schedule_approvals",
    )
    approved_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.approved_by_id and self.approved_by.role not in {
            UserRole.SYSTEM_ADMIN,
            UserRole.CENTRAL_SCHEDULER,
        }:
            errors["approved_by"] = "Only a system administrator or central scheduler may approve."
        if self.schedule_id and self.schedule.status != ScheduleStatus.APPROVED:
            errors["schedule"] = "The schedule must be in APPROVED status."
        if self.schedule_id and not hasattr(self.schedule, "validation_result"):
            errors["schedule"] = "The schedule must have an independent validation result."
        elif self.schedule_id and not self.schedule.validation_result.is_feasible:
            errors["schedule"] = "Only an independently validated feasible schedule may be approved."
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Schedule approvals are immutable.")
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Schedule approvals cannot be deleted.")

    def __str__(self) -> str:
        return f"Approval for {self.schedule} by {self.approved_by}"


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=100, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Audit log entries are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Audit log entries are append-only and cannot be deleted.")

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.action} {self.entity_type}:{self.entity_id}"
