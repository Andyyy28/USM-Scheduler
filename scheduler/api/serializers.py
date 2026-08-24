from rest_framework import serializers

from scheduler import models


class AcademicTermSerializer(serializers.ModelSerializer):
    semester_display = serializers.CharField(source="get_semester_display", read_only=True)

    class Meta:
        model = models.AcademicTerm
        fields = [
            "id", "academic_year", "semester", "semester_display", "campus",
            "starts_on", "ends_on", "status",
        ]


class TermDatasetRevisionSerializer(serializers.ModelSerializer):
    term = AcademicTermSerializer(read_only=True)

    class Meta:
        model = models.TermDatasetRevision
        fields = [
            "id", "term", "revision_number", "status", "label", "content_hash",
            "created_at", "committed_at",
        ]


class ObjectiveProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ObjectiveProfile
        fields = [
            "id", "name", "version", "term_id", "weights", "definitions",
            "normalization_denominators", "profile_hash", "is_approved",
        ]


class ProblemSnapshotSerializer(serializers.ModelSerializer):
    revision = TermDatasetRevisionSerializer(read_only=True)

    class Meta:
        model = models.ProblemSnapshot
        fields = [
            "id", "revision", "objective_profile_id", "schema_version", "snapshot_hash",
            "event_count", "candidate_count", "preprocessing_seconds", "created_at",
        ]


class ExperimentBatchSerializer(serializers.ModelSerializer):
    run_count = serializers.IntegerField(source="runs.count", read_only=True)

    class Meta:
        model = models.ExperimentBatch
        fields = [
            "id",
            "name",
            "snapshot_id",
            "status",
            "seeds",
            "order_seed",
            "time_limit_seconds",
            "cpu_limit",
            "memory_limit_mb",
            "configuration",
            "run_count",
            "created_at",
        ]


class RunMetricSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.RunMetric
        fields = ["name", "value", "unit", "metadata"]


class ValidationResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ValidationResult
        fields = [
            "is_feasible", "hard_violation_count", "violations", "raw_soft_penalty",
            "objective_breakdown", "normalized_quality_score", "validator_version", "validated_at",
        ]


class ScheduleRunSerializer(serializers.ModelSerializer):
    algorithm_display = serializers.CharField(source="get_algorithm_display", read_only=True)
    metrics = RunMetricSerializer(many=True, read_only=True)
    validation = serializers.SerializerMethodField()
    schedule_version_id = serializers.SerializerMethodField()

    class Meta:
        model = models.ScheduleRun
        fields = [
            "id", "snapshot_id", "experiment_batch_id", "algorithm", "algorithm_display",
            "seed", "status", "configuration", "queued_at", "started_at", "finished_at",
            "first_feasible_seconds", "execution_seconds", "objective_value", "best_bound",
            "relative_gap", "hard_violation_count", "stopping_reason", "diagnostics",
            "error_message", "metrics", "validation", "schedule_version_id",
        ]

    def get_validation(self, obj):
        try:
            return ValidationResultSerializer(obj.validation_result).data
        except models.ValidationResult.DoesNotExist:
            return None

    def get_schedule_version_id(self, obj):
        try:
            return obj.schedule_version.pk
        except models.ScheduleVersion.DoesNotExist:
            return None


class ScheduleAssignmentSerializer(serializers.ModelSerializer):
    subject_code = serializers.CharField(source="meeting_requirement.offering.subject.code", read_only=True)
    offering_key = serializers.CharField(source="meeting_requirement.offering.external_key", read_only=True)
    meeting_label = serializers.CharField(source="meeting_requirement.__str__", read_only=True)
    room_code = serializers.CharField(source="room.code", read_only=True)
    time_label = serializers.CharField(source="start_time_slot.__str__", read_only=True)

    class Meta:
        model = models.ScheduleAssignment
        fields = [
            "id", "meeting_requirement_id", "meeting_label", "subject_code", "offering_key",
            "room_id", "room_code", "start_time_slot_id", "time_label", "placement_data",
            "objective_contribution",
        ]


class ScheduleReviewSerializer(serializers.ModelSerializer):
    college_code = serializers.CharField(source="college.code", read_only=True)
    reviewer_name = serializers.CharField(source="reviewer.__str__", read_only=True)

    class Meta:
        model = models.ScheduleReview
        fields = [
            "id", "college_id", "college_code", "reviewer_id", "reviewer_name", "status",
            "comment", "is_resolved", "created_at",
        ]


class ScheduleVersionSerializer(serializers.ModelSerializer):
    assignments = ScheduleAssignmentSerializer(many=True, read_only=True)
    reviews = ScheduleReviewSerializer(many=True, read_only=True)
    validation = serializers.SerializerMethodField()

    class Meta:
        model = models.ScheduleVersion
        fields = [
            "id", "term_id", "revision_id", "snapshot_id", "run_id", "parent_id",
            "version_number", "name", "source", "status", "objective_value",
            "objective_breakdown", "finalized_at", "created_at", "validation",
            "assignments", "reviews",
        ]

    def get_validation(self, obj):
        try:
            return ValidationResultSerializer(obj.validation_result).data
        except models.ValidationResult.DoesNotExist:
            return None


class ImportErrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ImportError
        fields = ["sheet_name", "row_number", "column_name", "code", "message"]


class ImportBatchSerializer(serializers.ModelSerializer):
    errors = ImportErrorSerializer(many=True, read_only=True)
    safe_summary = serializers.SerializerMethodField()

    class Meta:
        model = models.ImportBatch
        fields = [
            "id", "term_id", "original_filename", "file_hash", "status", "total_rows",
            "error_count", "safe_summary", "committed_revision_id", "created_at", "errors",
        ]

    def get_safe_summary(self, obj):
        summary = dict(obj.summary or {})
        # Normalized staging rows can contain faculty and pseudonymous student
        # records. The preview endpoint exposes counts and diagnostics only.
        summary.pop("sheets", None)
        summary.pop("staged_rows", None)
        return summary
