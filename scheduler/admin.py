from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from . import models

admin.site.site_header = "USM Scheduler Administration"
admin.site.site_title = "USM Scheduler"
admin.site.index_title = "Academic scheduling data"
admin.site.disable_action("delete_selected")


class ReadOnlyArtifactAdmin(admin.ModelAdmin):
    """Prevent research/audit artifacts from being silently rewritten in admin."""

    def has_change_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False if obj is not None else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False

    def has_add_permission(self, request):  # type: ignore[no-untyped-def]
        return False


class UserCollegeScopeInline(admin.TabularInline):
    model = models.UserCollegeScope
    extra = 0


@admin.register(models.User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (("USM scheduling access", {"fields": ("role",)}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (("USM scheduling access", {"fields": ("role",)}),)
    list_display = ("username", "email", "get_full_name", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_active")
    inlines = (UserCollegeScopeInline,)


@admin.register(models.College)
class CollegeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(models.Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "college", "is_active")
    list_filter = ("college", "is_active")
    search_fields = ("code", "name", "college__code")
    autocomplete_fields = ("college",)


@admin.register(models.Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "department", "curriculum_label", "is_active")
    list_filter = ("department__college", "department", "is_active")
    search_fields = ("code", "name")
    autocomplete_fields = ("department",)


class ProgramSubjectInline(admin.TabularInline):
    model = models.ProgramSubject
    extra = 0
    autocomplete_fields = ("subject", "authoritative_college", "authoritative_department")


@admin.register(models.Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "title")


@admin.register(models.ProgramSubject)
class ProgramSubjectAdmin(admin.ModelAdmin):
    list_display = (
        "program",
        "subject",
        "curriculum_version",
        "classification",
        "authoritative_college",
        "is_active",
    )
    list_filter = ("classification", "authoritative_college", "curriculum_version", "is_active")
    search_fields = ("program__code", "subject__code", "subject__title")
    autocomplete_fields = ("program", "subject", "authoritative_college", "authoritative_department")


@admin.register(models.AcademicTerm)
class AcademicTermAdmin(admin.ModelAdmin):
    list_display = ("academic_year", "semester", "campus", "starts_on", "ends_on", "status")
    list_filter = ("campus", "semester", "status")
    search_fields = ("academic_year", "campus")


@admin.register(models.TermDatasetRevision)
class TermDatasetRevisionAdmin(admin.ModelAdmin):
    list_display = ("term", "revision_number", "label", "status", "created_by", "committed_at")
    list_filter = ("status", "term__campus")
    search_fields = ("term__academic_year", "label", "content_hash")
    readonly_fields = ("created_at", "updated_at", "committed_at")
    autocomplete_fields = ("term", "created_by")


@admin.register(models.Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "program",
        "year_level",
        "cohort_status",
        "expected_enrollment",
        "revision",
        "is_active",
    )
    list_filter = ("revision", "program__department__college", "cohort_status", "year_level", "is_active")
    search_fields = ("code", "program__code")
    autocomplete_fields = ("revision", "program")


@admin.register(models.Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("pseudonymous_code", "status")
    list_filter = ("status",)
    search_fields = ("pseudonymous_code",)


@admin.register(models.Instructor)
class InstructorAdmin(admin.ModelAdmin):
    list_display = ("employee_code", "display_name", "department", "is_active")
    list_filter = ("department__college", "department", "is_active")
    search_fields = ("employee_code", "display_name")
    autocomplete_fields = ("department",)


class RoomCapabilityInline(admin.TabularInline):
    model = models.RoomCapability
    extra = 0
    autocomplete_fields = ("capability",)


@admin.register(models.Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "campus", "kind", "owner", "is_active")
    list_filter = ("campus", "kind", "owning_college", "is_active")
    search_fields = ("code", "name", "owning_department__code")
    autocomplete_fields = ("owning_college", "owning_department")
    inlines = (RoomCapabilityInline,)

    @admin.display(description="Owner")
    def owner(self, obj):  # type: ignore[no-untyped-def]
        return obj.owning_college or obj.owning_department


@admin.register(models.Capability)
class CapabilityAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(models.RoomAuthorization)
class RoomAuthorizationAdmin(admin.ModelAdmin):
    list_display = ("room", "classification", "authorized_unit", "revision")
    list_filter = ("revision", "classification", "college", "department")
    search_fields = ("room__code", "college__code", "department__code")
    autocomplete_fields = ("revision", "room", "college", "department")


@admin.register(models.TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ("revision", "day", "sequence", "starts_at", "ends_at", "is_break", "is_active")
    list_filter = ("revision", "day", "is_break", "is_active")
    search_fields = ("revision__term__academic_year", "revision__term__campus")
    ordering = ("revision", "day", "sequence")


class OfferingSectionInline(admin.TabularInline):
    model = models.OfferingSection
    extra = 0
    autocomplete_fields = ("section", "program_subject")


class OfferingInstructorInline(admin.TabularInline):
    model = models.OfferingInstructor
    extra = 0
    autocomplete_fields = ("instructor",)


class MeetingRequirementInline(admin.TabularInline):
    model = models.MeetingRequirement
    extra = 0
    fields = ("component", "occurrence_number", "duration_atoms", "distinct_day_group", "is_active")


@admin.register(models.CourseOffering)
class CourseOfferingAdmin(admin.ModelAdmin):
    list_display = ("external_key", "subject", "offering_department", "revision", "is_active")
    list_filter = ("revision", "offering_department__college", "is_active")
    search_fields = ("external_key", "subject__code", "subject__title")
    autocomplete_fields = ("revision", "subject", "offering_department")
    inlines = (OfferingSectionInline, OfferingInstructorInline, MeetingRequirementInline)


@admin.register(models.MeetingRequirement)
class MeetingRequirementAdmin(admin.ModelAdmin):
    list_display = ("offering", "component", "occurrence_number", "duration_atoms", "is_active")
    list_filter = ("component", "is_active", "offering__revision")
    search_fields = ("offering__external_key", "offering__subject__code", "stable_key")
    autocomplete_fields = ("offering",)
    readonly_fields = ("stable_key",)


@admin.register(models.ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "term",
        "status",
        "total_rows",
        "error_count",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("status", "term")
    search_fields = ("original_filename", "file_hash")
    readonly_fields = ("file_hash", "summary", "created_at", "updated_at")
    autocomplete_fields = ("term", "uploaded_by", "committed_revision")


@admin.register(models.ObjectiveProfile)
class ObjectiveProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "term", "is_approved", "approved_by", "updated_at")
    list_filter = ("is_approved", "term")
    search_fields = ("name", "profile_hash")
    readonly_fields = ("profile_hash", "approved_at", "created_at", "updated_at")


@admin.register(models.ConstraintPolicyVersion)
class ConstraintPolicyVersionAdmin(admin.ModelAdmin):
    list_display = (
        "rule_code",
        "version",
        "classification",
        "effective_term",
        "owner_office",
        "is_approved",
        "approved_at",
    )
    list_filter = ("classification", "is_approved", "effective_term")
    search_fields = ("rule_code", "title", "owner_office", "source", "policy_hash")
    readonly_fields = ("policy_hash", "approved_at", "created_at", "updated_at")
    autocomplete_fields = ("effective_term", "approved_by")


@admin.register(models.ReservedTimeBlock)
class ReservedTimeBlockAdmin(ReadOnlyArtifactAdmin):
    list_display = ("label", "scope", "scope_target", "revision", "policy_version", "is_active")
    list_filter = ("scope", "is_active", "revision")
    search_fields = ("label", "reason", "policy_version__rule_code")
    readonly_fields = (
        "revision",
        "scope",
        "college",
        "department",
        "program",
        "section",
        "policy_version",
        "label",
        "reason",
        "is_active",
        "created_at",
        "updated_at",
    )


@admin.register(models.InstructorAvailabilityProfile)
class InstructorAvailabilityProfileAdmin(admin.ModelAdmin):
    list_display = (
        "instructor",
        "revision",
        "assume_fully_available",
        "max_daily_teaching_atoms",
        "acknowledge_no_daily_limit",
        "daily_load_policy_version",
    )
    list_filter = (
        "revision",
        "assume_fully_available",
        "acknowledge_no_daily_limit",
    )
    search_fields = ("instructor__employee_code", "instructor__display_name")
    autocomplete_fields = (
        "revision",
        "instructor",
        "daily_load_policy_version",
        "acknowledged_by",
    )


@admin.register(models.ProblemSnapshot)
class ProblemSnapshotAdmin(ReadOnlyArtifactAdmin):
    list_display = ("snapshot_hash_short", "revision", "objective_profile", "event_count", "created_at")
    list_filter = ("revision", "schema_version")
    search_fields = ("snapshot_hash",)
    readonly_fields = (
        "snapshot_hash",
        "revision",
        "objective_profile",
        "schema_version",
        "event_count",
        "candidate_count",
        "preprocessing_seconds",
        "constraint_manifest_hash",
        "rule_manifest",
        "fixed_student_limit",
        "section_headcounts",
        "meeting_headcounts",
        "reserved_block_evidence",
        "instructor_daily_load_evidence",
        "instance_characteristics",
        "created_by",
        "created_at",
        "updated_at",
        "input_data",
        "candidate_map",
    )

    @admin.display(description="Hash")
    def snapshot_hash_short(self, obj):  # type: ignore[no-untyped-def]
        return obj.snapshot_hash[:12]


@admin.register(models.ExperimentStudy)
class ExperimentStudyAdmin(ReadOnlyArtifactAdmin):
    list_display = (
        "name",
        "mode",
        "protocol_version",
        "status",
        "source_snapshot",
        "created_at",
    )
    list_filter = ("mode", "protocol_version", "status")
    search_fields = ("name", "manifest_hash", "source_snapshot__snapshot_hash")
    readonly_fields = (
        "name",
        "mode",
        "protocol_version",
        "status",
        "source_snapshot",
        "scale_percentages",
        "seeds",
        "order_seed",
        "deadline_seconds",
        "cpu_limit",
        "memory_limit_mb",
        "warmups_per_algorithm_scale",
        "protocol_manifest",
        "manifest_hash",
        "protocol_integrity",
        "invalid_reason",
        "created_by",
        "cancelled_by",
        "cancelled_at",
        "created_at",
        "updated_at",
    )


@admin.register(models.ExperimentBatch)
class ExperimentBatchAdmin(ReadOnlyArtifactAdmin):
    list_display = (
        "name",
        "study",
        "planned_scale_percentage",
        "actual_scale_percentage",
        "status",
        "time_limit_seconds",
        "cpu_limit",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("name", "snapshot__snapshot_hash")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("snapshot", "created_by")


@admin.register(models.ScheduleRun)
class ScheduleRunAdmin(ReadOnlyArtifactAdmin):
    list_display = (
        "algorithm",
        "seed",
        "purpose",
        "pair_attempt",
        "status",
        "snapshot",
        "execution_seconds",
        "objective_value",
        "created_at",
    )
    list_filter = ("algorithm", "purpose", "status", "failure_category", "experiment_batch")
    search_fields = ("task_id", "snapshot__snapshot_hash", "stopping_reason")
    readonly_fields = ("created_at", "updated_at", "diagnostics", "result_data")
    autocomplete_fields = ("experiment_batch", "snapshot", "requested_by")


class ScheduleAssignmentInline(admin.TabularInline):
    model = models.ScheduleAssignment
    extra = 0
    autocomplete_fields = ("meeting_requirement", "room", "start_time_slot")


@admin.register(models.ScheduleVersion)
class ScheduleVersionAdmin(admin.ModelAdmin):
    list_display = ("name", "term", "version_number", "source", "status", "created_by", "created_at")
    list_filter = ("term", "source", "status")
    search_fields = ("name",)
    readonly_fields = ("status", "finalized_at", "created_at", "updated_at")
    autocomplete_fields = ("term", "revision", "snapshot", "run", "parent", "created_by")
    inlines = (ScheduleAssignmentInline,)

    def get_inline_instances(self, request, obj=None):  # type: ignore[no-untyped-def]
        if obj is not None and obj.status != models.ScheduleStatus.DRAFT:
            return []
        return super().get_inline_instances(request, obj)


@admin.register(models.LockedAssignment)
class LockedAssignmentAdmin(admin.ModelAdmin):
    list_display = ("meeting_requirement", "room", "start_time_slot", "locked_by", "is_active")
    list_filter = ("is_active", "room__campus")
    search_fields = ("meeting_requirement__offering__external_key", "room__code", "reason")
    autocomplete_fields = ("meeting_requirement", "room", "start_time_slot", "source_schedule", "locked_by")


@admin.register(models.ScheduleReview)
class ScheduleReviewAdmin(ReadOnlyArtifactAdmin):
    list_display = ("schedule", "college", "reviewer", "status", "is_resolved", "created_at")
    list_filter = ("college", "status", "is_resolved")
    search_fields = ("schedule__name", "reviewer__username", "comment")
    autocomplete_fields = ("schedule", "college", "reviewer")


@admin.register(models.ScheduleApproval)
class ScheduleApprovalAdmin(admin.ModelAdmin):
    list_display = ("schedule", "approved_by", "approved_at")
    search_fields = ("schedule__name", "approved_by__username")
    readonly_fields = ("approved_at", "created_at", "updated_at")
    autocomplete_fields = ("schedule", "approved_by")


@admin.register(models.AuditLog)
class AuditLogAdmin(ReadOnlyArtifactAdmin):
    list_display = ("created_at", "actor", "action", "entity_type", "entity_id", "ip_address")
    list_filter = ("action", "entity_type")
    search_fields = ("actor__username", "entity_type", "entity_id", "action")
    readonly_fields = ("actor", "action", "entity_type", "entity_id", "details", "ip_address", "created_at")


# Low-volume link/projection models remain available for diagnostics without custom screens.
for model in (
    models.StudentSectionMembership,
    models.LaboratoryProfile,
    models.RoomCapability,
    models.InstructorAvailability,
    models.RoomAvailabilityProfile,
    models.RoomAvailability,
    models.InstructorPreference,
    models.OfferingSection,
    models.OfferingInstructor,
    models.MeetingRequiredCapability,
    models.ImportError,
    models.ReservedTimeBlockSlot,
):
    admin.site.register(model)


for model in (
    models.ValidationResult,
    models.RunMetric,
    models.ScheduleAssignment,
    models.ScheduleRoomAllocation,
    models.ScheduleInstructorAllocation,
    models.ScheduleSectionAllocation,
):
    admin.site.register(model, ReadOnlyArtifactAdmin)
