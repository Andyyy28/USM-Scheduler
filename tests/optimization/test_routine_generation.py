from dataclasses import replace

import pytest

from scheduler.domain import SolverAlgorithm, SolverConfig, SolverStatus
from scheduler.solvers import CpSatSolver, GeneticAlgorithmSolver


def test_quick_mode_never_claims_quality_optimality_or_changes_config_defaults():
    config = SolverConfig(algorithm=SolverAlgorithm.CP_SAT)
    assert "first_feasible_only" not in config.to_dict()
    quick = replace(config, first_feasible_only=True)
    assert quick.canonical_hash != config.canonical_hash
    assert SolverConfig.from_dict(quick.to_dict()) == quick
    with pytest.raises(ValueError, match="must be Boolean"):
        replace(config, first_feasible_only="false")


@pytest.mark.parametrize("algorithm,solver", [
    (SolverAlgorithm.CP_SAT, CpSatSolver()),
    (SolverAlgorithm.GENETIC_ALGORITHM, GeneticAlgorithmSolver()),
])
def test_quick_mode_does_not_relax_conflicting_rules(conflicting_problem, algorithm, solver):
    result = solver.solve(conflicting_problem, SolverConfig(
        algorithm=algorithm, time_limit_seconds=1, first_feasible_only=True,
    ))
    assert result.status in {SolverStatus.INFEASIBLE, SolverStatus.NO_SOLUTION}
    assert not result.validation.feasible
