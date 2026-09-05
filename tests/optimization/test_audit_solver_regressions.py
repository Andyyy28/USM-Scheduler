from dataclasses import replace
from random import Random

from scheduler.domain import Assignment, SolverAlgorithm, SolverConfig, SolverStatus, validate_schedule
from scheduler.solvers import CpSatSolver
from scheduler.solvers.neighborhood import PlacementGuide
from scripts.benchmark_ga import _load_solver
from tests.optimization.test_thesis_v2_rules import _candidate, _problem


def test_legacy_daily_policy_does_not_create_false_infeasibility():
    problem = replace(_problem(two_events=True, daily_limit=1, no_daily_limit=False), schema_version="1.1")
    assert validate_schedule(problem, (Assignment("E1", "E1-MON"), Assignment("E2", "E2-TUE"))).feasible
    result = CpSatSolver().solve(problem, SolverConfig(algorithm=SolverAlgorithm.CP_SAT, time_limit_seconds=2))
    assert result.validation.feasible


def test_cp_sat_rejected_incumbent_is_error_not_timeout():
    problem = _problem(reserved_atom_ids=("MON0",))
    event = replace(problem.events[0], candidates=(
        problem.events[0].candidates[0], replace(_candidate("E1-TUE", "TUE", "TUE0"), preference_penalty=10),
    ))
    problem = replace(problem, events=(event,))
    assert validate_schedule(problem, (Assignment("E1", "E1-TUE"),)).feasible
    result = CpSatSolver().solve(problem, SolverConfig(algorithm=SolverAlgorithm.CP_SAT, time_limit_seconds=2))
    assert result.status is SolverStatus.ERROR
    assert "independent validator" in result.stopping_reason


def test_disabled_preferences_do_not_change_neighborhood_order(balanced_problem):
    events = balanced_problem.events
    changed = tuple(replace(event, candidates=tuple(
        replace(candidate, preference_penalty=100-index) for index, candidate in enumerate(event.candidates)
    )) for event in events)
    first = PlacementGuide(events, (0, 0), preference_weight=0)
    second = PlacementGuide(changed, (0, 0), preference_weight=0)
    assert first.alternatives(0, 0, Random(7)) == second.alternatives(0, 0, Random(7))


def test_neighborhood_ranking_stops_at_deadline(balanced_problem):
    guide = PlacementGuide(balanced_problem.events, (0, 0))
    assert guide.alternatives(0, 0, Random(7), lambda: 2, 1) == []


def test_archived_solver_binds_own_neighborhood_without_replacing_live_module(tmp_path):
    import scheduler.solvers.neighborhood as live

    source = tmp_path / "genetic.py"
    source.write_text("from scheduler.solvers.neighborhood import ARCHIVE_MARKER\n")
    (tmp_path / "neighborhood.py").write_text("ARCHIVE_MARKER = 'baseline'\n")
    assert _load_solver(source).ARCHIVE_MARKER == "baseline"
    import scheduler.solvers.neighborhood as restored

    assert restored is live
