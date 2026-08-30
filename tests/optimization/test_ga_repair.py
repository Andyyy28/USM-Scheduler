from __future__ import annotations

from random import Random

import pytest

from scheduler.domain import (
    Assignment,
    CandidatePlacement,
    MeetingEvent,
    ObjectiveProfile,
    ProblemInstance,
    TimeAtom,
    score_schedule,
    validate_schedule,
)
from scheduler.solvers.genetic import _Evaluation, _repair


def _candidate(
    candidate_id: str,
    atom_id: str,
    *,
    room_id: str = "R1",
    preference_penalty: int = 0,
) -> CandidatePlacement:
    return CandidatePlacement(
        candidate_id=candidate_id,
        room_id=room_id,
        day_id="MON",
        start_atom_id=atom_id,
        occupied_atom_ids=(atom_id,),
        preference_penalty=preference_penalty,
    )


def _plateau_problem() -> ProblemInstance:
    return ProblemInstance(
        schema_version="1.0",
        term_revision_id="TWO-MOVE-REPAIR-REGRESSION",
        time_atoms=(TimeAtom("A", "MON", 0, 0), TimeAtom("B", "MON", 0, 1)),
        events=(
            MeetingEvent(
                event_id="E1",
                duration_atoms=1,
                section_ids=("S1",),
                instructor_ids=("I1",),
                candidates=(_candidate("E1-A", "A"), _candidate("E1-B", "B")),
            ),
            MeetingEvent(
                event_id="E2",
                duration_atoms=1,
                section_ids=("S1",),
                instructor_ids=("I2",),
                candidates=(
                    _candidate("E2-A", "A", room_id="R2"),
                    _candidate("E2-B", "B", room_id="R2"),
                ),
            ),
            MeetingEvent(
                event_id="E3",
                duration_atoms=1,
                section_ids=("S2",),
                instructor_ids=("I3",),
                candidates=(_candidate("E3-A", "A"), _candidate("E3-B", "B")),
            ),
        ),
        locked_assignments=(Assignment("E2", "E2-A"),),
        objective_profile=ObjectiveProfile(
            preference_weight=0,
            section_gap_weight=0,
            instructor_gap_weight=0,
            load_imbalance_weight=0,
        ),
    )


def _independent_evaluation(problem: ProblemInstance, chromosome: tuple[int, ...]) -> _Evaluation:
    assignments = tuple(
        Assignment(event.event_id, event.candidates[gene].candidate_id)
        for event, gene in zip(problem.events, chromosome, strict=True)
    )
    validation = validate_schedule(problem, assignments)
    indexes = {event.event_id: index for index, event in enumerate(problem.events)}
    # Deliberately include locked conflict events: repair must enforce locks even
    # when the callback does not prefilter them from the conflict neighborhood.
    involved = tuple(sorted({
        indexes[event_id]
        for violation in validation.violations
        for event_id in violation.event_ids
    }))
    return _Evaluation(
        (validation.hard_violation_count, score_schedule(problem, assignments).weighted_total),
        involved,
    )


def _alias_problem(candidate_count: int, *, increasing_penalties: bool) -> ProblemInstance:
    return ProblemInstance(
        schema_version="1.0",
        term_revision_id="BOUNDED-REPAIR-REGRESSION",
        time_atoms=(TimeAtom("A", "MON", 0, 0),),
        events=tuple(
            MeetingEvent(
                event_id=f"E{index}",
                duration_atoms=1,
                section_ids=("S1",),
                instructor_ids=("I1",),
                candidates=tuple(
                    _candidate(
                        f"E{index}-C{gene}",
                        "A",
                        preference_penalty=gene if increasing_penalties else 0,
                    )
                    for gene in range(candidate_count)
                ),
            )
            for index in range(2)
        ),
    )


def test_two_move_repair_crosses_a_neutral_plateau_and_never_touches_locks() -> None:
    problem = _plateau_problem()
    original = (0, 0, 1)
    initial = _independent_evaluation(problem, original)
    assert initial.fitness == (1, 0)
    for single_move in ((1, 0, 1), (0, 0, 0)):
        assert _independent_evaluation(problem, single_move).fitness >= initial.fitness

    observed = []

    def evaluate(chromosome: tuple[int, ...]) -> _Evaluation:
        assert chromosome[1] == 0
        observed.append(chromosome)
        return _independent_evaluation(problem, chromosome)

    diagnostics: dict[str, int] = {}
    repaired = _repair(
        problem.events, original, {1: 0}, 1, evaluate, Random(3), float("inf"), diagnostics
    )

    assert repaired == (1, 0, 0)
    assert _independent_evaluation(problem, repaired).fitness == (0, 0)
    assert (1, 0, 1) in observed
    assert diagnostics["repair_second_move_improvements"] == 1
    assert diagnostics["repair_iterations"] == diagnostics["repair_successes"] == 1


def test_two_move_repair_does_not_accept_a_worse_final_schedule() -> None:
    problem = _alias_problem(2, increasing_penalties=True)
    original = (0, 0)
    observed = []

    def evaluate(chromosome: tuple[int, ...]) -> _Evaluation:
        observed.append(chromosome)
        return _independent_evaluation(problem, chromosome)

    diagnostics: dict[str, int] = {}
    repaired = _repair(
        problem.events, original, {}, 3, evaluate, Random(3), float("inf"), diagnostics
    )

    assert repaired == original
    assert (1, 1) in observed
    assert _independent_evaluation(problem, (1, 1)).fitness > _independent_evaluation(problem, original).fitness
    assert diagnostics["repair_second_move_evaluations"] > 0
    assert diagnostics.get("repair_second_move_improvements", 0) == 0


def test_zero_repair_attempts_disable_both_single_and_second_moves() -> None:
    problem = _plateau_problem()
    original = (0, 0, 1)
    observed = []

    def evaluate(chromosome: tuple[int, ...]) -> _Evaluation:
        observed.append(chromosome)
        return _independent_evaluation(problem, chromosome)

    diagnostics: dict[str, int] = {}
    repaired = _repair(
        problem.events, original, {1: 0}, 0, evaluate, Random(3), float("inf"), diagnostics
    )

    assert repaired == original
    assert observed == [original]
    assert diagnostics.get("repair_candidate_evaluations", 0) == 0
    assert diagnostics.get("repair_second_move_evaluations", 0) == 0


def test_second_move_budget_counts_cached_evaluation_requests() -> None:
    problem = _alias_problem(70, increasing_penalties=False)
    original = (0, 0)
    neutral = _independent_evaluation(problem, original)
    # Every candidate alias occupies exactly the same resource/time footprint
    # and carries the same preference cost, so all cached evaluations agree.
    cache = {(left, right): neutral for left in range(70) for right in range(70)}
    cache_requests = 0

    def cached_evaluate(chromosome: tuple[int, ...]) -> _Evaluation:
        nonlocal cache_requests
        cache_requests += 1
        return cache[chromosome]

    diagnostics: dict[str, int] = {}
    repaired = _repair(
        problem.events, original, {}, 20, cached_evaluate, Random(3), float("inf"), diagnostics
    )

    assert repaired == original
    assert diagnostics["repair_second_move_evaluations"] == 32
    assert diagnostics["repair_single_move_evaluations"] == 96
    assert diagnostics["repair_max_evaluation_requests"] == 128
    assert diagnostics["repair_budget_exhaustions"] == 1
    assert cache_requests == (
        1 + diagnostics["repair_candidate_evaluations"] + diagnostics["repair_second_move_evaluations"]
    )
    assert diagnostics.get("repair_second_move_improvements", 0) == 0


def test_expired_repair_deadline_skips_all_evaluations(monkeypatch: pytest.MonkeyPatch) -> None:
    problem = _plateau_problem()
    original = (0, 0, 1)
    monkeypatch.setattr("scheduler.solvers.genetic.monotonic", lambda: 2.0)

    def evaluate(chromosome: tuple[int, ...]) -> _Evaluation:
        pytest.fail(f"Repair evaluated {chromosome!r} after its deadline")

    diagnostics: dict[str, int] = {}
    repaired = _repair(problem.events, original, {1: 0}, 20, evaluate, Random(3), 1.0, diagnostics)

    assert repaired == original
    assert diagnostics["repair_deadline_skips"] == 1
    assert diagnostics.get("repair_second_move_evaluations", 0) == 0


def test_coordinated_budget_visits_all_four_retained_bridges() -> None:
    problem = _alias_problem(100, increasing_penalties=False)
    neutral = _independent_evaluation(problem, (0, 0))
    observed = []

    def evaluate(chromosome):
        observed.append(chromosome)
        return neutral

    diagnostics = {}
    assert _repair(problem.events, (0, 0), {}, 20, evaluate, Random(4), float("inf"), diagnostics) == (0, 0)
    # Equal-fitness bridges retain insertion order. Before revisiting any one
    # bridge, coordinated repair tries each of the four retained intermediates.
    bridges = observed[1:5]
    coordinated = observed[96:100]
    assert len(coordinated) == 4
    for bridge, proposal in zip(bridges, coordinated, strict=True):
        changed = next(index for index, gene in enumerate(bridge) if gene)
        assert proposal[changed] == bridge[changed]
        assert proposal[1 - changed] != 0
    assert diagnostics["repair_max_evaluation_requests"] == 128


def test_single_move_budget_is_shared_across_repair_iterations() -> None:
    events = _alias_problem(200, increasing_penalties=True).events[:1]
    requests = []

    def evaluate(chromosome):
        # A deterministic fitness landscape with many successive hard gains.
        # Repeated requests for earlier genes still consume the per-call budget.
        requests.append(chromosome)
        return _Evaluation((200 - chromosome[0], 0), (0,))

    diagnostics = {}
    repaired = _repair(events, (0,), {}, 1000, evaluate, Random(1), float("inf"), diagnostics)
    assert diagnostics["repair_iterations"] > 1
    assert diagnostics["repair_single_move_evaluations"] == 96
    assert len(requests) == diagnostics["repair_total_evaluation_requests"] == 96
    assert diagnostics["repair_max_evaluation_requests"] == 96
    assert repaired[0] > 0


def test_deadline_during_a_neutral_bridge_keeps_the_original_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = _plateau_problem()
    original = (0, 0, 1)
    observed = []
    expired = False
    monkeypatch.setattr("scheduler.solvers.genetic.monotonic", lambda: 2.0 if expired else 0.0)

    def evaluate(chromosome: tuple[int, ...]) -> _Evaluation:
        nonlocal expired
        observed.append(chromosome)
        result = _independent_evaluation(problem, chromosome)
        expired = chromosome != original
        return result

    diagnostics: dict[str, int] = {}
    repaired = _repair(problem.events, original, {1: 0}, 20, evaluate, Random(3), 1.0, diagnostics)

    assert repaired == original
    assert observed == [original, (1, 0, 1)]
    assert diagnostics.get("repair_second_move_evaluations", 0) == 0
