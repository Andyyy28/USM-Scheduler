from __future__ import annotations

from dataclasses import replace

from scheduler.domain import (
    Assignment,
    ObjectiveBreakdown,
    ProblemInstance,
    SolverAlgorithm,
    SolverConfig,
    SolverResult,
    SolverStatus,
    ValidationReport,
    Violation,
    ViolationCode,
    score_schedule,
    validate_schedule,
)
from scheduler.services.runs import _verify_solver_result


def _config() -> SolverConfig:
    return SolverConfig(
        algorithm=SolverAlgorithm.CP_SAT,
        seed=91,
        time_limit_seconds=2,
        worker_count=1,
    )


def _result(problem: ProblemInstance, config: SolverConfig) -> SolverResult:
    assignments = (
        Assignment(event_id="E1", candidate_id="E1-M0"),
        Assignment(event_id="E2", candidate_id="E2-T2"),
    )
    return SolverResult(
        algorithm=config.algorithm,
        status=SolverStatus.FEASIBLE,
        assignments=assignments,
        validation=validate_schedule(problem, assignments),
        objective=score_schedule(problem, assignments),
        runtime_seconds=0.1,
        first_feasible_seconds=0.05,
        stopping_reason="Synthetic solver result.",
        seed=config.seed,
        problem_hash=problem.canonical_hash,
        config_hash=config.canonical_hash,
    )


def test_service_verification_accepts_exact_reconstruction(
    balanced_problem: ProblemInstance,
) -> None:
    config = _config()
    verified = _verify_solver_result(balanced_problem, config, _result(balanced_problem, config))

    assert verified.status is SolverStatus.FEASIBLE
    assert dict(verified.metrics)["service_verification_passed"] == 1
    assert verified.objective == score_schedule(balanced_problem, verified.assignments)


def test_service_verification_rejects_problem_and_configuration_hashes(
    balanced_problem: ProblemInstance,
) -> None:
    config = _config()
    reported = replace(
        _result(balanced_problem, config),
        problem_hash="0" * 64,
        config_hash="1" * 64,
    )

    verified = _verify_solver_result(balanced_problem, config, reported)

    assert verified.status is SolverStatus.ERROR
    assert "problem hash" in verified.stopping_reason
    assert "resolved configuration hash" in verified.stopping_reason
    assert dict(verified.metrics)["reported_problem_hash"] == "0" * 64


def test_service_verification_rejects_feasibility_claim_mismatch(
    balanced_problem: ProblemInstance,
) -> None:
    config = _config()
    reported = replace(
        _result(balanced_problem, config),
        validation=ValidationReport(
            feasible=False,
            violations=(
                Violation(
                    code=ViolationCode.MISSING_ASSIGNMENT,
                    message="Fabricated mismatch.",
                    event_ids=("E1",),
                ),
            ),
        ),
    )

    verified = _verify_solver_result(balanced_problem, config, reported)

    assert verified.status is SolverStatus.ERROR
    assert "feasibility" in verified.stopping_reason
    assert verified.validation.feasible


def test_service_verification_rejects_full_objective_breakdown_mismatch(
    balanced_problem: ProblemInstance,
) -> None:
    config = _config()
    reported = _result(balanced_problem, config)
    assert reported.objective is not None
    wrong_objective = ObjectiveBreakdown(
        preference_penalty=reported.objective.preference_penalty,
        section_gap_atoms=reported.objective.section_gap_atoms + 1,
        instructor_gap_atoms=reported.objective.instructor_gap_atoms,
        load_imbalance=reported.objective.load_imbalance,
        weighted_total=reported.objective.weighted_total + 1,
        quality_score=reported.objective.quality_score,
    )

    verified = _verify_solver_result(
        balanced_problem,
        config,
        replace(reported, objective=wrong_objective),
    )

    assert verified.status is SolverStatus.ERROR
    assert "full objective breakdown" in verified.stopping_reason
    assert verified.objective == score_schedule(balanced_problem, verified.assignments)
