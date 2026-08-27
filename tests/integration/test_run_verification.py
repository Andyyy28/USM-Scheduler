from __future__ import annotations

import json
from dataclasses import replace
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from scheduler import models
from scheduler.domain import ObjectiveBreakdown
from scheduler.services.problem_builder import build_and_store_snapshot
from scheduler.services.runs import build_solver_config, create_run, persist_result
from scheduler.solvers import CpSatSolver, is_ortools_available

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(not is_ortools_available(), reason="OR-Tools is not installed"),
]


def test_rejected_solver_evidence_is_stored_without_promoting_schedule() -> None:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    identifiers = json.loads(output.getvalue().strip().splitlines()[-1])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    objective = models.ObjectiveProfile.objects.get(pk=identifiers["objective_profile_id"])
    user = models.User.objects.get(pk=identifiers["central_user_id"])
    snapshot, built = build_and_store_snapshot(revision, objective, user)
    run = create_run(
        snapshot=snapshot,
        algorithm=models.SolverAlgorithm.CP_SAT,
        requested_by=user,
        seed=73,
        configuration={"time_limit_seconds": 2, "worker_count": 1},
    )
    run.status = models.RunStatus.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at", "updated_at"])

    result = CpSatSolver().solve(built.problem, build_solver_config(run))
    assert result.objective is not None
    wrong = ObjectiveBreakdown(
        preference_penalty=result.objective.preference_penalty,
        section_gap_atoms=result.objective.section_gap_atoms,
        instructor_gap_atoms=result.objective.instructor_gap_atoms,
        load_imbalance=result.objective.load_imbalance,
        weighted_total=result.objective.weighted_total + 1,
        quality_score=result.objective.quality_score,
    )

    persisted = persist_result(run.pk, replace(result, objective=wrong))

    assert persisted.status == models.RunStatus.FAILED
    assert not models.ScheduleVersion.objects.filter(run=persisted).exists()
    assert persisted.diagnostics["metrics"]["service_verification_passed"] == 0
    assert "full objective breakdown" in persisted.stopping_reason
    assert persisted.result_data["metrics"]["reported_objective_weighted_total"] == (
        result.objective.weighted_total + 1
    )
