from __future__ import annotations

from collections.abc import Mapping
from statistics import median
from types import SimpleNamespace
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import FieldError, ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import DatabaseError, connection
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.text import slugify
from django.views.decorators.http import require_GET

from scheduler.services.revision_metadata import with_dataset_counts


def _model(name: str) -> Any | None:
    """Resolve domain models at request time so the UI can evolve independently."""
    try:
        return apps.get_model("scheduler", name)
    except LookupError:
        return None


def _safe_list(
    model_name: str,
    *,
    limit: int | None = 100,
    filters: Mapping[str, Any] | None = None,
) -> list[Any]:
    model = _model(model_name)
    if model is None:
        return []
    try:
        queryset = model.objects.all()
        if model_name == "ScheduleRun":
            queryset = queryset.select_related("snapshot__revision__term", "schedule_version")
        if model_name == "ScheduleAssignment":
            queryset = queryset.select_related(
                "meeting_requirement__offering__subject", "room", "start_time_slot",
                "meeting_requirement__offering__offering_department__college",
            ).prefetch_related(
                "meeting_requirement__offering__section_links__section",
                "meeting_requirement__offering__instructor_links__instructor",
            )
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
    schema_version = str(_first(snapshot, "schema_version", default=""))
    revision = _first(snapshot, "revision")
    term_label = str(_first(snapshot, "revision.term", default="—")).replace("-", "–")
    revision_number = _first(revision, "revision_number", default="—")
    event_count = _first(snapshot, "event_count", default="—")
    short_hash = str(snapshot_hash)[:12] if snapshot_hash else "no hash"
    return SimpleNamespace(
        id=str(_first(snapshot, "pk", "id", default="")),
        label=str(snapshot),
        term=str(_first(snapshot, "revision.term", default="—")),
        revision=str(_first(snapshot, "revision", default="—")),
        revision_number=revision_number,
        event_count=event_count,
        candidate_count=_first(snapshot, "candidate_count", default="—"),
        schema_version=schema_version,
        supports_formal_study=schema_version == "1.2",
        short_hash=short_hash,
        option_label=(
            f"{term_label} · Rev {revision_number} · {event_count} meetings · "
            f"snapshot {short_hash}"
        ),
    )


def _revision_view(revision: Any) -> SimpleNamespace:
    term = revision.term
    try:
        source_filename = revision.source_import_batch.original_filename
    except ObjectDoesNotExist:
        source_filename = ""
    source_or_label = source_filename or revision.label or "Unlabelled dataset"
    academic_year = str(term.academic_year).replace("-", "–")
    term_label = f"{academic_year} · {term.get_semester_display()} · {term.campus}"
    return SimpleNamespace(
        id=str(revision.pk),
        option_label=f"{term_label} · Rev {revision.revision_number} · {source_or_label}",
        label=revision.label or "—",
        source_filename=source_filename or "Not available",
        data_origin=revision.get_data_origin_display(),
        committed_at=revision.committed_at,
        section_count=revision.section_count,
        meeting_count=revision.meeting_count,
        room_count=revision.room_count,
        instructor_count=revision.instructor_count,
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
        study_mode=_first(batch, "study.mode", default="EXPLORATORY"),
    )


def _central_user(user: Any) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and (
            getattr(user, "is_superuser", False)
            or getattr(user, "role", "") in {"SYSTEM_ADMIN", "CENTRAL_SCHEDULER"}
        )
    )


def _formal_study_view(study: Any) -> SimpleNamespace:
    from scheduler.services.formal_studies import inspect_formal_study

    try:
        inspection = inspect_formal_study(study)
    except (DatabaseError, TypeError, ValueError, ValidationError):
        inspection = {
            "counts": {},
            "formal_conclusion": {
                "available": False,
                "status": "NO_FORMAL_CONCLUSION_AVAILABLE",
                "reasons": ["REPORT_UNAVAILABLE"],
            },
            "scales": [],
        }
    counts = inspection.get("counts", {})
    by_status = counts.get("by_status", {})
    total = int(counts.get("all_runs", 0) or 0)
    completed = sum(
        int(by_status.get(status, 0) or 0)
        for status in (
            "FEASIBLE",
            "OPTIMAL",
            "INFEASIBLE",
            "NO_SOLUTION",
            "TIMEOUT",
            "CANCELLED",
            "FAILED",
        )
    )
    progress = round(completed / total * 100) if total else 0
    status = _display(study, "status", default="Draft")
    conclusion = inspection.get("formal_conclusion", {})
    return SimpleNamespace(
        raw=study,
        id=str(_first(study, "pk", "id", default="")),
        name=str(study),
        status=status,
        status_class=_status_class(status),
        protocol_version=_first(study, "protocol_version", default="formal-v2"),
        manifest_hash=str(_first(study, "manifest_hash", default="")),
        short_hash=str(_first(study, "manifest_hash", default=""))[:12],
        source_snapshot=str(_first(study, "source_snapshot", default="—")),
        created_at=_first(study, "created_at"),
        total_runs=total,
        completed_runs=completed,
        measured_runs=int(counts.get("included_measured", 0) or 0),
        excluded_runs=int(counts.get("excluded", 0) or 0),
        replacement_runs=int(counts.get("replacements", 0) or 0),
        progress=progress,
        conclusion_available=bool(conclusion.get("available")),
        conclusion_status=str(conclusion.get("status", "NO_FORMAL_CONCLUSION_AVAILABLE")),
        inspection=inspection,
    )


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in {float("inf"), float("-inf")} else None


def _plot_y(value: Any, *, maximum: float, top: float = 32, bottom: float = 218) -> float | None:
    numeric = _number(value)
    if numeric is None or maximum <= 0:
        return None
    bounded = max(0.0, min(maximum, numeric))
    return round(bottom - bounded / maximum * (bottom - top), 2)


def _quartiles(values: list[float]) -> tuple[float, float, float, float, float] | None:
    ordered = sorted(value for value in values if _number(value) is not None)
    if not ordered:
        return None

    def percentile(fraction: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        remainder = position - lower
        return ordered[lower] * (1 - remainder) + ordered[upper] * remainder

    return ordered[0], percentile(0.25), median(ordered), percentile(0.75), ordered[-1]


def _formal_scale_rows(analysis: Mapping[str, Any]) -> list[SimpleNamespace]:
    rows: list[SimpleNamespace] = []
    for index, scale in enumerate((25, 50, 75, 100)):
        detail = _dig(analysis, f"scales.{scale}", {})
        outcomes = _dig(detail, "primary_outcomes", {})
        feasibility = _dig(outcomes, "feasibility", {})
        quality = _dig(outcomes, "schedule_quality", {})
        time_result = _dig(outcomes, "time_to_feasibility", {})
        cp_feasibility = _dig(feasibility, "by_algorithm.CP_SAT", {})
        ga_feasibility = _dig(feasibility, "by_algorithm.GA", {})
        cp_quality = _dig(quality, "by_algorithm.CP_SAT", {})
        ga_quality = _dig(quality, "by_algorithm.GA", {})
        cp_time = _dig(time_result, "by_algorithm.CP_SAT", {})
        ga_time = _dig(time_result, "by_algorithm.GA", {})
        x = 102 + index * 188

        def interval(summary: Mapping[str, Any]) -> tuple[float | None, float | None]:
            values = summary.get("wilson_95")
            if not isinstance(values, list) or len(values) != 2:
                return None, None
            return (
                _plot_y(float(values[1]) * 100, maximum=100),
                _plot_y(float(values[0]) * 100, maximum=100),
            )

        cp_top, cp_bottom = interval(cp_feasibility)
        ga_top, ga_bottom = interval(ga_feasibility)
        rows.append(
            SimpleNamespace(
                scale=scale,
                x=x,
                cp_x=x - 18,
                ga_x=x + 18,
                cp_feasibility=cp_feasibility,
                ga_feasibility=ga_feasibility,
                cp_y=_plot_y(cp_feasibility.get("percentage"), maximum=100),
                ga_y=_plot_y(ga_feasibility.get("percentage"), maximum=100),
                cp_interval_top=cp_top,
                cp_interval_bottom=cp_bottom,
                ga_interval_top=ga_top,
                ga_interval_bottom=ga_bottom,
                feasibility_comparison=feasibility.get("comparison", {}),
                cp_quality=cp_quality,
                ga_quality=ga_quality,
                quality_comparison=quality.get("comparison", {}),
                cp_time=cp_time,
                ga_time=ga_time,
                time_comparison=time_result.get("comparison", {}),
                holm=_dig(detail, "holm_family", {}),
                hard_violations=_dig(detail, "hard_violation_categories", {}),
            )
        )
    return rows


def _km_step_path(points: Any, *, deadline: float) -> str:
    if not isinstance(points, list) or deadline <= 0:
        return ""
    path = ""
    previous_x = 70.0
    previous_y = 32.0
    path = f"M {previous_x:.2f} {previous_y:.2f}"
    for point in points[1:]:
        if not isinstance(point, Mapping):
            continue
        time_value = _number(point.get("time_seconds"))
        survival = _number(point.get("survival_probability"))
        if time_value is None or survival is None:
            continue
        x = 70 + max(0, min(deadline, time_value)) / deadline * 660
        y = 32 + (1 - max(0, min(1, survival))) * 186
        path += f" H {x:.2f} V {y:.2f}"
        previous_x, previous_y = x, y
    if previous_x < 730:
        path += " H 730"
    return path


def _penalty_plot(scale_rows: list[SimpleNamespace]) -> SimpleNamespace:
    all_values: list[float] = []
    series: list[tuple[int, str, list[float]]] = []
    for index, row in enumerate(scale_rows):
        for algorithm, summary in (
            ("CP-SAT", row.cp_quality),
            ("GA", row.ga_quality),
        ):
            values = _dig(summary, "raw_weighted_soft_penalty.values", [])
            numeric = [value for value in (_number(item) for item in values) if value is not None]
            all_values.extend(numeric)
            series.append((index, algorithm, numeric))
    maximum = max(1.0, max(all_values, default=1.0))
    groups: list[SimpleNamespace] = []
    for index, algorithm, values in series:
        summary = _quartiles(values)
        x = 102 + index * 188 + (-18 if algorithm == "CP-SAT" else 18)
        points = [
            SimpleNamespace(x=round(x + ((point_index % 5) - 2) * 2.4, 2), y=_plot_y(value, maximum=maximum))
            for point_index, value in enumerate(values)
        ]
        if summary is None:
            groups.append(SimpleNamespace(algorithm=algorithm, x=x, available=False, points=[]))
            continue
        low, q1, med, q3, high = summary
        groups.append(
            SimpleNamespace(
                algorithm=algorithm,
                x=x,
                available=True,
                low_y=_plot_y(low, maximum=maximum),
                q1_y=_plot_y(q1, maximum=maximum),
                median_y=_plot_y(med, maximum=maximum),
                q3_y=_plot_y(q3, maximum=maximum),
                high_y=_plot_y(high, maximum=maximum),
                box_y=_plot_y(q3, maximum=maximum),
                box_height=round(
                    abs(
                        (_plot_y(q1, maximum=maximum) or 0)
                        - (_plot_y(q3, maximum=maximum) or 0)
                    ),
                    2,
                ),
                points=points,
            )
        )
    return SimpleNamespace(groups=groups, maximum=maximum)


def _distribution_summary(values: list[float], maximum: float) -> SimpleNamespace:
    summary = _quartiles(values)
    if summary is None:
        return SimpleNamespace(available=False, count=0)
    low, q1, med, q3, high = summary
    scale = maximum if maximum > 0 else 1
    return SimpleNamespace(
        available=True,
        count=len(values),
        minimum=low,
        q1=q1,
        median=med,
        q3=q3,
        maximum=high,
        low_percent=round(low / scale * 100, 2),
        q1_percent=round(q1 / scale * 100, 2),
        median_percent=round(med / scale * 100, 2),
        q3_percent=round(q3 / scale * 100, 2),
        high_percent=round(high / scale * 100, 2),
        box_width=round(max(0, q3 - q1) / scale * 100, 2),
        whisker_width=round(max(0, high - low) / scale * 100, 2),
    )


def _execution_distribution_rows(observations: Any) -> list[SimpleNamespace]:
    grouped: dict[tuple[int, str], list[float]] = {}
    for item in observations:
        if not getattr(item, "eligible", False):
            continue
        value = _number(getattr(item, "metadata", {}).get("execution_seconds"))
        if value is not None:
            grouped.setdefault((item.scale_percentage, item.algorithm), []).append(value)
    maximum = max(1.0, max((value for values in grouped.values() for value in values), default=1.0))
    rows: list[SimpleNamespace] = []
    for scale in (25, 50, 75, 100):
        cp = _distribution_summary(grouped.get((scale, "CP_SAT"), []), maximum)
        ga = _distribution_summary(grouped.get((scale, "GA"), []), maximum)
        cp.label, cp.css_class = "CP-SAT", "cp"
        ga.label, ga.css_class = "Genetic Algorithm", "ga"
        rows.append(
            SimpleNamespace(
                scale=scale,
                cp=cp,
                ga=ga,
                distributions=(cp, ga),
                scale_maximum=maximum,
            )
        )
    return rows


def _objective_component_rows(scale_rows: list[SimpleNamespace]) -> list[SimpleNamespace]:
    definitions = (
        ("faculty_preference_penalty", "Faculty preference"),
        ("section_gaps", "Section gaps"),
        ("instructor_gaps", "Instructor gaps"),
        ("daily_load_imbalance", "Daily-load imbalance"),
    )
    rows: list[SimpleNamespace] = []
    for key, label in definitions:
        values: list[SimpleNamespace] = []
        observed: list[float] = []
        for row in scale_rows:
            cp = _number(_dig(row.cp_quality, f"objective_components.{key}.median"))
            ga = _number(_dig(row.ga_quality, f"objective_components.{key}.median"))
            observed.extend(value for value in (cp, ga) if value is not None)
            values.append(SimpleNamespace(scale=row.scale, cp=cp, ga=ga))
        maximum = max(1.0, max(observed, default=1.0))
        for value in values:
            value.cp_percent = round(value.cp / maximum * 100, 2) if value.cp is not None else 0
            value.ga_percent = round(value.ga / maximum * 100, 2) if value.ga is not None else 0
        rows.append(SimpleNamespace(key=key, label=label, values=values))
    return rows


def _instance_rows(study: Any) -> list[SimpleNamespace]:
    rows: list[SimpleNamespace] = []
    for batch in study.batches.select_related("snapshot").order_by("planned_scale_percentage"):
        descriptor = batch.snapshot.instance_characteristics or {}
        candidates = descriptor.get("candidates", {})
        headcounts = descriptor.get("section_headcounts", {})
        rows.append(
            SimpleNamespace(
                scale=batch.planned_scale_percentage,
                actual=batch.actual_scale_percentage,
                offerings=descriptor.get("offerings", "—"),
                meetings=descriptor.get("meetings", "—"),
                sections=descriptor.get("sections", "—"),
                instructors=descriptor.get("instructors", "—"),
                rooms=descriptor.get("rooms", "—"),
                time_atoms=descriptor.get("time_atoms", "—"),
                locks=descriptor.get("locks", "—"),
                candidate_total=candidates.get("total", "—"),
                candidate_min=candidates.get("min", "—"),
                candidate_median=candidates.get("median", "—"),
                candidate_mean=candidates.get("mean", "—"),
                candidate_max=candidates.get("max", "—"),
                required_atoms=descriptor.get("required_meeting_atoms", "—"),
                available_atoms=descriptor.get("available_room_atoms", "—"),
                domain_density=descriptor.get("candidate_domain_density"),
                demand_pressure=descriptor.get("room_time_demand_pressure"),
                availability_density=descriptor.get("availability_density"),
                headcount_min=headcounts.get("min", "—"),
                headcount_median=headcounts.get("median", "—"),
                headcount_mean=headcounts.get("mean", "—"),
                headcount_max=headcounts.get("max", "—"),
                approaching_50=descriptor.get("meetings_approaching_50", "—"),
            )
        )
    return rows


def _diagnostic_summary(study: Any) -> SimpleNamespace:
    runs = list(
        study.batches.filter(planned_scale_percentage=100)
        .values_list("pk", flat=True)
    )
    run_model = _model("ScheduleRun")
    records = (
        list(
            run_model.objects.filter(
                experiment_batch_id__in=runs,
                purpose="MEASURED",
                included_in_analysis=True,
                status__in=["FEASIBLE", "OPTIMAL", "INFEASIBLE", "NO_SOLUTION", "TIMEOUT", "FAILED"],
            ).exclude(
                failure_category__in=["INFRASTRUCTURE", "USER_CANCELLATION", "UNCLASSIFIED"]
            ).prefetch_related(
                "metrics"
            )
        )
        if run_model
        else []
    )

    def metric_values(algorithm: str, name: str) -> list[float]:
        values: list[float] = []
        for run in records:
            if run.algorithm != algorithm:
                continue
            value = next(
                (
                    _number(metric.value)
                    for metric in run.metrics.all()
                    if metric.name == name
                ),
                None,
            )
            if value is None:
                value = _number(_dig(run.diagnostics, f"metrics.{name}"))
            if value is not None:
                values.append(value)
        return values

    def typical(algorithm: str, name: str) -> float | None:
        values = metric_values(algorithm, name)
        return median(values) if values else None

    cp_records = [run for run in records if run.algorithm == "CP_SAT"]
    ga_records = [run for run in records if run.algorithm == "GA"]
    ga_repairs = [
        _number(run.configuration.get("repair_attempts"))
        for run in ga_records
        if _number(run.configuration.get("repair_attempts")) is not None
    ]
    return SimpleNamespace(
        cp=SimpleNamespace(
            recorded=len(cp_records),
            proven_optimal=sum(run.status == "OPTIMAL" for run in cp_records),
            proven_infeasible=sum(run.status == "INFEASIBLE" for run in cp_records),
            relative_gap=(
                median(
                    values
                    for run in cp_records
                    if (values := _number(run.relative_gap)) is not None
                )
                if any(_number(run.relative_gap) is not None for run in cp_records)
                else None
            ),
            branches=typical("CP_SAT", "branches"),
            conflicts=typical("CP_SAT", "conflicts"),
            variables=typical("CP_SAT", "model_variable_count"),
            constraints=typical("CP_SAT", "model_constraint_count"),
        ),
        ga=SimpleNamespace(
            recorded=len(ga_records),
            evaluations=typical("GA", "evaluated_chromosomes"),
            generations=typical("GA", "generations"),
            repair_attempts=median(ga_repairs) if ga_repairs else None,
            repair_successes=typical("GA", "repair_successes"),
            repair_failures=typical("GA", "repair_failures"),
            mutation_operations=typical("GA", "mutation_operations"),
            duplicates=typical("GA", "duplicates_suppressed"),
            mutation_rate=typical("GA", "mutation_rate"),
            stagnation=typical("GA", "stagnation_generations"),
        ),
    )


def _run_view(run: Any) -> SimpleNamespace:
    status = _display(run, "status", default="Unknown")
    status_value = str(_first(run, "status", default=status))
    status = {
        "FEASIBLE": "Schedule found", "OPTIMAL": "Best schedule found",
        "TIMEOUT": "Time limit reached", "NO_SOLUTION": "No timetable found",
        "INFEASIBLE": "Conflicting scheduling rules", "FAILED": "Generation failed",
    }.get(status_value, status)
    algorithm = _display(run, "algorithm", default="Unspecified")
    runtime = _run_metric(run, "execution_seconds", "runtime_seconds", "execution_time_seconds")
    first_feasible = _run_metric(run, "first_feasible_seconds", "time_to_first_feasible")
    configuration = _first(run, "configuration", default={}) or {}
    limit = float(configuration.get("time_limit_seconds", settings.SOLVER_DEFAULT_TIME_LIMIT_SECONDS))
    outcomes = {
        "OPTIMAL": "A valid timetable was generated and its best quality score was proven.",
        "FEASIBLE": "A valid timetable was generated. Open it to review the classes, rooms and times.",
        "TIMEOUT": "The time limit ended before a valid timetable was found. This does not mean the semester is impossible to schedule.",
        "NO_SOLUTION": "The search ended without a valid timetable. A different random seed or a longer search may help.",
        "INFEASIBLE": "The scheduling rules cannot all be satisfied with this checked data. Review availability and locked classes in Prepare Data, then check the revised data again.",
        "FAILED": "Generation stopped because of a system error. Try a new attempt. If it fails again, give the run identifier to the administrator so they can check the server logs.",
        "CANCELLED": "This attempt was cancelled. You can start a new attempt when you are ready.",
        "QUEUED": "This attempt is waiting for the scheduling worker. If it stays queued, ask the administrator to check that the worker is running.",
        "RUNNING": "The solver is searching for a valid timetable. Refresh this page to check the result.",
    }
    return SimpleNamespace(
        raw=run,
        id=str(_first(run, "pk", "id", default="")),
        algorithm=algorithm,
        status=status,
        outcome=outcomes.get(status_value, "Check the generation details below."),
        time_limit=f"{limit:g} seconds",
        retry_time_limit=min(3600, max(300, int(limit * 2) if status_value == "TIMEOUT" else int(limit))),
        algorithm_value=_first(run, "algorithm", default="CP_SAT"),
        first_feasible_only=configuration.get("first_feasible_only", False),
        status_class="error" if status_value == "FAILED" else slugify(status_value.replace("_", "-")),
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
        ) if status_value in {"FEASIBLE", "OPTIMAL"} or _first(run, "result_data.assignments") else "Not evaluated",
        objective=_run_metric(run, "objective", "objective_value", "quality_score", default="—"),
        retry_count=_run_metric(run, "retry_count", "retries", default="—"),
        room_utilization=_run_metric(run, "room_utilization", default="—"),
        seed=_first(run, "seed", default="—"),
        stopping_reason=_run_metric(run, "stopping_reason", default="—"),
        problem_hash=_run_metric(run, "problem_hash", "snapshot.snapshot_hash", default=""),
        config_hash=_run_metric(run, "config_hash", default=""),
        can_cancel=_first(run, "status", default="") in {"QUEUED", "RUNNING"},
        schedule_id=str(_first(run, "schedule_version.pk", default="")),
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
        approved=status_value == "APPROVED",
        can_lock=status_value in {"UNDER_REVIEW", "APPROVED"},
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
    if hasattr(assignment, "resolved_locked"):
        return assignment.resolved_locked
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
    if hasattr(assignment, "resolved_end_time"):
        end_time = assignment.resolved_end_time
    elif allocations is not None:
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


def _workflow_steps(
    *,
    has_term: bool,
    has_import: bool,
    has_prepared_data: bool,
    has_checked_data: bool,
    has_run: bool,
    has_successful_run: bool,
    has_timetable: bool,
    has_review: bool,
    has_approval: bool,
) -> tuple[list[SimpleNamespace], SimpleNamespace]:
    """Build plain-language progress for the active scheduling term."""

    def state(complete: bool, in_progress: bool, ready: bool) -> tuple[str, str]:
        if complete:
            return "Complete", "complete"
        if in_progress:
            return "In progress", "in-progress"
        if ready:
            return "Ready", "ready"
        return "Not started", "not-started"

    term_state = state(has_term, False, False)
    data_state = state(has_prepared_data, has_import, has_term)
    generate_state = state(has_successful_run, has_checked_data or has_run, has_prepared_data)
    timetable_state = state(has_timetable, False, has_successful_run)
    review_state = state(has_approval, has_review, has_timetable)
    definitions = (
        (1, "Set up the academic term", "Confirm the semester, campus, dates, and teaching days.", term_state, "scheduler:terms", "Review academic term" if has_term else "Set up academic term"),
        (2, "Prepare scheduling data", "Upload the semester workbook, check errors, and save a clean version.", data_state, "scheduler:imports", "Review prepared data" if has_prepared_data else "Prepare scheduling data"),
        (3, "Generate a schedule", "Check the prepared data, then let the system build a candidate timetable.", generate_state, "scheduler:runs", "View schedule runs" if has_successful_run else "Generate a schedule"),
        (4, "Check the timetable", "Inspect classes, instructors, rooms, and conflicts before review.", timetable_state, "scheduler:schedules", "Open timetables"),
        (5, "Review and approve", "Collect college decisions and approve the final schedule.", review_state, "scheduler:reviews", "Open review queue"),
    )
    steps = [
        SimpleNamespace(
            number=number,
            title=title,
            description=description,
            state=step_state[0],
            state_class=step_state[1],
            url_name=url_name,
            action=action,
        )
        for number, title, description, step_state, url_name, action in definitions
    ]
    next_step = next((step for step in steps if step.state != "Complete"), steps[-1])
    return steps, next_step


def _render(request: HttpRequest, template: str, *, section: str, title: str, **context: Any) -> HttpResponse:
    is_central = _central_user(request.user)
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
    term_records = _safe_list("AcademicTerm", limit=12)
    active_term_record = next(
        (term for term in term_records if _first(term, "status", default="") == "ACTIVE"),
        term_records[0] if term_records else None,
    )
    active_term = _term_view(active_term_record) if active_term_record is not None else None
    term_id = _first(active_term_record, "pk", "id")
    run_filters = {"snapshot__revision__term_id": term_id} if term_id else None
    schedule_filters = {"term_id": term_id} if term_id else None
    import_filters = {"term_id": term_id} if term_id else None
    revision_filters = {"term_id": term_id, "status": "COMMITTED"} if term_id else {"status": "COMMITTED"}
    snapshot_filters = {"revision__term_id": term_id} if term_id else None
    run_records = _safe_list("ScheduleRun", limit=25, filters=run_filters)
    recent_runs = [_run_view(run) for run in run_records[:6]]
    reviews = [_review_view(review) for review in _safe_list("ScheduleReview", limit=100)]
    central = request.user.is_active and (
        request.user.is_superuser
        or getattr(request.user, "role", "") in {"SYSTEM_ADMIN", "CENTRAL_SCHEDULER"}
    )
    imports = [_import_view(batch) for batch in _safe_list("ImportBatch", limit=1, filters=import_filters)] if central else []
    committed_revisions = _safe_list("TermDatasetRevision", limit=1, filters=revision_filters)
    snapshots = _safe_list("ProblemSnapshot", limit=1, filters=snapshot_filters)
    schedules = [_schedule_view(item) for item in _safe_list("ScheduleVersion", limit=25, filters=schedule_filters)]
    has_successful_run = any(
        _first(run, "status", default="") in {"FEASIBLE", "OPTIMAL"} for run in run_records
    )
    has_approved_schedule = any(schedule.status_value == "APPROVED" for schedule in schedules)
    has_review_schedule = any(schedule.status_value == "UNDER_REVIEW" for schedule in schedules)

    workflow, next_step = _workflow_steps(
        has_term=bool(active_term),
        has_import=bool(imports),
        has_prepared_data=bool(committed_revisions),
        has_checked_data=bool(snapshots),
        has_run=bool(run_records),
        has_successful_run=has_successful_run,
        has_timetable=bool(schedules),
        has_review=has_review_schedule,
        has_approval=has_approved_schedule,
    )
    return _render(
        request,
        "scheduler/dashboard.html",
        section="dashboard",
        title="Scheduling overview",
        active_term=active_term,
        recent_runs=recent_runs,
        latest_import=imports[0] if imports else None,
        workflow=workflow,
        next_step=next_step,
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
    snapshots = [_snapshot_view(item) for item in _safe_list("ProblemSnapshot", limit=100)]
    revision_model = _model("TermDatasetRevision")
    revisions = [] if revision_model is None else [
        _revision_view(revision)
        for revision in with_dataset_counts(
            revision_model.objects.filter(status="COMMITTED").select_related(
                "term", "source_import_batch"
            )
        )
        .order_by("-term__starts_on", "-revision_number")[:100]
    ]
    objectives = _safe_list("ObjectiveProfile", limit=100, filters={"is_approved": True})
    algorithm = request.GET.get("algorithm", "").strip().lower()
    status = request.GET.get("status", "").strip().lower()
    run_model = _model("ScheduleRun")
    filters = {}
    for parameter, value in (("algorithm", algorithm), ("status", status)):
        if value and run_model is not None:
            choices = run_model._meta.get_field(parameter).choices
            aliases = {slugify(str(alias).replace("_", "-")): key
                       for key, label in choices for alias in (key, label)}
            if parameter == "status":
                aliases["error"] = "FAILED"
            filters[parameter] = aliases.get(slugify(value.replace("_", "-")), "__unknown__")
    rows = [_run_view(run) for run in _safe_list("ScheduleRun", limit=250, filters=filters)]
    retry = None
    if request.GET.get("retry"):
        previous = _safe_get("ScheduleRun", request.GET["retry"])
        if previous is not None:
            retry = _run_view(previous)
    return _render(
        request,
        "scheduler/runs.html",
        section="runs",
        title="Schedule runs",
        runs=rows,
        snapshots=snapshots,
        revisions=revisions,
        objective_profiles=objectives,
        selected_algorithm=algorithm,
        selected_status=status,
        retry_run=retry,
        local_execution=settings.CELERY_TASK_ALWAYS_EAGER,
    )


@login_required
@require_GET
def research_tools(request: HttpRequest) -> HttpResponse:
    """Keep thesis evaluation tools available without crowding routine scheduling."""

    snapshots = [_snapshot_view(item) for item in _safe_list("ProblemSnapshot", limit=100)]
    formal_snapshots = [snapshot for snapshot in snapshots if snapshot.supports_formal_study]
    experiments = [
        row
        for item in _safe_list("ExperimentBatch", limit=100)
        if (row := _experiment_view(item)).study_mode != "FORMAL"
    ]
    formal_studies = [
        _formal_study_view(item)
        for item in _safe_list("ExperimentStudy", limit=50, filters={"mode": "FORMAL"})
    ]
    selected_workflow = request.GET.get("workflow", "formal").strip().lower()
    if selected_workflow not in {"formal", "exploratory"}:
        selected_workflow = "formal"
    return _render(
        request,
        "scheduler/research.html",
        section="research",
        title="Research tools",
        snapshots=snapshots,
        formal_snapshots=formal_snapshots,
        experiments=experiments,
        formal_studies=formal_studies,
        selected_workflow=selected_workflow,
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
        and left.id != right.id
        and (
            (left.problem_hash and left.problem_hash == right.problem_hash)
            or (left.snapshot_id and left.snapshot_id == right.snapshot_id)
        )
    )
    return _render(
        request,
        "scheduler/run_comparison.html",
        section="research",
        title="Compare algorithm runs",
        run_options=options,
        left=left,
        right=right,
        left_id=left_id,
        right_id=right_id,
        comparable=comparable,
        same_run_selected=bool(left_id and left_id == right_id),
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
        section="research",
        title=f"Experiment {pk}",
        batch=batch,
        summary=summary,
        experiment_id=pk,
    )


@login_required
@require_GET
def formal_study_detail(request: HttpRequest, pk: str) -> HttpResponse:
    study = _safe_get("ExperimentStudy", pk)
    if study is None or not bool(_first(study, "is_formal", default=False)):
        raise Http404("Formal study not found.")

    from scheduler.services.convergence import study_convergence
    from scheduler.services.formal_studies import inspect_formal_study
    from scheduler.services.research_metrics import (
        analyze_experiment_study,
        trial_observations_from_study,
    )

    try:
        inspection = inspect_formal_study(study)
        integrity = study.protocol_integrity if isinstance(study.protocol_integrity, dict) else {}
        analysis = analyze_experiment_study(
            study,
            protocol_valid=bool(integrity.get("formal_eligible")),
            resamples=10_000,
        )
        observations = trial_observations_from_study(study)
    except (DatabaseError, TypeError, ValueError, ValidationError):
        inspection = None
        analysis = None
        observations = ()

    scale_rows = _formal_scale_rows(analysis or {})
    full_scale = next((row for row in scale_rows if row.scale == 100), None)
    deadline = float(_first(study, "deadline_seconds", default=300))
    cp_km = _dig(full_scale, "cp_time.kaplan_meier", []) if full_scale else []
    ga_km = _dig(full_scale, "ga_time.kaplan_meier", []) if full_scale else []
    counts = inspection.get("counts", {}) if inspection else {}
    by_status = counts.get("by_status", {})
    pending_count = int(by_status.get("QUEUED", 0) or 0) + int(by_status.get("RUNNING", 0) or 0)
    failed_count = int(by_status.get("FAILED", 0) or 0)
    protocol_integrity = (
        study.protocol_integrity if isinstance(study.protocol_integrity, dict) else {}
    )
    return _render(
        request,
        "scheduler/formal_study_detail.html",
        section="research",
        title=f"Formal study {pk}",
        study=study,
        inspection=inspection,
        analysis=analysis,
        scale_rows=scale_rows,
        full_scale=full_scale,
        penalty_plot=_penalty_plot(scale_rows),
        cp_km_path=_km_step_path(cp_km, deadline=deadline),
        ga_km_path=_km_step_path(ga_km, deadline=deadline),
        execution_rows=_execution_distribution_rows(observations),
        component_rows=_objective_component_rows(scale_rows),
        instance_rows=_instance_rows(study),
        diagnostics=_diagnostic_summary(study),
        convergence_sections=study_convergence(study),
        protocol_issues=protocol_integrity.get("issues", []),
        pending_count=pending_count,
        failed_count=failed_count,
        excluded_count=int(counts.get("excluded", 0) or 0),
        replacement_count=int(counts.get("replacements", 0) or 0),
    )


@login_required
@require_GET
def formal_study_evidence(request: HttpRequest, pk: str) -> HttpResponse:
    if not _central_user(request.user):
        raise PermissionDenied("Only central schedulers and system administrators may download raw evidence.")
    study = _safe_get("ExperimentStudy", pk)
    if study is None or not bool(_first(study, "is_formal", default=False)):
        raise Http404("Formal study not found.")

    from scheduler.services.formal_studies import formal_evidence_bundle

    content, filename = formal_evidence_bundle(study)
    response = HttpResponse(content, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
def schedules(request: HttpRequest) -> HttpResponse:
    schedule_rows = [_schedule_view(item) for item in _safe_list("ScheduleVersion", limit=250)]
    selected_id = request.GET.get("schedule", "")
    if not selected_id and schedule_rows:
        selected_id = schedule_rows[0].id
    selected_model = _safe_get("ScheduleVersion", selected_id)
    selected = _schedule_view(selected_model) if selected_model is not None else None
    assignment_models = _safe_list("ScheduleAssignment", limit=None, filters={"schedule_id": selected_id})
    if selected_model is not None and hasattr(selected_model, "snapshot_id"):
        from scheduler.services.assignment_display import prepare_assignments

        prepare_assignments(selected_model, assignment_models)
    assignments = [_assignment_view(item) for item in assignment_models]
    day_labels = (
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    )
    day_order = {label: index for index, label in enumerate(day_labels)}
    assignments.sort(
        key=lambda row: (
            day_order.get(str(row.day), len(day_order)),
            str(row.starts_at),
            str(row.room),
        )
    )
    timetable_days = [
        SimpleNamespace(
            label=label,
            day_index=index,
            assignments=[row for row in assignments if str(row.day) == label],
        )
        for index, label in enumerate(day_labels)
    ]
    return _render(
        request,
        "scheduler/schedules.html",
        section="schedules",
        title="Timetables",
        schedules=schedule_rows,
        selected_schedule=selected,
        selected_id=selected_id,
        assignments=assignments,
        timetable_days=timetable_days,
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


@login_required
@require_GET
def help_guide(request: HttpRequest) -> HttpResponse:
    """Render the role-aware, print-friendly operating guide."""

    return _render(
        request,
        "scheduler/help.html",
        section="help",
        title="Help and user guide",
    )
