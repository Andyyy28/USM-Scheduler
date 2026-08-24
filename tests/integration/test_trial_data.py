from __future__ import annotations

import hashlib
from datetime import date
from io import BytesIO

import pytest
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from scheduler import models
from scheduler.domain import SolverAlgorithm, SolverConfig, SolverStatus
from scheduler.services.imports import commit_import, preview_workbook
from scheduler.services.problem_builder import build_problem
from scheduler.services.trial_data import build_trial_workbook_bytes
from scheduler.solvers import CpSatSolver, GeneticAlgorithmSolver, is_ortools_available

pytestmark = pytest.mark.django_db


def _term() -> models.AcademicTerm:
    return models.AcademicTerm.objects.create(
        academic_year="2026-2027",
        semester=models.Semester.FIRST,
        campus="Kabacan",
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 12, 20),
    )


def test_trial_download_is_deterministic_synthetic_and_role_restricted() -> None:
    first = build_trial_workbook_bytes()
    second = build_trial_workbook_bytes()
    assert first == second
    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()

    workbook = load_workbook(BytesIO(first), read_only=True)
    assert "SYNTHETIC TEST DATA ONLY" in workbook.properties.description
    assert workbook["Students"].max_row == 1
    assert workbook["MeetingRequirements"].max_row == 15

    central = models.User.objects.create_user(
        username="trial-central",
        role=models.UserRole.CENTRAL_SCHEDULER,
    )
    reviewer = models.User.objects.create_user(
        username="trial-reviewer",
        role=models.UserRole.COLLEGE_REVIEWER,
    )
    client = APIClient()
    client.force_authenticate(reviewer)
    assert client.get(reverse("api:trial-workbook")).status_code == 403

    client.force_authenticate(central)
    response = client.get(reverse("api:trial-workbook"))
    assert response.status_code == 200
    assert response.content == first
    assert "Synthetic-Trial" in response["Content-Disposition"]


@pytest.mark.skipif(not is_ortools_available(), reason="OR-Tools is not installed")
def test_trial_workbook_imports_preflights_and_is_feasible_for_both_engines() -> None:
    central = models.User.objects.create_user(
        username="trial-scheduler",
        role=models.UserRole.CENTRAL_SCHEDULER,
    )
    batch = preview_workbook(build_trial_workbook_bytes(), _term(), central)

    assert batch.status == models.ImportStatus.PREVIEWED
    assert batch.error_count == 0
    revision = commit_import(batch, central)
    assert revision.sections.count() == 5
    assert revision.course_offerings.count() == 11
    assert models.Student.objects.count() == 0
    assert models.LockedAssignment.objects.count() == 1

    objective = models.ObjectiveProfile.objects.create(
        name="Synthetic trial objective",
        term=revision.term,
        is_approved=True,
        approved_by=central,
    )
    result = build_problem(revision, objective)
    problem = result.problem
    assert len(problem.events) == 14
    assert result.candidate_count > 100
    assert len(problem.locked_assignments) == 1
    assert any(event.requires_laboratory_room for event in problem.events)
    assert any(len(event.section_ids) == 2 for event in problem.events)
    assert any(len(event.instructor_ids) == 2 for event in problem.events)

    cp_result = CpSatSolver().solve(
        problem,
        SolverConfig(
            algorithm=SolverAlgorithm.CP_SAT,
            seed=1001,
            time_limit_seconds=5,
            worker_count=1,
        ),
    )
    assert cp_result.status in {SolverStatus.FEASIBLE, SolverStatus.OPTIMAL}
    assert cp_result.validation.feasible

    ga_result = GeneticAlgorithmSolver().solve(
        problem,
        SolverConfig(
            algorithm=SolverAlgorithm.GENETIC_ALGORITHM,
            seed=1001,
            time_limit_seconds=5,
            worker_count=1,
            population_size=100,
            tournament_size=3,
            max_generations=100,
            repair_attempts=20,
        ),
    )
    assert ga_result.status == SolverStatus.FEASIBLE
    assert ga_result.validation.feasible
