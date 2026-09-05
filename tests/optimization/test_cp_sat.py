from __future__ import annotations

import pytest

from scheduler.domain import (
    CandidatePlacement,
    MeetingEvent,
    ObjectiveProfile,
    ProblemInstance,
    SolverAlgorithm,
    SolverConfig,
    SolverStatus,
    TimeAtom,
)
from scheduler.solvers import CpSatSolver, is_ortools_available
from scheduler.solvers import cp_sat as cp_sat_module

pytestmark = pytest.mark.skipif(not is_ortools_available(), reason="OR-Tools is not installed")


def config(seed: int = 1001) -> SolverConfig:
    return SolverConfig(
        algorithm=SolverAlgorithm.CP_SAT,
        seed=seed,
        time_limit_seconds=5,
        worker_count=1,
    )


def test_cp_sat_finds_and_proves_known_zero_penalty_optimum(
    balanced_problem: ProblemInstance,
) -> None:
    result = CpSatSolver().solve(balanced_problem, config())

    assert result.status is SolverStatus.OPTIMAL
    assert result.validation.feasible
    assert result.objective is not None
    assert result.objective.weighted_total == 0
    assert result.first_feasible_seconds is not None
    assert result.problem_hash == balanced_problem.canonical_hash
    metrics = dict(result.metrics)
    assert metrics["objective_value"] == 0
    assert metrics["implementation_version"] == "cp-sat-v5"
    assert metrics["model_variable_count"] > 0
    assert metrics["model_constraint_count"] > 0


def test_cp_sat_proves_global_resource_conflict_infeasible(
    conflicting_problem: ProblemInstance,
) -> None:
    result = CpSatSolver().solve(conflicting_problem, config())

    assert result.status is SolverStatus.INFEASIBLE
    assert not result.validation.feasible
    assert result.objective is None
    assert result.assignments == ()
    assert "proved" in result.stopping_reason.lower()


def test_cp_sat_honors_lock(balanced_problem: ProblemInstance) -> None:
    import dataclasses

    from scheduler.domain import Assignment

    problem = dataclasses.replace(
        balanced_problem,
        locked_assignments=(Assignment(event_id="E1", candidate_id="E1-M0"),),
    )

    result = CpSatSolver().solve(problem, config())

    selected = {assignment.event_id: assignment.candidate_id for assignment in result.assignments}
    assert selected["E1"] == "E1-M0"
    assert result.validation.feasible


def test_cp_sat_encoded_soft_components_equal_independent_scorer(
    balanced_problem: ProblemInstance,
) -> None:
    import dataclasses

    from scheduler.domain import Assignment

    problem = dataclasses.replace(
        balanced_problem,
        locked_assignments=(
            Assignment(event_id="E1", candidate_id="E1-M0"),
            Assignment(event_id="E2", candidate_id="E2-M2"),
        ),
    )

    result = CpSatSolver().solve(problem, config())

    assert result.status is SolverStatus.OPTIMAL
    assert result.objective is not None
    assert result.objective.section_gap_atoms == 1
    assert result.objective.instructor_gap_atoms == 1
    assert result.objective.load_imbalance == 8
    assert result.objective.weighted_total == 10
    assert dict(result.metrics)["objective_value"] == 10


def test_cp_sat_rejects_wrong_algorithm_config(balanced_problem: ProblemInstance) -> None:
    wrong = dataclasses_replace_algorithm(config(), SolverAlgorithm.GENETIC_ALGORITHM)
    with pytest.raises(ValueError, match="requires algorithm"):
        CpSatSolver().solve(balanced_problem, wrong)


def test_cp_sat_gap_model_grows_linearly_over_only_the_active_span() -> None:
    small_variables, small_constraints = _gap_model_additions(4)
    large_variables, large_constraints = _gap_model_additions(8)

    assert small_variables == 4 * 4
    assert large_variables == 4 * 8
    assert large_variables == 2 * small_variables
    assert small_constraints == 7 * 4
    assert large_constraints == 7 * 8
    assert large_constraints == 2 * small_constraints


def dataclasses_replace_algorithm(
    value: SolverConfig, algorithm: SolverAlgorithm
) -> SolverConfig:
    import dataclasses

    return dataclasses.replace(value, algorithm=algorithm)


def _gap_model_additions(active_atom_count: int) -> tuple[int, int]:
    atoms = tuple(
        TimeAtom(atom_id=f"M{index}", day_id="MON", day_index=0, order=index)
        for index in range(active_atom_count)
    ) + tuple(
        TimeAtom(atom_id=f"T{index}", day_id="TUE", day_index=1, order=index)
        for index in range(active_atom_count)
    )
    candidates = tuple(
        CandidatePlacement(
            candidate_id=f"C{index}",
            room_id="R1",
            day_id="MON",
            start_atom_id=f"M{index}",
            occupied_atom_ids=(f"M{index}",),
        )
        for index in range(active_atom_count)
    )
    event = MeetingEvent(
        event_id="E1",
        duration_atoms=1,
        section_ids=("S1",),
        instructor_ids=(),
        candidates=candidates,
    )
    problem = ProblemInstance(
        schema_version="1.0",
        term_revision_id=f"LINEAR-{active_atom_count}",
        time_atoms=atoms,
        events=(event,),
        objective_profile=ObjectiveProfile(
            profile_id="linear-gap-v1",
            preference_weight=0,
            section_gap_weight=1,
            instructor_gap_weight=0,
            load_imbalance_weight=0,
        ),
    )
    model = cp_sat_module.cp_model.CpModel()
    variables = {
        (event.event_id, candidate.candidate_id): model.NewBoolVar(
            f"x__{candidate.candidate_id}"
        )
        for candidate in candidates
    }
    model.AddExactlyOne(variables.values())
    before = model.Proto()
    before_variables = len(before.variables)
    before_constraints = len(before.constraints)

    cp_sat_module._gap_expression(
        model,
        problem,
        variables,
        resource_kind="section",
    )

    after = model.Proto()
    return (
        len(after.variables) - before_variables,
        len(after.constraints) - before_constraints,
    )
