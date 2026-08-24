from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import FieldError, ObjectDoesNotExist, ValidationError
from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.text import slugify
from django.views.decorators.http import require_GET


def _model(name: str) -> Any | None:
    """Resolve domain models at request time so the UI can evolve independently."""
    try:
        return apps.get_model("scheduler", name)
    except LookupError:
        return None


def _safe_list(
    model_name: str,
    *,
    limit: int = 100,
    filters: Mapping[str, Any] | None = None,
) -> list[Any]:
    model = _model(model_name)
    if model is None:
        return []
    try:
        queryset = model.objects.all()
        if filters:
            queryset = queryset.filter(**filters)
        return list(queryset.order_by("-pk")[:limit])
    except (DatabaseError, FieldError, TypeError, ValueError, ValidationError):
        return []


def _safe_count(model_name: str) -> int:
    model = _model(model_name)
    if model is None:
        return 0
    try:
        return model.objects.count()
    except DatabaseError:
        return 0


def _safe_get(model_name: str, pk: str | None) -> Any | None:
    if not pk:
        return None
    model = _model(model_name)
    if model is None:
        return None
    try:
        return model.objects.filter(pk=pk).first()
    except (DatabaseError, TypeError, ValueError, ValidationError):
        return None


def _dig(source: Any, path: str, default: Any = None) -> Any:
    current = source
    for part in path.split("."):
        if current is None:
            return default
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            try:
                current = getattr(current, part, None)
            except ObjectDoesNotExist:
                return default
        if callable(current):
            try:
                current = current()
            except (TypeError, ValueError):
                return default
    return default if current is None else current


def _first(source: Any, *paths: str, default: Any = None) -> Any:
    for path in paths:
        value = _dig(source, path)
        if value not in (None, ""):
            return value
    return default


def _display(source: Any, field: str, default: str = "—") -> str:
    display = _dig(source, f"get_{field}_display")
    if display not in (None, ""):
        return str(display)
    value = _dig(source, field)
    return default if value in (None, "") else str(value).replace("_", " ").title()


def _status_class(value: Any) -> str:
    return slugify(str(value or "unknown")) or "unknown"


def _duration(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "—" if value in (None, "") else str(value)
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.2f} s"


def _term_view(term: Any) -> SimpleNamespace:
    year = _first(term, "academic_year", "year", default="Academic year")
    semester = _display(term, "semester", default="Term")
    campus = _first(term, "campus.name", "campus", default="USM")
    status = _display(term, "status", default="Draft")
    return SimpleNamespace(
        id=str(_first(term, "pk", "id", default="")),
        label=str(term),
        academic_year=year,
        semester=semester,
        campus=campus,
        status=status,
        status_class=_status_class(status),
        starts_at=_first(term, "starts_on", "starts_at", "start_date"),
        ends_at=_first(term, "ends_on", "ends_at", "end_date"),
    )


def _run_metric(run: Any, *names: str, default: Any = None) -> Any:
    containers = (
        run,
        _dig(run, "metrics", {}),
        _dig(run, "result_data", {}),
        _dig(run, "result_data.metrics", {}),
        _dig(run, "diagnostics", {}),
    )
    for container in containers:
        for name in names:
            value = _dig(container, name)
            if value not in (None, ""):
                return value
    return default


def _snapshot_view(snapshot: Any) -> SimpleNamespace:
    snapshot_hash = _first(snapshot, "snapshot_hash", default="")
    return SimpleNamespace(
        id=str(_first(snapshot, "pk", "id", default="")),
        label=str(snapshot),
        term=str(_first(snapshot, "revision.term", default="—")),
        revision=str(_first(snapshot, "revision", default="—")),
        event_count=_first(snapshot, "event_count", default="—"),
        candidate_count=_first(snapshot, "candidate_count", default="—"),
        short_hash=str(snapshot_hash)[:12] if snapshot_hash else "no hash",
    )


def _experiment_view(batch: Any) -> SimpleNamespace:
    status = _display(batch, "status", default="Draft")
    return SimpleNamespace(
        id=str(_first(batch, "pk", "id", default="")),
        name=str(batch),
        snapshot=str(_first(batch, "snapshot", default="—")),
        status=status,
        status_class=_status_class(status),
        seeds=len(_first(batch, "seeds", default=[])),
        run_count=_related_count(batch, "runs"),
        time_limit=_first(batch, "time_limit_seconds", default="—"),
        created_at=_first(batch, "created_at"),
        can_queue=_first(batch, "status") == "DRAFT",
    )


def _run_view(run: Any) -> SimpleNamespace:
    status = _display(run, "status", default="Unknown")
    algorithm = _display(run, "algorithm", default="Unspecified")
    runtime = _run_metric(run, "execution_seconds", "runtime_seconds", "execution_time_seconds")
    first_feasible = _run_metric(run, "first_feasible_seconds", "time_to_first_feasible")
    return SimpleNamespace(
        raw=run,
        id=str(_first(run, "pk", "id", default="")),
        algorithm=algorithm,
        status=status,
        status_class=_status_class(status),
        term=str(
            _first(
                run,
                "snapshot.revision.term",
                "snapshot.term",
                "term",
                "dataset_revision.term",
                default="—",
            )
        ),
        snapshot=str(_first(run, "snapshot", "dataset_revision", default="—")),
        snapshot_id=str(_first(run, "snapshot_id", "dataset_revision_id", default="")),
        created_at=_first(run, "created_at", "queued_at", "started_at"),
        started_at=_first(run, "started_at"),
        finished_at=_first(run, "finished_at", "completed_at"),
        runtime=_duration(runtime),
        runtime_raw=runtime,
        first_feasible=_duration(first_feasible),
        hard_violations=_run_metric(
            run,
            "hard_violation_count",
            "hard_violations",
            "validation.hard_violation_count",
            default="—",
        ),
        objective=_run_metric(run, "objective", "objective_value", "quality_score", default="—"),
        retry_count=_run_metric(run, "retry_count", "retries", default="—"),
        room_utilization=_run_metric(run, "room_utilization", default="—"),
        seed=_first(run, "seed", default="—"),
        stopping_reason=_run_metric(run, "stopping_reason", default="—"),
        problem_hash=_run_metric(run, "problem_hash", "snapshot.snapshot_hash", default=""),
        config_hash=_run_metric(run, "config_hash", default=""),
        can_cancel=_first(run, "status", default="") in {"QUEUED", "RUNNING"},
    )


def _schedule_view(schedule: Any) -> SimpleNamespace:
    status = _display(schedule, "status", default="Draft")
    status_value = _first(schedule, "status", default="DRAFT")
    run = _first(schedule, "run")
    return SimpleNamespace(
        raw=schedule,
        id=str(_first(schedule, "pk", "id", default="")),
        name=str(schedule),
        term=str(_first(schedule, "term", "revision.term", default="—")),
        version=_first(schedule, "version_number", "version", default="—"),
        source=_display(schedule, "source", default="Generated"),
        status=status,
        status_value=status_value,
        status_class=_status_class(status),
        algorithm=_display(run, "algorithm", default="Manual") if run else "Manual",
        created_at=_first(schedule, "created_at"),
        assignment_count=_related_count(schedule, "assignments", "schedule_assignments"),
        approved=bool(_first(schedule, "approval", "approved_at", default=False)),
        snapshot_id=str(_first(schedule, "snapshot_id", default="")),
    )


def _related_count(source: Any, *relations: str) -> int | str:
    for relation in relations:
        manager = getattr(source, relation, None)
        if manager is not None and hasattr(manager, "count"):
            try:
                return manager.count()
            except DatabaseError:
                return "—"
    return "—"


def _import_view(batch: Any) -> SimpleNamespace:
    status = _display(batch, "status", default="Pending")
    status_value = _first(batch, "status", default="")
    summary = _first(batch, "summary", default={})
    if isinstance(summary, Mapping):
        rows = summary.get("accepted_rows", summary.get("rows", _first(batch, "total_rows", default="—")))
    else:
        rows = "—"
    return SimpleNamespace(
        id=str(_first(batch, "pk", "id", default="")),
        term=str(_first(batch, "term", default="—")),
        filename=_first(batch, "original_filename", "filename", "file.name", default="Semester dataset"),
        status=status,
        status_class=_status_class(status),
        uploaded_by=str(_first(batch, "uploaded_by", "created_by", default="—")),
        created_at=_first(batch, "created_at", "uploaded_at"),
        rows=rows,
        error_count=_related_count(batch, "errors", "import_errors"),
        errors=list(batch.errors.all()[:5]) if hasattr(batch, "errors") else [],
        can_commit=status_value == "PREVIEWED",
        committed_revision_id=str(_first(batch, "committed_revision_id", default="")),
    )


def _review_view(review: Any) -> SimpleNamespace:
    status = _display(review, "status", default="Pending")
    return SimpleNamespace(
        id=str(_first(review, "pk", "id", default="")),
        schedule=str(_first(review, "schedule", default="—")),
        schedule_id=str(_first(review, "schedule_id", default="")),
        college=str(_first(review, "college", default="University scheduling office")),
        college_id=str(_first(review, "college_id", default="")),
        reviewer=str(_first(review, "reviewer", default="Unassigned")),
        status=status,
        status_class=_status_class(status),
        comment=_first(review, "comment", "notes", default=""),
        updated_at=_first(review, "updated_at", "reviewed_at", "created_at"),
        actionable=not bool(_first(review, "is_resolved", default=False))
        and _first(review, "status", default="") != "ENDORSED",
    )


def _related_text(source: Any, relation: str, *, limit: int = 3, default: str = "—") -> str:
    manager = getattr(source, relation, None)
    if manager is None or not hasattr(manager, "all"):
        return default
    try:
        values = [str(item) for item in manager.all()[: limit + 1]]
    except DatabaseError:
        return default
    if not values:
        return default
    if len(values) > limit:
        return f"{', '.join(values[:limit])} +{len(values) - limit}"
    return ", ".join(values)


def _through_text(
    source: Any,
    relation: str,
    target: str,
    *,
    limit: int = 3,
    default: str = "—",
) -> str:
    manager = getattr(source, relation, None)
    if manager is None or not hasattr(manager, "all"):
        return default
    try:
        values = [str(getattr(link, target)) for link in manager.all()[: limit + 1]]
    except (DatabaseError, AttributeError):
        return default
    if not values:
        return default
    return f"{', '.join(values[:limit])} +{len(values) - limit}" if len(values) > limit else ", ".join(values)


def _assignment_is_locked(assignment: Any) -> bool:
    lock_model = _model("LockedAssignment")
    if lock_model is None:
        return False
    try:
        return lock_model.objects.filter(
            meeting_requirement_id=_first(assignment, "meeting_requirement_id"),
            room_id=_first(assignment, "room_id"),
            start_time_slot_id=_first(assignment, "start_time_slot_id"),
            is_active=True,
        ).exists()
    except (DatabaseError, FieldError, TypeError, ValueError):
        return False


def _assignment_view(assignment: Any) -> SimpleNamespace:
    requirement = _first(assignment, "meeting_requirement", "requirement")
    section = _first(requirement, "offering_section", "section")
    offering = _first(requirement, "offering", default=_first(section, "course_offering", "offering"))
    subject = _first(offering, "subject", "course")
    slot = _first(assignment, "start_time_slot", "time_slot")
    room = _first(assignment, "room")
    end_time = _first(slot, "ends_at", "end_time")
    allocations = getattr(assignment, "room_allocations", None)
    if allocations is not None:
        try:
            last_allocation = allocations.select_related("time_slot").order_by(
                "-time_slot__day", "-time_slot__sequence"
            ).first()
            if last_allocation:
                end_time = last_allocation.time_slot.ends_at
        except DatabaseError:
            pass
    return SimpleNamespace(
        id=str(_first(assignment, "pk", "id", default="")),
        day=_display(slot, "day", default=_display(slot, "day_of_week", default="—")),
        starts_at=_first(slot, "start_time", "starts_at"),
        ends_at=end_time,
        subject_code=_first(subject, "code", default=str(subject or requirement or "—")),
        subject_name=_first(subject, "title", "name", default=""),
        section=_first(
            section,
            "code",
            "name",
            default=_through_text(offering, "section_links", "section", default=str(section or "—")),
        ),
        instructor=_first(
            assignment,
            "instructor",
            default=_first(
                requirement,
                "instructor",
                default=_through_text(offering, "instructor_links", "instructor", default="See offering"),
            ),
        ),
        room=_first(room, "code", "name", default=str(room or "—")),
        college=_first(
            offering,
            "offering_department.college.code",
            "offering_department.college.name",
            "department.college.code",
            default="—",
        ),
        locked=bool(_first(assignment, "is_locked", "locked", default=False))
        or _assignment_is_locked(assignment),
    )


def _render(request: HttpRequest, template: str, *, section: str, title: str, **context: Any) -> HttpResponse:
    is_central = bool(
        request.user.is_authenticated
        and request.user.is_active
        and (
            request.user.is_superuser
            or getattr(request.user, "role", "") in {"SYSTEM_ADMIN", "CENTRAL_SCHEDULER"}
        )
    )
    context.update({"section": section, "page_title": title, "is_central": is_central})
    return render(request, template, context)


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    del request
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unhealthy", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok", "database": "ready"})


@login_required
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    terms = [_term_view(term) for term in _safe_list("AcademicTerm", limit=12)]
    active_term = next((term for term in terms if term.status.lower() == "active"), terms[0] if terms else None)
    recent_runs = [_run_view(run) for run in _safe_list("ScheduleRun", limit=6)]
    reviews = [_review_view(review) for review in _safe_list("ScheduleReview", limit=100)]
    central = request.user.is_active and (
        request.user.is_superuser
        or getattr(request.user, "role", "") in {"SYSTEM_ADMIN", "CENTRAL_SCHEDULER"}
    )
    imports = [_import_view(batch) for batch in _safe_list("ImportBatch", limit=1)] if central else []
    return _render(
        request,
        "scheduler/dashboard.html",
        section="dashboard",
        title="Scheduling overview",
        active_term=active_term,
        recent_runs=recent_runs,
        latest_import=imports[0] if imports else None,
        counts={
            "terms": _safe_count("AcademicTerm"),
            "offerings": _safe_count("CourseOffering"),
            "rooms": _safe_count("Room"),
            "instructors": _safe_count("Instructor"),
            "pending_reviews": sum(review.actionable for review in reviews),
        },
    )


@login_required
@require_GET
def terms(request: HttpRequest) -> HttpResponse:
    rows = [_term_view(term) for term in _safe_list("AcademicTerm", limit=250)]
    clone_sources = _safe_list(
        "TermDatasetRevision",
        limit=100,
        filters={"status__in": ["COMMITTED", "SUPERSEDED"]},
    )
    draft_revisions = _safe_list(
        "TermDatasetRevision",
        limit=100,
        filters={"status": "DRAFT"},
    )
    approved_objectives = _safe_list(
        "ObjectiveProfile",
        limit=100,
        filters={"is_approved": True},
    )
    return _render(
        request,
        "scheduler/terms.html",
        section="terms",
        title="Academic terms",
        terms=rows,
        clone_sources=clone_sources,
        draft_revisions=draft_revisions,
        approved_objectives=approved_objectives,
    )


@login_required
@require_GET
def runs(request: HttpRequest) -> HttpResponse:
    rows = [_run_view(run) for run in _safe_list("ScheduleRun", limit=250)]
    snapshots = [_snapshot_view(item) for item in _safe_list("ProblemSnapshot", limit=100)]
    revisions = _safe_list(
        "TermDatasetRevision",
        limit=100,
        filters={"status": "COMMITTED"},
    )
    objectives = _safe_list("ObjectiveProfile", limit=100, filters={"is_approved": True})
    experiments = [_experiment_view(item) for item in _safe_list("ExperimentBatch", limit=100)]
    algorithm = request.GET.get("algorithm", "").strip().lower()
    status = request.GET.get("status", "").strip().lower()
    if algorithm:
        rows = [row for row in rows if slugify(row.algorithm) == slugify(algorithm)]
    if status:
        rows = [row for row in rows if slugify(row.status) == slugify(status)]
    return _render(
        request,
        "scheduler/runs.html",
        section="runs",
        title="Schedule runs",
        runs=rows,
        snapshots=snapshots,
        revisions=revisions,
        objective_profiles=objectives,
        experiments=experiments,
        selected_algorithm=algorithm,
        selected_status=status,
    )


@login_required
@require_GET
def run_detail(request: HttpRequest, pk: str) -> HttpResponse:
    run = _safe_get("ScheduleRun", pk)
    row = _run_view(run) if run is not None else None
    return _render(
        request,
        "scheduler/run_detail.html",
        section="runs",
        title=f"Run {pk}",
        run=row,
        run_id=pk,
    )


@login_required
@require_GET
def run_comparison(request: HttpRequest) -> HttpResponse:
    options = [_run_view(run) for run in _safe_list("ScheduleRun", limit=250)]
    left_id = request.GET.get("left", "")
    right_id = request.GET.get("right", "")
    if not left_id and len(options) >= 1:
        left_id = options[0].id
    if not right_id and len(options) >= 2:
        right_id = options[1].id
    left_model = _safe_get("ScheduleRun", left_id)
    right_model = _safe_get("ScheduleRun", right_id)
    left = _run_view(left_model) if left_model is not None else None
    right = _run_view(right_model) if right_model is not None else None
    comparable = bool(
        left
        and right
        and (
            (left.problem_hash and left.problem_hash == right.problem_hash)
            or (left.snapshot_id and left.snapshot_id == right.snapshot_id)
        )
    )
    return _render(
        request,
        "scheduler/run_comparison.html",
        section="runs",
        title="Compare algorithm runs",
        run_options=options,
        left=left,
        right=right,
        left_id=left_id,
        right_id=right_id,
        comparable=comparable,
    )


@login_required
@require_GET
def experiment_detail(request: HttpRequest, pk: str) -> HttpResponse:
    batch = _safe_get("ExperimentBatch", pk)
    summary = None
    if batch is not None:
        from scheduler.services.experiments import summarize_experiment

        try:
            summary = summarize_experiment(batch)
        except (DatabaseError, TypeError, ValueError, ValidationError):
            summary = None
    return _render(
        request,
        "scheduler/experiment_detail.html",
        section="runs",
        title=f"Experiment {pk}",
        batch=batch,
        summary=summary,
        experiment_id=pk,
    )


@login_required
@require_GET
def schedules(request: HttpRequest) -> HttpResponse:
    schedule_rows = [_schedule_view(item) for item in _safe_list("ScheduleVersion", limit=250)]
    selected_id = request.GET.get("schedule", "")
    if not selected_id and schedule_rows:
        selected_id = schedule_rows[0].id
    selected_model = _safe_get("ScheduleVersion", selected_id)
    selected = _schedule_view(selected_model) if selected_model is not None else None
    assignments = [
        _assignment_view(item)
        for item in _safe_list("ScheduleAssignment", limit=2000, filters={"schedule_id": selected_id})
    ]
    assignments.sort(key=lambda row: (str(row.day), str(row.starts_at), str(row.room)))
    return _render(
        request,
        "scheduler/schedules.html",
        section="schedules",
        title="Timetables",
        schedules=schedule_rows,
        selected_schedule=selected,
        selected_id=selected_id,
        assignments=assignments,
    )


@login_required
@require_GET
def imports(request: HttpRequest) -> HttpResponse:
    terms_list = [_term_view(term) for term in _safe_list("AcademicTerm", limit=100)]
    central = request.user.is_active and (
        request.user.is_superuser
        or getattr(request.user, "role", "") in {"SYSTEM_ADMIN", "CENTRAL_SCHEDULER"}
    )
    batches = [_import_view(batch) for batch in _safe_list("ImportBatch", limit=100)] if central else []
    return _render(
        request,
        "scheduler/imports.html",
        section="imports",
        title="Semester data import",
        terms=terms_list,
        batches=batches,
    )


@login_required
@require_GET
def reviews(request: HttpRequest) -> HttpResponse:
    schedule_model = _model("ScheduleVersion")
    review_model = _model("ScheduleReview")
    college_model = _model("College")
    rows: list[SimpleNamespace] = []
    if schedule_model and review_model and college_model:
        from scheduler.services.workflow import required_review_college_ids

        try:
            schedules_under_review = list(
                schedule_model.objects.filter(status="UNDER_REVIEW").order_by("term", "version_number")
            )
            scoped_ids = set(
                request.user.college_scopes.values_list("college_id", flat=True)
            ) if getattr(request.user, "role", "") == "COLLEGE_REVIEWER" else set()
            central = request.user.is_active and (request.user.is_superuser or getattr(request.user, "role", "") in {
                "SYSTEM_ADMIN",
                "CENTRAL_SCHEDULER",
            })
            for schedule in schedules_under_review:
                for college in college_model.objects.filter(
                    pk__in=required_review_college_ids(schedule)
                ).order_by("code"):
                    if not central and college.pk not in scoped_ids:
                        continue
                    latest = review_model.objects.filter(
                        schedule=schedule,
                        college=college,
                    ).order_by("-created_at").first()
                    if latest:
                        row = _review_view(latest)
                        row.actionable = row.actionable and (central or college.pk in scoped_ids)
                        rows.append(row)
                    else:
                        rows.append(
                            SimpleNamespace(
                                id=f"pending-{schedule.pk}-{college.pk}",
                                schedule=str(schedule),
                                schedule_id=str(schedule.pk),
                                college=str(college),
                                college_id=str(college.pk),
                                reviewer="Unassigned",
                                status="Pending",
                                status_class="pending",
                                comment="",
                                updated_at=schedule.updated_at,
                                actionable=central or college.pk in scoped_ids,
                            )
                        )
        except (DatabaseError, FieldError, TypeError, ValueError, ValidationError):
            rows = [_review_view(review) for review in _safe_list("ScheduleReview", limit=250)]
    return _render(
        request,
        "scheduler/reviews.html",
        section="reviews",
        title="Schedule review",
        reviews=rows,
    )
