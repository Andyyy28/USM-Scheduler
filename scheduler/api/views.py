from __future__ import annotations

import json
from datetime import date

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, connection
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from scheduler import models
from scheduler.api.permissions import IsCentralScheduler, IsSchedulerUser
from scheduler.api.serializers import (
    AcademicTermSerializer,
    ExperimentBatchSerializer,
    ExperimentStudySerializer,
    ImportBatchSerializer,
    ObjectiveProfileSerializer,
    ProblemSnapshotSerializer,
    ScheduleRunSerializer,
    ScheduleVersionSerializer,
    TermDatasetRevisionSerializer,
)
from scheduler.services.problem_builder import ProblemBuildError, build_and_store_snapshot
from scheduler.services.revision_metadata import with_dataset_counts
from scheduler.services.runs import RunDispatchError, create_run, queue_run
from scheduler.services.statistics import describe, vargha_delaney_a12, wilson_interval
from scheduler.services.workflow import (
    approve_schedule,
    cancel_run,
    lock_schedule_assignments,
    review_schedule,
    submit_for_review,
    validate_schedule_version,
)


def _translate_domain_error(exc: Exception) -> None:
    if isinstance(exc, DjangoPermissionDenied):
        raise PermissionDenied(str(exc)) from exc
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
        raise ValidationError(detail) from exc
    if isinstance(exc, ValueError):
        raise ValidationError(str(exc)) from exc
    raise exc


def _formal_study_error_response(exc: Exception) -> Response:
    """Keep formal-protocol failures machine readable for research tooling."""

    from scheduler.services.formal_studies import FormalStudyError

    if not isinstance(exc, FormalStudyError):
        _translate_domain_error(exc)
    return Response(
        {
            "code": "FORMAL_STUDY_ERROR",
            "detail": str(exc),
            "issues": [issue.to_dict() for issue in exc.issues],
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _problem_build_error_response(exc: ProblemBuildError) -> Response:
    return Response(
        {
            "code": "PREFLIGHT_FAILED",
            "detail": str(exc),
            "issues": [issue.to_dict() for issue in exc.issues],
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


class HealthView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except DatabaseError:
            return Response(
                {"status": "unhealthy", "database": "unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "ok", "database": "ok"})


class TermListView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request):
        return Response(AcademicTermSerializer(models.AcademicTerm.objects.all(), many=True).data)


class RevisionListView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request, term_id: int):
        term = get_object_or_404(models.AcademicTerm, pk=term_id)
        revisions = with_dataset_counts(
            term.dataset_revisions.select_related("source_import_batch")
        )
        return Response(TermDatasetRevisionSerializer(revisions, many=True).data)


class TermCloneView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, revision_id: int):
        from scheduler.services.term_cloning import clone_term_revision

        source = get_object_or_404(models.TermDatasetRevision, pk=revision_id)
        try:
            starts_on = date.fromisoformat(str(request.data.get("starts_on", "")))
            ends_on = date.fromisoformat(str(request.data.get("ends_on", "")))
        except ValueError as exc:
            raise ValidationError("starts_on and ends_on must use YYYY-MM-DD.") from exc
        try:
            revision = clone_term_revision(
                source,
                academic_year=request.data.get("academic_year", ""),
                semester=request.data.get("semester", ""),
                starts_on=starts_on,
                ends_on=ends_on,
                actor=request.user,
                label=request.data.get("label") or None,
            )
        except Exception as exc:
            _translate_domain_error(exc)
        return Response(TermDatasetRevisionSerializer(revision).data, status=status.HTTP_201_CREATED)


class RevisionFinalizeView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, revision_id: int):
        from scheduler.services.problem_builder import ProblemBuildError
        from scheduler.services.revision_lifecycle import validate_and_commit_revision

        revision = get_object_or_404(models.TermDatasetRevision, pk=revision_id)
        objective = get_object_or_404(
            models.ObjectiveProfile,
            pk=request.data.get("objective_profile_id"),
        )
        try:
            revision = validate_and_commit_revision(revision, objective, request.user)
        except ProblemBuildError as exc:
            return _problem_build_error_response(exc)
        except Exception as exc:
            _translate_domain_error(exc)
        return Response(TermDatasetRevisionSerializer(revision).data)


class ObjectiveProfileListView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request):
        term_id = request.query_params.get("term_id")
        queryset = models.ObjectiveProfile.objects.all()
        if term_id:
            queryset = queryset.filter(Q(term_id=term_id) | Q(term__isnull=True))
        return Response(ObjectiveProfileSerializer(queryset, many=True).data)


class ImportTemplateView(APIView):
    permission_classes = [IsCentralScheduler]

    def get(self, request):
        from scheduler.services.imports import build_import_template

        response = HttpResponse(
            build_import_template(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="usm-semester-import-v1.xlsx"'
        return response


class TrialWorkbookView(APIView):
    permission_classes = [IsCentralScheduler]

    def get(self, request):
        from scheduler.services.trial_data import (
            TRIAL_WORKBOOK_FILENAME,
            TrialPolicyConfigurationError,
            approved_trial_policy_hashes,
            build_trial_workbook_bytes,
        )

        term_id = request.query_params.get("term_id")
        if not term_id:
            raise ValidationError(
                {"term_id": "Choose the target academic term so approved policy hashes can be embedded."}
            )
        try:
            term_id = int(term_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"term_id": "Enter a valid academic-term ID."}) from exc
        term = get_object_or_404(models.AcademicTerm, pk=term_id)
        try:
            policy_hashes = approved_trial_policy_hashes(term)
        except TrialPolicyConfigurationError as exc:
            raise ValidationError({"term_id": str(exc)}) from exc
        response = HttpResponse(
            build_trial_workbook_bytes(campus=term.campus, **policy_hashes),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{TRIAL_WORKBOOK_FILENAME}"'
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["X-USM-Target-Term-ID"] = str(term.pk)
        return response


class ImportPreviewView(APIView):
    permission_classes = [IsCentralScheduler]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        from scheduler.services.imports import preview_workbook

        upload = request.FILES.get("file")
        if not upload:
            raise ValidationError({"file": "An XLSX file is required."})
        acknowledged = str(request.data.get("confirm_authorized", "")).lower() in {
            "1", "true", "yes", "on"
        }
        if not acknowledged:
            raise ValidationError(
                {"confirm_authorized": "Confirm authorization and data minimization before preview."}
            )
        if not upload.name.lower().endswith(".xlsx"):
            raise ValidationError({"file": "Use the versioned .xlsx import template."})
        if upload.size > 20 * 1024 * 1024:
            raise ValidationError({"file": "The workbook exceeds the 20 MB limit."})
        term = get_object_or_404(models.AcademicTerm, pk=request.data.get("term_id"))
        data_origin = request.data.get("data_origin")
        if data_origin not in {
            models.DatasetOrigin.SYNTHETIC,
            models.DatasetOrigin.INSTITUTIONAL,
        }:
            raise ValidationError(
                {"data_origin": "Declare whether this dataset is synthetic or institutional."}
            )
        batch = preview_workbook(
            upload.read(),
            term=term,
            user=request.user,
            data_origin=data_origin,
        )
        if batch.original_filename != upload.name:
            batch.original_filename = upload.name[:255]
            batch.save(update_fields=["original_filename", "updated_at"])
        return Response(ImportBatchSerializer(batch).data, status=status.HTTP_201_CREATED)


class ImportCommitView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, batch_id: int):
        from scheduler.services.imports import commit_import

        batch = get_object_or_404(models.ImportBatch, pk=batch_id)
        try:
            revision = commit_import(batch, user=request.user)
        except Exception as exc:
            _translate_domain_error(exc)
        return Response(TermDatasetRevisionSerializer(revision).data)


class SnapshotListCreateView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request):
        queryset = models.ProblemSnapshot.objects.select_related("revision__term").all()
        if request.query_params.get("revision_id"):
            queryset = queryset.filter(revision_id=request.query_params["revision_id"])
        return Response(ProblemSnapshotSerializer(queryset, many=True).data)

    def post(self, request):
        if not IsCentralScheduler().has_permission(request, self):
            raise PermissionDenied("Central scheduler access is required.")
        revision = get_object_or_404(models.TermDatasetRevision, pk=request.data.get("revision_id"))
        objective = get_object_or_404(models.ObjectiveProfile, pk=request.data.get("objective_profile_id"))
        try:
            snapshot, _ = build_and_store_snapshot(revision, objective, request.user)
        except ProblemBuildError as exc:
            return _problem_build_error_response(exc)
        return Response(ProblemSnapshotSerializer(snapshot).data, status=status.HTTP_201_CREATED)


class RunListCreateView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request):
        queryset = models.ScheduleRun.objects.select_related("snapshot", "requested_by").prefetch_related("metrics")
        if request.query_params.get("snapshot_id"):
            queryset = queryset.filter(snapshot_id=request.query_params["snapshot_id"])
        if request.query_params.get("experiment_batch_id"):
            queryset = queryset.filter(experiment_batch_id=request.query_params["experiment_batch_id"])
        return Response(ScheduleRunSerializer(queryset, many=True).data)

    def post(self, request):
        if not IsCentralScheduler().has_permission(request, self):
            raise PermissionDenied("Central scheduler access is required.")
        snapshot = get_object_or_404(models.ProblemSnapshot, pk=request.data.get("snapshot_id"))
        raw_algorithms = request.data.get("algorithms") or request.data.get("algorithm")
        if hasattr(request.data, "getlist") and request.data.getlist("algorithms"):
            raw_algorithms = request.data.getlist("algorithms")
        if isinstance(raw_algorithms, str):
            algorithms = [item.strip() for item in raw_algorithms.split(",") if item.strip()]
        else:
            algorithms = list(raw_algorithms or [])
        aliases = {
            "CP-SAT": models.SolverAlgorithm.CP_SAT,
            "CP_SAT": models.SolverAlgorithm.CP_SAT,
            "GA": models.SolverAlgorithm.GENETIC_ALGORITHM,
            "GENETIC_ALGORITHM": models.SolverAlgorithm.GENETIC_ALGORITHM,
        }
        algorithms = [aliases.get(str(algorithm).upper(), algorithm) for algorithm in algorithms]
        if not algorithms:
            raise ValidationError({"algorithms": "Select CP_SAT, GA, or both."})
        raw_configuration = request.data.get("configuration") or {}
        if isinstance(raw_configuration, str):
            try:
                configuration = json.loads(raw_configuration)
            except json.JSONDecodeError as exc:
                raise ValidationError({"configuration": "Enter a valid JSON object."}) from exc
        else:
            configuration = dict(raw_configuration)
        if not isinstance(configuration, dict):
            raise ValidationError({"configuration": "Configuration must be an object."})
        configurable_fields = {
            "time_limit_seconds",
            "worker_count",
            "population_size",
            "tournament_size",
            "crossover_rate",
            "mutation_rate",
            "elite_fraction",
            "repair_attempts",
            "max_generations",
        }
        for field in configurable_fields:
            if request.data.get(field) not in (None, ""):
                configuration[field] = request.data[field]
        if "first_feasible_only" in request.data:
            value = request.data["first_feasible_only"]
            if value not in (True, False, "true", "false"):
                raise ValidationError({"first_feasible_only": "Choose a generation goal."})
            configuration["first_feasible_only"] = value in (True, "true")
        queued = []
        for algorithm in algorithms:
            try:
                run = create_run(
                    snapshot=snapshot,
                    algorithm=algorithm,
                    requested_by=request.user,
                    seed=int(request.data.get("seed", 0)),
                    configuration=configuration,
                )
                queued.append(queue_run(run))
            except RunDispatchError as exc:
                return Response(
                    {"detail": str(exc), "run_id": run.pk},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            except Exception as exc:
                _translate_domain_error(exc)
        return Response(ScheduleRunSerializer(queued, many=True).data, status=status.HTTP_202_ACCEPTED)


class RunDetailView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request, run_id: int):
        run = get_object_or_404(models.ScheduleRun.objects.prefetch_related("metrics"), pk=run_id)
        return Response(ScheduleRunSerializer(run).data)


class RunCancelView(APIView):
    permission_classes = [IsSchedulerUser]

    def post(self, request, run_id: int):
        run = get_object_or_404(models.ScheduleRun, pk=run_id)
        try:
            run = cancel_run(run, request.user)
        except Exception as exc:
            _translate_domain_error(exc)
        return Response(ScheduleRunSerializer(run).data)


class RunComparisonView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request):
        snapshot_id = request.query_params.get("snapshot_id")
        experiment_id = request.query_params.get("experiment_batch_id")
        runs = models.ScheduleRun.objects.select_related("snapshot", "experiment_batch")
        scope: dict[str, int | str]
        if experiment_id:
            batch = get_object_or_404(models.ExperimentBatch, pk=experiment_id)
            runs = runs.filter(experiment_batch_id=experiment_id)
            scope = {
                "type": "controlled_experiment",
                "experiment_batch_id": batch.pk,
                "snapshot_id": batch.snapshot_id,
            }
        elif snapshot_id:
            snapshot = get_object_or_404(models.ProblemSnapshot, pk=snapshot_id)
            runs = runs.filter(snapshot_id=snapshot_id)
            scope = {"type": "snapshot", "snapshot_id": snapshot.pk}
        else:
            raise ValidationError("snapshot_id or experiment_batch_id is required.")

        planned_runs = list(runs)
        if snapshot_id and not experiment_id:
            from scheduler.services.experiments import snapshot_comparison_heterogeneity

            heterogeneous = snapshot_comparison_heterogeneity(planned_runs)
            if heterogeneous:
                return Response(
                    {
                        "code": "HETEROGENEOUS_COMPARISON",
                        "detail": (
                            "Snapshot comparisons require one deadline and worker count, "
                            "plus one implementation version and resolved configuration "
                            "within each algorithm. Use a controlled experiment batch or "
                            "narrow the run set."
                        ),
                        "heterogeneous_dimensions": heterogeneous,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        from scheduler.services.experiments import TERMINAL_STATUSES

        payload: dict[str, dict] = {}
        for algorithm in models.SolverAlgorithm.values:
            planned = [run for run in planned_runs if run.algorithm == algorithm]
            observed = [run for run in planned if run.status in TERMINAL_STATUSES]
            feasible = [
                run
                for run in observed
                if run.status in {models.RunStatus.FEASIBLE, models.RunStatus.OPTIMAL}
            ]
            interval = (
                wilson_interval(len(feasible), len(observed))
                if observed
                else (None, None)
            )
            payload[algorithm] = {
                "runs": len(observed),
                "planned_runs": len(planned),
                "observed_runs": len(observed),
                "pending_runs": len(planned) - len(observed),
                "feasible_runs": len(feasible),
                "success_rate": len(feasible) / len(observed) if observed else None,
                "success_rate_wilson_95": interval,
                "execution_seconds": describe(
                    run.execution_seconds
                    for run in observed
                    if run.execution_seconds is not None
                ).to_dict(),
                "first_feasible_seconds": describe(
                    run.first_feasible_seconds
                    for run in feasible
                    if run.first_feasible_seconds is not None
                ).to_dict(),
                "soft_penalty": describe(
                    run.objective_value
                    for run in feasible
                    if run.objective_value is not None
                ).to_dict(),
            }
        cp_penalties = [
            run.objective_value
            for run in planned_runs
            if run.algorithm == models.SolverAlgorithm.CP_SAT
            and run.status in {models.RunStatus.FEASIBLE, models.RunStatus.OPTIMAL}
            and run.objective_value is not None
        ]
        ga_penalties = [
            run.objective_value
            for run in planned_runs
            if run.algorithm == models.SolverAlgorithm.GENETIC_ALGORITHM
            and run.status in {models.RunStatus.FEASIBLE, models.RunStatus.OPTIMAL}
            and run.objective_value is not None
        ]
        payload["scope"] = scope
        payload["effect_sizes"] = {
            "cp_sat_probability_lower_penalty": (
                vargha_delaney_a12(cp_penalties, ga_penalties) if cp_penalties and ga_penalties else None
            )
        }
        return Response(payload)


class ScheduleListView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request):
        queryset = models.ScheduleVersion.objects.select_related("term", "run").all()
        if request.query_params.get("term_id"):
            queryset = queryset.filter(term_id=request.query_params["term_id"])
        return Response(ScheduleVersionSerializer(queryset, many=True).data)


class ScheduleDetailView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request, schedule_id: int):
        schedule = get_object_or_404(
            models.ScheduleVersion.objects.prefetch_related(
                "assignments__meeting_requirement__offering__subject",
                "assignments__room", "assignments__start_time_slot",
                "reviews__college", "reviews__reviewer",
            ),
            pk=schedule_id,
        )
        return Response(ScheduleVersionSerializer(schedule).data)


class ScheduleValidateView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, schedule_id: int):
        schedule = get_object_or_404(models.ScheduleVersion, pk=schedule_id)
        try:
            validation = validate_schedule_version(schedule, actor=request.user)
        except Exception as exc:
            _translate_domain_error(exc)
        return Response({"validation_id": validation.pk, "feasible": validation.is_feasible})


class ScheduleSubmitReviewView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, schedule_id: int):
        schedule = get_object_or_404(models.ScheduleVersion, pk=schedule_id)
        try:
            schedule = submit_for_review(schedule, request.user)
        except Exception as exc:
            _translate_domain_error(exc)
        return Response(ScheduleVersionSerializer(schedule).data)


class ScheduleReviewView(APIView):
    permission_classes = [IsSchedulerUser]

    def post(self, request, schedule_id: int):
        schedule = get_object_or_404(models.ScheduleVersion, pk=schedule_id)
        college = get_object_or_404(models.College, pk=request.data.get("college_id"))
        try:
            review = review_schedule(
                schedule=schedule,
                college=college,
                reviewer=request.user,
                status=request.data.get("status", models.ReviewStatus.COMMENT),
                comment=request.data.get("comment", ""),
            )
        except Exception as exc:
            _translate_domain_error(exc)
        return Response({"review_id": review.pk, "status": review.status}, status=status.HTTP_201_CREATED)


class ScheduleApproveView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, schedule_id: int):
        schedule = get_object_or_404(models.ScheduleVersion, pk=schedule_id)
        try:
            approval = approve_schedule(schedule, request.user, notes=request.data.get("notes", ""))
        except Exception as exc:
            _translate_domain_error(exc)
        return Response({"approval_id": approval.pk, "schedule_id": schedule_id})


class ScheduleLockView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, schedule_id: int):
        schedule = get_object_or_404(models.ScheduleVersion, pk=schedule_id)
        assignment_ids = request.data.get("assignment_ids", [])
        if hasattr(request.data, "getlist"):
            assignment_ids = request.data.getlist("assignment_ids") or assignment_ids
        if isinstance(assignment_ids, (str, int)):
            assignment_ids = [assignment_ids]
        try:
            locks = lock_schedule_assignments(
                schedule=schedule,
                assignment_ids=assignment_ids,
                actor=request.user,
                reason=request.data.get("reason", "Approved placement"),
            )
        except Exception as exc:
            _translate_domain_error(exc)
        return Response({"lock_ids": [lock.pk for lock in locks]}, status=status.HTTP_201_CREATED)


class ScheduleRegenerateView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, schedule_id: int):
        schedule = get_object_or_404(
            models.ScheduleVersion.objects.select_related(
                "revision", "snapshot__objective_profile"
            ),
            pk=schedule_id,
        )
        if not schedule.snapshot_id:
            raise ValidationError("Only a snapshot-backed schedule can be regenerated.")
        assignment_ids = request.data.get("assignment_ids", [])
        if hasattr(request.data, "getlist"):
            assignment_ids = request.data.getlist("assignment_ids") or assignment_ids
        if isinstance(assignment_ids, (str, int)):
            assignment_ids = [assignment_ids]
        if assignment_ids:
            try:
                lock_schedule_assignments(
                    schedule=schedule,
                    assignment_ids=assignment_ids,
                    actor=request.user,
                    reason=request.data.get("reason", "Carry into child regeneration"),
                )
            except Exception as exc:
                _translate_domain_error(exc)
        try:
            snapshot, _ = build_and_store_snapshot(
                schedule.revision,
                schedule.snapshot.objective_profile,
                request.user,
            )
        except ProblemBuildError as exc:
            return _problem_build_error_response(exc)
        raw_algorithms = request.data.get("algorithms") or request.data.get("algorithm") or "CP_SAT"
        if hasattr(request.data, "getlist") and request.data.getlist("algorithms"):
            raw_algorithms = request.data.getlist("algorithms")
        algorithms = (
            [item.strip() for item in raw_algorithms.split(",") if item.strip()]
            if isinstance(raw_algorithms, str)
            else list(raw_algorithms)
        )
        aliases = {
            "CP-SAT": models.SolverAlgorithm.CP_SAT,
            "CP_SAT": models.SolverAlgorithm.CP_SAT,
            "GA": models.SolverAlgorithm.GENETIC_ALGORITHM,
            "GENETIC_ALGORITHM": models.SolverAlgorithm.GENETIC_ALGORITHM,
        }
        configuration = {
            "parent_schedule_id": schedule.pk,
            "time_limit_seconds": request.data.get("time_limit_seconds", 300),
            "worker_count": 1,
        }
        queued = []
        for algorithm in algorithms:
            try:
                run = create_run(
                    snapshot=snapshot,
                    algorithm=aliases.get(str(algorithm).upper(), algorithm),
                    requested_by=request.user,
                    seed=int(request.data.get("seed", 0)),
                    configuration=configuration,
                )
                queued.append(queue_run(run))
            except Exception as exc:
                _translate_domain_error(exc)
        return Response(ScheduleRunSerializer(queued, many=True).data, status=status.HTTP_202_ACCEPTED)


class ScheduleExportView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request, schedule_id: int, export_format: str):
        from scheduler.services.exports import (
            schedule_csv_bytes,
            schedule_export_filename,
            schedule_xlsx_bytes,
        )

        schedule = get_object_or_404(
            models.ScheduleVersion.objects.select_related(
                "term", "revision", "snapshot__objective_profile", "validation_result"
            ),
            pk=schedule_id,
        )
        validation = getattr(schedule, "validation_result", None)
        if (
            schedule.status != models.ScheduleStatus.APPROVED
            or validation is None
            or not validation.is_feasible
        ):
            raise ValidationError(
                "Only an approved, independently validated schedule can be exported."
            )
        if export_format == "csv":
            content = schedule_csv_bytes(schedule)
            content_type = "text/csv; charset=utf-8"
        elif export_format == "xlsx":
            content = schedule_xlsx_bytes(schedule)
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            raise ValidationError("Supported schedule export formats are csv and xlsx.")
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = (
            f'attachment; filename="{schedule_export_filename(schedule, export_format)}"'
        )
        return response


class SnapshotManifestView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request, snapshot_id: int):
        from scheduler.services.exports import snapshot_manifest_bytes

        snapshot = get_object_or_404(
            models.ProblemSnapshot.objects.select_related("revision", "objective_profile"),
            pk=snapshot_id,
        )
        response = HttpResponse(snapshot_manifest_bytes(snapshot), content_type="application/json")
        response["Content-Disposition"] = (
            f'attachment; filename="problem-snapshot-{snapshot.pk}-manifest.json"'
        )
        return response


class ExperimentListCreateView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request):
        queryset = models.ExperimentBatch.objects.select_related("snapshot").prefetch_related("runs")
        if request.query_params.get("snapshot_id"):
            queryset = queryset.filter(snapshot_id=request.query_params["snapshot_id"])
        return Response(ExperimentBatchSerializer(queryset, many=True).data)

    def post(self, request):
        from scheduler.services.experiments import (
            DEFAULT_EXPERIMENT_SEEDS,
            create_experiment_batch,
            queue_experiment_batch,
        )

        if not IsCentralScheduler().has_permission(request, self):
            raise PermissionDenied("Central scheduler access is required.")
        snapshot = get_object_or_404(models.ProblemSnapshot, pk=request.data.get("snapshot_id"))
        raw_seeds = request.data.get("seeds", DEFAULT_EXPERIMENT_SEEDS)
        if isinstance(raw_seeds, str):
            try:
                seeds = [int(item.strip()) for item in raw_seeds.split(",") if item.strip()]
            except ValueError as exc:
                raise ValidationError({"seeds": "Use comma-separated non-negative integers."}) from exc
        else:
            seeds = list(raw_seeds)
        raw_configuration = request.data.get("configuration") or {}
        if isinstance(raw_configuration, str):
            try:
                run_configuration = json.loads(raw_configuration)
            except json.JSONDecodeError as exc:
                raise ValidationError({"configuration": "Enter a valid JSON object."}) from exc
        else:
            run_configuration = dict(raw_configuration)
        try:
            batch = create_experiment_batch(
                snapshot=snapshot,
                user=request.user,
                seeds=seeds,
                time_limit=int(request.data.get("time_limit_seconds", 300)),
                order_seed=int(request.data.get("order_seed", 20260824)),
                name=request.data.get("name") or None,
                memory_limit_mb=(
                    int(request.data["memory_limit_mb"])
                    if request.data.get("memory_limit_mb") not in (None, "")
                    else None
                ),
                run_configuration=run_configuration,
            )
            if str(request.data.get("queue", "false")).lower() in {"1", "true", "yes", "on"}:
                batch = queue_experiment_batch(batch)
        except Exception as exc:
            _translate_domain_error(exc)
        return Response(ExperimentBatchSerializer(batch).data, status=status.HTTP_201_CREATED)


class ExperimentDetailView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request, experiment_id: int):
        from scheduler.services.experiments import summarize_experiment

        batch = get_object_or_404(
            models.ExperimentBatch.objects.select_related(
                "snapshot__revision", "snapshot__objective_profile"
            ),
            pk=experiment_id,
        )
        return Response(summarize_experiment(batch))


class ExperimentQueueView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, experiment_id: int):
        from scheduler.services.experiments import queue_experiment_batch

        batch = get_object_or_404(models.ExperimentBatch, pk=experiment_id)
        try:
            batch = queue_experiment_batch(batch)
        except Exception as exc:
            _translate_domain_error(exc)
        return Response(ExperimentBatchSerializer(batch).data, status=status.HTTP_202_ACCEPTED)


class ExperimentCancelView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, experiment_id: int):
        batch = get_object_or_404(models.ExperimentBatch, pk=experiment_id)
        for run in batch.runs.filter(status__in=[models.RunStatus.QUEUED, models.RunStatus.RUNNING]):
            cancel_run(run, request.user)
        batch.status = models.ExperimentStatus.CANCELLED
        batch.save(update_fields=["status", "updated_at"])
        return Response(ExperimentBatchSerializer(batch).data)


class ExperimentExportView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request, experiment_id: int, export_format: str):
        from scheduler.services.experiments import export_experiment_csv, export_experiment_json

        batch = get_object_or_404(
            models.ExperimentBatch.objects.select_related("snapshot"),
            pk=experiment_id,
        )
        if export_format == "json":
            content = export_experiment_json(batch)
            content_type = "application/json"
        elif export_format == "csv":
            content = export_experiment_csv(batch)
            content_type = "text/csv; charset=utf-8"
        else:
            raise ValidationError("Supported experiment export formats are json and csv.")
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = (
            f'attachment; filename="experiment-{batch.pk}.{export_format}"'
        )
        return response


class FormalStudyListCreateView(APIView):
    """List formal studies or create the preregistered v2 protocol."""

    permission_classes = [IsSchedulerUser]

    def get(self, request):
        queryset = (
            models.ExperimentStudy.objects.filter(mode=models.ExperimentMode.FORMAL)
            .select_related("source_snapshot")
            .prefetch_related("batches")
        )
        if request.query_params.get("status"):
            queryset = queryset.filter(status=request.query_params["status"])
        if request.query_params.get("source_snapshot_id"):
            queryset = queryset.filter(
                source_snapshot_id=request.query_params["source_snapshot_id"]
            )
        return Response(ExperimentStudySerializer(queryset, many=True).data)

    def post(self, request):
        from scheduler.services.formal_studies import create_formal_study

        if not IsCentralScheduler().has_permission(request, self):
            raise PermissionDenied("Central scheduler access is required.")
        source_snapshot_id = request.data.get("source_snapshot_id")
        if source_snapshot_id in {None, ""}:
            raise ValidationError(
                {"source_snapshot_id": "A schema 1.2 source snapshot is required."}
            )
        snapshot = get_object_or_404(models.ProblemSnapshot, pk=source_snapshot_id)
        solver_profiles = request.data.get("solver_profiles")
        if isinstance(solver_profiles, str):
            try:
                solver_profiles = json.loads(solver_profiles)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    {"solver_profiles": "Enter a valid JSON object."}
                ) from exc
        # The tuning command emits a complete, checksum-bearing selection
        # artifact. Accept that artifact directly while retaining support for
        # callers that submit only its selected_profiles object.
        if isinstance(solver_profiles, dict) and isinstance(
            solver_profiles.get("selected_profiles"), dict
        ):
            solver_profiles = solver_profiles["selected_profiles"]
        try:
            scaling_seed = int(request.data.get("scaling_seed", 20260824))
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                {"scaling_seed": "Use a non-negative integer."}
            ) from exc
        try:
            study = create_formal_study(
                source_snapshot=snapshot,
                actor=request.user,
                solver_profiles=solver_profiles,
                name=request.data.get("name") or None,
                scaling_seed=scaling_seed,
            )
        except Exception as exc:
            return _formal_study_error_response(exc)
        return Response(
            ExperimentStudySerializer(study).data,
            status=status.HTTP_201_CREATED,
        )


class FormalStudyDetailView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request, study_id: int):
        from scheduler.services.formal_studies import inspect_formal_study

        study = get_object_or_404(
            models.ExperimentStudy.objects.filter(mode=models.ExperimentMode.FORMAL),
            pk=study_id,
        )
        try:
            return Response(inspect_formal_study(study))
        except Exception as exc:
            return _formal_study_error_response(exc)


class FormalStudyValidateView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, study_id: int):
        from scheduler.services.formal_studies import validate_formal_study

        study = get_object_or_404(
            models.ExperimentStudy.objects.filter(mode=models.ExperimentMode.FORMAL),
            pk=study_id,
        )
        try:
            result = validate_formal_study(study, actor=request.user)
        except Exception as exc:
            return _formal_study_error_response(exc)
        return Response(result)


class FormalStudyQueueView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, study_id: int):
        from scheduler.services.formal_studies import (
            inspect_formal_study,
            queue_formal_study,
        )

        study = get_object_or_404(
            models.ExperimentStudy.objects.filter(mode=models.ExperimentMode.FORMAL),
            pk=study_id,
        )
        try:
            study = queue_formal_study(study, actor=request.user)
            payload = inspect_formal_study(study)
        except Exception as exc:
            return _formal_study_error_response(exc)
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class FormalStudyCancelView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, study_id: int):
        from scheduler.services.formal_studies import (
            cancel_formal_study,
            inspect_formal_study,
        )

        study = get_object_or_404(
            models.ExperimentStudy.objects.filter(mode=models.ExperimentMode.FORMAL),
            pk=study_id,
        )
        try:
            study = cancel_formal_study(study, actor=request.user)
            payload = inspect_formal_study(study)
        except Exception as exc:
            return _formal_study_error_response(exc)
        return Response(payload)


class FormalStudyAnalysisView(APIView):
    permission_classes = [IsSchedulerUser]

    def get(self, request, study_id: int):
        from scheduler.services.research_metrics import analyze_experiment_study

        study = get_object_or_404(
            models.ExperimentStudy.objects.filter(mode=models.ExperimentMode.FORMAL),
            pk=study_id,
        )
        try:
            return Response(analyze_experiment_study(study))
        except Exception as exc:
            return _formal_study_error_response(exc)


class FormalStudyEvidenceView(APIView):
    permission_classes = [IsCentralScheduler]

    def get(self, request, study_id: int):
        import hashlib

        from scheduler.services.formal_studies import formal_evidence_bundle

        study = get_object_or_404(
            models.ExperimentStudy.objects.filter(mode=models.ExperimentMode.FORMAL),
            pk=study_id,
        )
        try:
            content, filename = formal_evidence_bundle(study)
        except Exception as exc:
            return _formal_study_error_response(exc)
        response = HttpResponse(content, content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Content-Length"] = str(len(content))
        response["X-Evidence-SHA256"] = hashlib.sha256(content).hexdigest()
        response["X-Content-Type-Options"] = "nosniff"
        return response


class FormalRunFailureClassificationView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, run_id: int):
        from scheduler.services.formal_studies import classify_run_failure

        run = get_object_or_404(
            models.ScheduleRun.objects.select_related("experiment_batch__study"),
            pk=run_id,
        )
        category = str(request.data.get("category", "")).strip().upper()
        reason = str(request.data.get("reason", "")).strip()
        try:
            run = classify_run_failure(
                run,
                actor=request.user,
                category=category,
                reason=reason,
            )
        except Exception as exc:
            return _formal_study_error_response(exc)
        return Response(ScheduleRunSerializer(run).data)


class FormalRunPairedReplacementView(APIView):
    permission_classes = [IsCentralScheduler]

    def post(self, request, run_id: int):
        from scheduler.services.formal_studies import (
            create_paired_infrastructure_replacement,
        )

        run = get_object_or_404(
            models.ScheduleRun.objects.select_related("experiment_batch__study"),
            pk=run_id,
        )
        try:
            replacements = create_paired_infrastructure_replacement(
                run,
                actor=request.user,
            )
        except Exception as exc:
            return _formal_study_error_response(exc)
        return Response(
            {
                "pair_attempt": 2,
                "replacement_runs": ScheduleRunSerializer(
                    replacements,
                    many=True,
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )
