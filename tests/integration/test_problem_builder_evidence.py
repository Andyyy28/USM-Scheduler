from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from scheduler import models
from scheduler.domain import ProblemInstance
from scheduler.services.problem_builder import build_problem

pytestmark = pytest.mark.django_db


def test_builder_freezes_complete_schema_1_1_hard_rule_evidence() -> None:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    identifiers = json.loads(output.getvalue().strip().splitlines()[-1])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    objective = models.ObjectiveProfile.objects.get(pk=identifiers["objective_profile_id"])

    problem = build_problem(revision, objective).problem
    restored = ProblemInstance.from_dict(problem.to_dict())

    assert restored == problem
    assert problem.schema_version == "1.1"
    assert problem.supports_independent_hard_rule_validation
    assert problem.room_evidence
    assert problem.instructor_evidence
    assert all(event.authorization_requirements for event in problem.events)
    assert any(event.requires_laboratory_room for event in problem.events)

    for event in problem.events:
        for candidate in event.candidates:
            room = problem.room_evidence_map[candidate.room_id]
            assert set(candidate.occupied_atom_ids) <= set(room.available_atom_ids)
            assert set(event.required_capability_ids) <= set(room.capability_ids)
            if event.requires_laboratory_room:
                assert room.room_kind == models.RoomKind.LABORATORY
                assert room.has_laboratory_profile
            for requirement in event.authorization_requirements:
                assert any(
                    grant.classification == requirement.classification
                    and (
                        grant.department_id == requirement.applicable_department_id
                        if grant.department_id is not None
                        else grant.college_id == requirement.applicable_college_id
                    )
                    for grant in room.authorization_grants
                )
        for instructor_id in event.instructor_ids:
            instructor = problem.instructor_evidence_map[instructor_id]
            assert all(
                set(candidate.occupied_atom_ids) <= set(instructor.available_atom_ids)
                for candidate in event.candidates
            )


def test_builder_uses_bounded_bulk_queries_and_is_deterministic() -> None:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    identifiers = json.loads(output.getvalue().strip().splitlines()[-1])
    revision = models.TermDatasetRevision.objects.get(pk=identifiers["revision_id"])
    objective = models.ObjectiveProfile.objects.get(pk=identifiers["objective_profile_id"])

    with CaptureQueriesContext(connection) as captured:
        first = build_problem(revision, objective).problem

    # The bound is independent of the number of rooms, instructors, meetings,
    # availability rows, and preferences in the revision.  It guards against
    # reintroducing related-manager N+1 queries in the canonical builder.
    assert len(captured) <= 16
    assert build_problem(revision, objective).problem.canonical_hash == first.canonical_hash
