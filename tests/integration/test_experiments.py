from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date, time

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from scheduler import models
from scheduler.services import experiments

pytestmark = pytest.mark.django_db


def _experiment_graph(
    suffix: str = "", *, objective_approved: bool = True
) -> dict[str, object]:
    code_suffix = hashlib.sha256(suffix.encode("utf-8")).hexdigest()[:12] if suffix else ""
    user = models.User.objects.create_user(
        username=f"experimenter{suffix}",
        password="test-password",
        role=models.UserRole.CENTRAL_SCHEDULER,
    )
    college = models.College.objects.create(code=f"EC{code_suffix}", name=f"College {suffix}")
    department = models.Department.objects.create(
        college=college, code=f"ED{code_suffix}", name=f"Department {suffix}"
    )
    program = models.Program.objects.create(
        department=department, code=f"EP{code_suffix}", name=f"Program {suffix}"
    )
    subject = models.Subject.objects.create(code=f"ES{code_suffix}", title="Experiments")
    program_subject = models.ProgramSubject.objects.create(
        program=program,
        subject=subject,
        curriculum_version="2026",
        classification=models.SubjectClassification.MAJOR,
        authoritative_college=college,
        authoritative_department=department,
    )
    term = models.AcademicTerm.objects.create(
        academic_year="2026-2027",
        semester=models.Semester.FIRST,
        campus=f"Campus-{suffix}",
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 12, 20),
    )
    revision = models.TermDatasetRevision.objects.create(
        term=term, revision_number=1, created_by=user
    )
    section = models.Section.objects.create(
        revision=revision,
        program=program,
        code=f"BSCS-1A-{code_suffix}",
        year_level=1,
        cohort_status=models.CohortStatus.INCOMING,
    )
    instructor = models.Instructor.objects.create(
        department=department,
        employee_code=f"EF-{code_suffix}",
        display_name="Experiment Faculty",
    )
    room = models.Room.objects.create(
        code=f"ER-{code_suffix}", campus=term.campus, owning_college=college
    )
    slots = (
        models.TimeSlot.objects.create(
            revision=revision,
            day=models.Weekday.MONDAY,
            sequence=0,
            starts_at=time(8, 0),
            ends_at=time(8, 30),
        ),
        models.TimeSlot.objects.create(
            revision=revision,
            day=models.Weekday.MONDAY,
            sequence=1,
            starts_at=time(8, 30),
            ends_at=time(9, 0),
        ),
    )
    offering = models.CourseOffering.objects.create(
        revision=revision,
        subject=subject,
        offering_department=department,
        external_key=f"EO-{code_suffix}",
    )
    models.OfferingSection.objects.create(
        offering=offering, section=section, program_subject=program_subject
    )
    models.OfferingInstructor.objects.create(offering=offering, instructor=instructor)
    meeting = models.MeetingRequirement.objects.create(
        offering=offering,
        component=models.MeetingComponent.LECTURE,
        occurrence_number=1,
        duration_atoms=1,
    )
    objective = models.ObjectiveProfile.objects.create(
        name=f"Experiment {suffix}",
        version=1,
        is_approved=objective_approved,
        approved_by=user if objective_approved else None,
        approved_at=timezone.now() if objective_approved else None,
    )
    candidate_id = f"{meeting.stable_key}:{room.pk}:{slots[0].pk}"
    candidate = {
        "candidate_id": candidate_id,
        "room_id": str(room.pk),
        "day_id": "day:0",
        "start_atom_id": f"slot:{slots[0].pk}",
        "occupied_atom_ids": [f"slot:{slots[0].pk}"],
        "preference_penalty": 0,
        "eligibility_metadata": [],
    }
    snapshot = models.ProblemSnapshot.objects.create(
        revision=revision,
        objective_profile=objective,
        schema_version="1.0",
        input_data={"events": [{"event_id": str(meeting.stable_key), "candidates": [candidate]}]},
        candidate_map={str(meeting.stable_key): [candidate]},
        event_count=1,
        candidate_count=1,
        preprocessing_seconds=0.25,
        created_by=user,
    )
    models.RoomAvailabilityProfile.objects.create(
        revision=revision,
        room=room,
        assume_fully_available=True,
        acknowledged_by=user,
        acknowledged_at=timezone.now(),
    )
    return {
        "user": user,
        "term": term,
        "revision": revision,
        "room": room,
        "slots": slots,
        "meeting": meeting,
        "candidate_id": candidate_id,
        "snapshot": snapshot,
    }


def test_create_batch_freezes_same_snapshot_matrix_and_deterministic_order() -> None:
    graph = _experiment_graph("create")
    snapshot = graph["snapshot"]
    user = graph["user"]

    batch = experiments.create_experiment_batch(
        snapshot,
        user,
        seeds=(1001, 1002, 1003),
        time_limit=17,
        order_seed=44,
        run_configuration={"population_size": 20, "tournament_size": 2},
    )

    assert batch.status == models.ExperimentStatus.DRAFT
    assert batch.study is not None
    assert batch.study.mode == models.ExperimentMode.EXPLORATORY
    assert batch.study.scale_percentages == [100]
    assert batch.study.protocol_integrity["formal_eligible"] is False
    assert batch.runs.count() == 6
    order = experiments.ordered_experiment_runs(batch)
    assert [(run.seed, run.algorithm) for run in order] == [
        (entry["seed"], entry["algorithm"])
        for entry in experiments.deterministic_execution_order((1001, 1002, 1003), 44)
    ]
    assert all(run.snapshot_id == snapshot.pk for run in order)
    assert all(run.configuration["time_limit_seconds"] == 17 for run in order)
    assert all(run.configuration["worker_count"] == 1 for run in order)
    assert batch.memory_limit_mb == experiments.DEFAULT_MEMORY_LIMIT_MB
    manifest = batch.configuration["environment_manifest"]
    assert set(manifest["build"]) == {
        "app_build_id",
        "source_commit",
        "container_image_id",
    }
    assert manifest["python"]["version"]
    assert manifest["logical_cpu_count"]
    assert manifest["packages"]["Django"]
    assert len(manifest["manifest_hash"]) == 64
    for seed in (1001, 1002, 1003):
        assert {run.algorithm for run in order if run.seed == seed} == set(
            models.SolverAlgorithm.values
        )
    assert experiments.deterministic_execution_order((1001, 1002, 1003), 44) == (
        experiments.deterministic_execution_order((1001, 1002, 1003), 44)
    )


def test_experiment_rejects_unapproved_objective_profile() -> None:
    graph = _experiment_graph("unapproved", objective_approved=False)

    with pytest.raises(ValueError, match="approved objective profile"):
        experiments.create_experiment_batch(
            graph["snapshot"], graph["user"], seeds=(1001,), time_limit=5
        )

    assert models.ExperimentBatch.objects.count() == 0


def test_direct_execution_is_sequential_and_continues_after_failure(monkeypatch) -> None:
    graph = _experiment_graph("direct")
    batch = experiments.create_experiment_batch(
        graph["snapshot"], graph["user"], seeds=(1, 2), time_limit=5, order_seed=7
    )
    expected_ids = [run.pk for run in experiments.ordered_experiment_runs(batch)]
    calls: list[int] = []

    def fake_execute(run_id: int):
        calls.append(run_id)
        run = models.ScheduleRun.objects.get(pk=run_id)
        if len(calls) == 1:
            raise RuntimeError("synthetic worker error")
        run.status = (
            models.RunStatus.OPTIMAL
            if run.algorithm == models.SolverAlgorithm.CP_SAT
            else models.RunStatus.FEASIBLE
        )
        run.execution_seconds = 0.5
        run.first_feasible_seconds = 0.25
        run.objective_value = run.seed
        run.result_data = {
            "assignments": [{"event_id": "E", "candidate_id": f"C{run.seed}"}]
        }
        run.save()
        return run

    monkeypatch.setattr(experiments, "execute_run", fake_execute)

    result = experiments.execute_experiment_batch(batch)

    assert calls == expected_ids
    assert result.status == models.ExperimentStatus.FAILED
    assert models.ScheduleRun.objects.get(pk=expected_ids[0]).status == models.RunStatus.FAILED
    assert all(
        status in experiments.TERMINAL_STATUSES
        for status in batch.runs.values_list("status", flat=True)
    )


def test_queue_submission_preserves_frozen_order(monkeypatch) -> None:
    graph = _experiment_graph("queue")
    batch = experiments.create_experiment_batch(
        graph["snapshot"], graph["user"], seeds=(1, 2, 3), order_seed=19
    )
    expected_ids = [run.pk for run in experiments.ordered_experiment_runs(batch)]
    calls: list[int] = []

    def fake_queue(run: models.ScheduleRun):
        calls.append(run.pk)
        models.ScheduleRun.objects.filter(pk=run.pk).update(task_id=f"task-{run.pk}")
        return run

    monkeypatch.setattr(experiments, "queue_run", fake_queue)

    result = experiments.queue_experiment_batch(batch)

    assert calls == expected_ids
    assert result.status == models.ExperimentStatus.QUEUED
    assert all(batch.runs.exclude(task_id="").values_list("task_id", flat=True))
    queued_summary = experiments.summarize_experiment(result)
    assert queued_summary["algorithms"][models.SolverAlgorithm.CP_SAT]["observed_runs"] == 0
    assert queued_summary["algorithms"][models.SolverAlgorithm.CP_SAT]["success_rate"] is None


def test_benchmark_contract_handles_unavailable_partial_complete_and_invalid_states() -> None:
    graph = _experiment_graph("benchmark-states")
    batch = experiments.create_experiment_batch(
        graph["snapshot"], graph["user"], seeds=(1, 2), time_limit=7
    )
    runs = experiments.ordered_experiment_runs(batch)
    cp_runs = [run for run in runs if run.algorithm == models.SolverAlgorithm.CP_SAT]
    ga_runs = [run for run in runs if run.algorithm == models.SolverAlgorithm.GENETIC_ALGORITHM]

    unavailable = experiments.summarize_experiment(batch)["benchmark"]
    assert unavailable["schema_version"] == "1.0"
    assert unavailable["state"] == "unavailable"
    assert unavailable["comparable"] is False
    assert unavailable["algorithm_ids"] == ["CP_SAT", "GA"]

    models.ScheduleRun.objects.filter(pk=cp_runs[0].pk).update(
        status=models.RunStatus.FEASIBLE,
        objective_value=0,
        execution_seconds=1.0,
        first_feasible_seconds=0.5,
    )
    one_sided = experiments.summarize_experiment(batch)["benchmark"]
    assert one_sided["state"] == "preliminary"
    assert one_sided["comparable"] is False
    assert one_sided["comparability_reasons"][0]["code"] == "ONE_SIDED_EVIDENCE"
    assert one_sided["by_algorithm"]["CP_SAT"]["median_feasible_raw_penalty"][
        "value"
    ] == 0.0

    models.ScheduleRun.objects.filter(pk=ga_runs[0].pk).update(
        status=models.RunStatus.NO_SOLUTION,
        execution_seconds=2.0,
    )
    partial = experiments.summarize_experiment(batch)["benchmark"]
    assert partial["state"] == "preliminary"
    assert partial["comparable"] is True
    assert partial["by_algorithm"]["GA"]["median_feasible_raw_penalty"][
        "available"
    ] is False
    assert partial["by_algorithm"]["GA"]["rmst_time_to_feasibility_seconds"][
        "value"
    ] == 7.0

    models.ScheduleRun.objects.filter(pk__in=[cp_runs[1].pk, ga_runs[1].pk]).update(
        status=models.RunStatus.NO_SOLUTION,
        execution_seconds=7.0,
    )
    complete = experiments.summarize_experiment(batch)["benchmark"]
    assert complete["state"] == "complete"
    assert complete["comparable"] is True
    assert complete["by_algorithm"]["CP_SAT"]["planned_runs"] == 2
    assert complete["by_algorithm"]["CP_SAT"]["observed_runs"] == 2

    invalid_graph = _experiment_graph("benchmark-invalid")
    invalid_batch = experiments.create_experiment_batch(
        invalid_graph["snapshot"], invalid_graph["user"], seeds=(1,), time_limit=7
    )
    corrupt = invalid_batch.runs.order_by("pk").first()
    corrupt.configuration = {**corrupt.configuration, "time_limit_seconds": 8}
    corrupt.save(update_fields=["configuration", "updated_at"])
    models.ScheduleRun.objects.filter(pk=corrupt.pk).update(
        status=models.RunStatus.FAILED,
        diagnostics={
            "metrics": {
                "service_verification_performed": 1,
                "service_verification_passed": 0,
                "reported_config_hash": "0" * 64,
            }
        },
    )
    invalid = experiments.summarize_experiment(invalid_batch)["benchmark"]
    assert invalid["state"] == "invalid"
    assert invalid["comparable"] is False
    assert {issue["code"] for issue in invalid["protocol_integrity"]["issues"]} >= {
        "DEADLINE_MISMATCH",
        "RESULT_VERIFICATION_FAILURE",
    }


def test_run_comparison_counts_only_terminal_runs_and_accepts_experiment_batches() -> None:
    graph = _experiment_graph("comparison-counts")
    batch = experiments.create_experiment_batch(
        graph["snapshot"], graph["user"], seeds=(1, 2), time_limit=5
    )
    ga_run = batch.runs.get(algorithm=models.SolverAlgorithm.GENETIC_ALGORITHM, seed=1)
    models.ScheduleRun.objects.filter(pk=ga_run.pk).update(
        status=models.RunStatus.FEASIBLE,
        objective_value=4,
        execution_seconds=1.5,
        first_feasible_seconds=0.75,
    )
    client = APIClient()
    client.force_authenticate(graph["user"])

    response = client.get(
        reverse("api:run-comparison"), {"experiment_batch_id": batch.pk}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"]["type"] == "controlled_experiment"
    assert payload["CP_SAT"]["planned_runs"] == 2
    assert payload["CP_SAT"]["observed_runs"] == 0
    assert payload["CP_SAT"]["pending_runs"] == 2
    assert payload["CP_SAT"]["runs"] == 0
    assert payload["CP_SAT"]["success_rate"] is None
    assert payload["GA"]["planned_runs"] == 2
    assert payload["GA"]["observed_runs"] == 1
    assert payload["GA"]["pending_runs"] == 1
    assert payload["GA"]["success_rate"] == 1.0


@pytest.mark.parametrize(
    ("left_configuration", "right_configuration", "dimension"),
    [
        (
            {"time_limit_seconds": 5, "worker_count": 1},
            {"time_limit_seconds": 6, "worker_count": 1},
            "time_limit_seconds",
        ),
        (
            {"time_limit_seconds": 5, "worker_count": 1},
            {"time_limit_seconds": 5, "worker_count": 2},
            "worker_count",
        ),
        (
            {
                "time_limit_seconds": 5,
                "worker_count": 1,
                "population_size": 20,
                "tournament_size": 2,
            },
            {
                "time_limit_seconds": 5,
                "worker_count": 1,
                "population_size": 40,
                "tournament_size": 2,
            },
            "configuration_hashes_by_algorithm",
        ),
        (
            {
                "time_limit_seconds": 5,
                "worker_count": 1,
                "implementation_version": "ga-v1",
            },
            {
                "time_limit_seconds": 5,
                "worker_count": 1,
                "implementation_version": "ga-v2",
            },
            "implementation_versions_by_algorithm",
        ),
    ],
)
def test_snapshot_run_comparison_rejects_heterogeneous_evidence(
    left_configuration: dict[str, object],
    right_configuration: dict[str, object],
    dimension: str,
) -> None:
    graph = _experiment_graph(f"heterogeneous-{dimension}")
    for seed, configuration in enumerate(
        (left_configuration, right_configuration), start=1
    ):
        models.ScheduleRun.objects.create(
            snapshot=graph["snapshot"],
            algorithm=models.SolverAlgorithm.GENETIC_ALGORITHM,
            seed=seed,
            configuration=configuration,
            requested_by=graph["user"],
        )
    client = APIClient()
    client.force_authenticate(graph["user"])

    response = client.get(
        reverse("api:run-comparison"), {"snapshot_id": graph["snapshot"].pk}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "HETEROGENEOUS_COMPARISON"
    assert dimension in response.json()["heterogeneous_dimensions"]


def test_terminal_solver_version_is_authoritative_and_mismatch_invalidates_evidence() -> None:
    graph = _experiment_graph("implementation-provenance")
    batch = experiments.create_experiment_batch(
        graph["snapshot"],
        graph["user"],
        seeds=(1,),
        time_limit=5,
        run_configuration={"implementation_version": "ga-v2"},
    )
    ga_run = batch.runs.get(algorithm=models.SolverAlgorithm.GENETIC_ALGORITHM)
    models.ScheduleRun.objects.filter(pk=ga_run.pk).update(
        status=models.RunStatus.FEASIBLE,
        execution_seconds=1.0,
        first_feasible_seconds=0.5,
        objective_value=0,
        diagnostics={"metrics": {"implementation_version": "ga-v1"}},
    )
    ga_run.refresh_from_db()

    assert experiments.run_implementation_version(ga_run) == "ga-v1"
    heterogeneous = experiments.snapshot_comparison_heterogeneity([ga_run])
    assert heterogeneous["implementation_provenance_mismatch_run_ids"] == [ga_run.pk]

    summary = experiments.summarize_experiment(batch)
    assert summary["benchmark"]["state"] == "invalid"
    assert "IMPLEMENTATION_PROVENANCE_MISMATCH" in {
        issue["code"] for issue in summary["benchmark"]["protocol_integrity"]["issues"]
    }


def test_summary_statistics_hamming_retry_cap_room_utilization_and_exports() -> None:
    graph = _experiment_graph("report")
    batch = experiments.create_experiment_batch(
        graph["snapshot"],
        graph["user"],
        seeds=(1, 2, 3, 4, 5, 6),
        order_seed=3,
        run_configuration={"population_size": 40, "tournament_size": 5},
    )
    runs = experiments.ordered_experiment_runs(batch)
    for run in runs:
        is_cp = run.algorithm == models.SolverAlgorithm.CP_SAT
        feasible = is_cp or run.seed in {1, 2}
        run.status = (
            models.RunStatus.OPTIMAL
            if is_cp and feasible
            else models.RunStatus.FEASIBLE
            if feasible
            else models.RunStatus.NO_SOLUTION
        )
        run.execution_seconds = float(run.seed + (0 if is_cp else 1))
        run.first_feasible_seconds = float(run.seed) / 10 if feasible else None
        raw_objective = {
            "preference_penalty": 10 if is_cp else 0,
            "section_gap_atoms": 0 if is_cp else 6,
            "instructor_gap_atoms": 0,
            "load_imbalance": 0,
            "weighted_total": 10 if is_cp else 6,
            "quality_score": 90.0 if is_cp else 94.0,
        }
        run.objective_value = (10 if is_cp else 6) if feasible else None
        run.hard_violation_count = 0 if feasible else 2
        run.result_data = {
            "assignments": [
                {
                    "event_id": "E",
                    "candidate_id": f"C{run.seed % 2 if is_cp else (run.seed + 1) % 2}",
                }
            ],
            "objective": raw_objective if feasible else None,
        }
        if is_cp:
            run.configuration = {
                **run.configuration,
                "retry_episode_id": "manual-1",
                "retry_attempt": run.seed,
            }
        run.save()
        models.ValidationResult.objects.create(
            run=run,
            is_feasible=feasible,
            hard_violation_count=run.hard_violation_count,
            violations={
                "counts": {} if feasible else {"ROOM_CONFLICT": 2},
                "violations": [],
            },
            raw_soft_penalty=run.objective_value or 0,
            objective_breakdown=raw_objective if feasible else {},
            normalized_quality_score=(90.0 if is_cp else 94.0) if feasible else None,
        )
        models.RunMetric.objects.bulk_create(
            [
                models.RunMetric(
                    run=run,
                    name="shared_preprocessing_seconds",
                    value=0.25,
                    unit="seconds",
                ),
                models.RunMetric(
                    run=run,
                    name="independent_validation_seconds",
                    value=run.seed / 1000,
                    unit="seconds",
                ),
                models.RunMetric(
                    run=run,
                    name="end_to_end_processing_seconds",
                    value=run.execution_seconds + 0.5,
                    unit="seconds",
                ),
            ]
            + (
                [
                    models.RunMetric(
                        run=run,
                        name="mutation_rate",
                        value=1.0,
                    )
                ]
                if not is_cp
                else []
            )
        )

    cp_run = next(
        run
        for run in runs
        if run.algorithm == models.SolverAlgorithm.CP_SAT and run.seed == 1
    )
    schedule = models.ScheduleVersion.objects.create(
        term=graph["term"],
        revision=graph["revision"],
        snapshot=graph["snapshot"],
        run=cp_run,
        version_number=1,
        name="Experiment schedule",
        source=models.ScheduleSource.CP_SAT,
        created_by=graph["user"],
    )
    assignment = models.ScheduleAssignment.objects.create(
        schedule=schedule,
        meeting_requirement=graph["meeting"],
        room=graph["room"],
        start_time_slot=graph["slots"][0],
        placement_data={"candidate_id": graph["candidate_id"]},
    )
    models.ScheduleRoomAllocation.objects.create(
        schedule=schedule,
        assignment=assignment,
        room=graph["room"],
        time_slot=graph["slots"][0],
    )
    batch.status = models.ExperimentStatus.RUNNING
    batch.save(update_fields=["status", "updated_at"])

    summary = experiments.summarize_experiment(batch)

    assert summary["batch"]["status"] == models.ExperimentStatus.COMPLETED
    assert summary["algorithms"][models.SolverAlgorithm.CP_SAT]["success_rate"] == 1.0
    assert summary["algorithms"][models.SolverAlgorithm.GENETIC_ALGORITHM]["success_rate"] == pytest.approx(
        2 / 6
    )
    assert summary["algorithms"][models.SolverAlgorithm.GENETIC_ALGORITHM][
        "hard_violation_vector"
    ] == {"ROOM_CONFLICT": 8}
    assert summary["algorithms"][models.SolverAlgorithm.GENETIC_ALGORITHM][
        "rmst_censored_runs"
    ] == 4
    assert summary["effect_sizes"]["cp_sat_probability_lower_feasible_penalty_a12"] is not None
    assert summary["algorithms"][models.SolverAlgorithm.CP_SAT][
        "feasible_soft_penalty_median_bootstrap_95"
    ] is not None
    cp_summary = summary["algorithms"][models.SolverAlgorithm.CP_SAT]
    ga_summary = summary["algorithms"][models.SolverAlgorithm.GENETIC_ALGORITHM]
    assert summary["quality_metric_policy"]["primary"] == "feasible_soft_penalty"
    assert summary["quality_metric_policy"]["normalizer_review"] == {
        "status": "placeholder_defaults",
        "requires_stakeholder_review": True,
        "all_default_denominators_are_one": True,
        "default_components": sorted(models.default_objective_normalizers()),
        "message": (
            "All objective normalizers use the placeholder denominator 1; interpret "
            "the normalized quality score as secondary until stakeholder review."
        ),
    }
    assert cp_summary["feasible_penalty_per_meeting"]["median"] == 10.0
    assert cp_summary["feasible_normalized_quality_score"]["median"] == 90.0
    assert cp_summary["feasible_objective_components"]["preference_penalty"][
        "median"
    ] == 10.0
    assert ga_summary["feasible_objective_components"]["section_gap_atoms"][
        "median"
    ] == 6.0
    assert cp_summary["shared_preprocessing_seconds"]["median"] == 0.25
    assert cp_summary["independent_validation_seconds"]["count"] == 6
    assert cp_summary["end_to_end_processing_seconds"]["count"] == 6
    cp_configuration = cp_summary["solver_configuration_by_run"][str(cp_run.pk)]
    assert cp_configuration["resolved"]["population_size"] == 40
    assert cp_configuration["persisted"]["tournament_size"] == 5
    ga_configuration = next(iter(ga_summary["solver_configuration_by_run"].values()))
    assert ga_configuration["effective_parameters"]["mutation_rate"] == 1.0
    comparison = summary["comparative_tests"]
    assert "not statistical pairing" in comparison["pairing_assumption"]
    assert comparison["outcomes"]["feasible_generation"]["available"]
    assert 0 <= comparison["outcomes"]["feasible_generation"]["p_value_holm_adjusted"] <= 1
    assert summary["placement_consistency"]["by_algorithm"][models.SolverAlgorithm.CP_SAT][
        "pairwise_comparisons"
    ] == 15
    retry = summary["retry_episodes"]
    assert retry["observed_episodes"] == 1
    assert retry["episodes"][0]["attempts_considered"] == 5
    assert retry["episodes"][0]["excluded_beyond_cap"] == 1
    utilization = summary["room_utilization"]
    assert utilization["available"]
    assert utilization["observations"][0]["utilization"] == 0.5
    sensitivity = summary["objective_weight_sensitivity"]
    assert sensitivity["available"]
    assert sensitivity["nominal"]["winner"] == models.SolverAlgorithm.GENETIC_ALGORITHM
    preference_half = next(
        scenario
        for scenario in sensitivity["scenarios"]
        if scenario["component"] == "preference_penalty" and scenario["multiplier"] == 0.5
    )
    assert preference_half["winner"] == models.SolverAlgorithm.CP_SAT
    assert preference_half["nominal_winner_changed"] is True
    assert sensitivity["nominal_winner_changes"] is True
    decision = summary["primary_engine_decision"]
    assert summary["formal_conclusion"] == "No formal conclusion available."
    assert decision["formal_claimable"] is False
    assert decision["evidence_class"] == "EXPLORATORY"
    assert decision["lexicographic_order"] == [
        "feasibility",
        "feasible_schedule_quality",
        "time_to_feasibility",
    ]
    assert decision["rationale"]
    assert decision["thresholds"]["quality_median_relative_reduction"] == 0.05

    json_payload = json.loads(experiments.export_experiment_json(batch))
    assert len(json_payload["runs"]) == 12
    csv_rows = list(csv.DictReader(io.StringIO(experiments.export_experiment_csv(batch).decode())))
    assert len(csv_rows) == 12
    assert [int(row["run_id"]) for row in csv_rows] == [run.pk for run in runs]
    assert json_payload["summary"]["batch"]["environment_manifest"]["packages"]["ortools"]
    assert json_payload["summary"]["batch"]["requested_run_configuration"] == {
        "population_size": 40,
        "tournament_size": 5,
    }
    exported_cp_run = next(row for row in json_payload["runs"] if row["run_id"] == cp_run.pk)
    assert exported_cp_run["raw_soft_penalty"] == 10.0
    assert exported_cp_run["meeting_count"] == 1
    assert exported_cp_run["penalty_per_meeting"] == 10.0
    assert exported_cp_run["normalized_quality_score"] == 90.0
    assert exported_cp_run["objective_breakdown"]["preference_penalty"] == 10
    assert exported_cp_run["shared_preprocessing_seconds"] == 0.25
    assert exported_cp_run["independent_validation_seconds"] == 0.001
    assert exported_cp_run["end_to_end_processing_seconds"] == 1.5
    assert exported_cp_run["solver_configuration"]["resolved"]["population_size"] == 40
    assert json.loads(csv_rows[0]["environment_manifest"])["manifest_hash"]
    exported_csv_cp = next(row for row in csv_rows if int(row["run_id"]) == cp_run.pk)
    assert exported_csv_cp["primary_engine_winner"] == (decision["winner"] or "")
    assert exported_csv_cp["primary_engine_decision_rationale"] == decision["rationale"]
    assert float(exported_csv_cp["raw_soft_penalty"]) == 10.0
    assert int(exported_csv_cp["meeting_count"]) == 1
    assert float(exported_csv_cp["penalty_per_meeting"]) == 10.0
    assert float(exported_csv_cp["normalized_quality_score"]) == 90.0
    assert json.loads(exported_csv_cp["objective_breakdown"])["weighted_total"] == 10
    assert json.loads(exported_csv_cp["solver_configuration"])["resolved"][
        "population_size"
    ] == 40


def test_primary_engine_decision_applies_preregistered_lexicographic_thresholds() -> None:
    def summaries(
        cp_rate: float,
        ga_rate: float,
        cp_penalty: float,
        ga_penalty: float,
        cp_rmst: float,
        ga_rmst: float,
    ) -> dict[str, dict[str, object]]:
        return {
            models.SolverAlgorithm.CP_SAT: {
                "success_rate": cp_rate,
                "feasible_soft_penalty": {"median": cp_penalty},
                "rmst_time_to_feasibility_seconds": cp_rmst,
            },
            models.SolverAlgorithm.GENETIC_ALGORITHM: {
                "success_rate": ga_rate,
                "feasible_soft_penalty": {"median": ga_penalty},
                "rmst_time_to_feasibility_seconds": ga_rmst,
            },
        }

    def tests(
        feasibility_p: float,
        quality_p: float,
        time_p: float = 1.0,
        time_difference: float = -1.0,
    ) -> dict:
        return {
            "outcomes": {
                "feasible_generation": {
                    "available": True,
                    "p_value_holm_adjusted": feasibility_p,
                },
                "feasible_soft_penalty": {
                    "available": True,
                    "p_value_holm_adjusted": quality_p,
                },
                "censored_time_to_feasibility_seconds": {
                    "available": True,
                    "p_value_holm_adjusted": time_p,
                    "observed_difference_first_minus_second": time_difference,
                },
            }
        }

    feasibility = experiments._primary_engine_decision(
        summaries(0.90, 0.80, 20, 10, 100, 50), tests(0.04, 0.01)
    )
    assert feasibility["winner"] == models.SolverAlgorithm.CP_SAT
    assert feasibility["deciding_tier"] == "feasibility"

    unresolved_feasibility = experiments._primary_engine_decision(
        summaries(0.90, 0.80, 100, 10, 100, 50), tests(0.40, 0.01)
    )
    assert unresolved_feasibility["winner"] is None
    assert unresolved_feasibility["deciding_tier"] == "feasibility"
    assert unresolved_feasibility["decision_status"] == "unresolved_feasibility"
    assert unresolved_feasibility["tiers"]["feasible_schedule_quality"]["applicable"] is False

    quality = experiments._primary_engine_decision(
        summaries(0.90, 0.88, 100, 90, 100, 50), tests(0.80, 0.04)
    )
    assert quality["winner"] == models.SolverAlgorithm.GENETIC_ALGORITHM
    assert quality["deciding_tier"] == "feasible_schedule_quality"
    assert quality["tiers"]["feasible_schedule_quality"]["relative_reduction"] == 0.10

    time_decision = experiments._primary_engine_decision(
        summaries(0.90, 0.88, 100, 96, 80, 100), tests(0.80, 0.04)
    )
    assert time_decision["winner"] == models.SolverAlgorithm.CP_SAT
    assert time_decision["deciding_tier"] == "time_to_feasibility"

    disagreeing_time = experiments._primary_engine_decision(
        summaries(0.90, 0.88, 100, 96, 80, 100),
        tests(0.80, 0.04, time_difference=1.0),
    )
    assert disagreeing_time["winner"] is None
    assert disagreeing_time["tiers"]["time_to_feasibility"][
        "censored_time_direction_agrees"
    ] is False

    no_winner = experiments._primary_engine_decision(
        summaries(0.90, 0.88, 100, 96, 95, 100), tests(0.80, 0.80)
    )
    assert no_winner["winner"] is None
    assert no_winner["no_forced_winner"] is True
    assert no_winner["deciding_tier"] is None


def test_management_command_defaults_to_non_mutating_plan() -> None:
    graph = _experiment_graph("command")
    stdout = io.StringIO()

    call_command(
        "run_comparison_experiment",
        graph["snapshot"].pk,
        "--seeds",
        "1001-1002",
        "--order-seed",
        "9",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["dry_run"] is True
    assert [row["algorithm"] for row in payload["warm_up_runs"]] == list(
        models.SolverAlgorithm.values
    )
    assert all(row["measured"] is False for row in payload["warm_up_runs"])
    assert len(payload["runs"]) == 4
    assert models.ExperimentBatch.objects.count() == 0
