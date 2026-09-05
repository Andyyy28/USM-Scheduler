"""Bounded, validator-driven GA neighborhoods. No policy verdict is cached here."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from random import Random
from typing import Protocol

from scheduler.domain.contracts import CandidatePlacement, MeetingEvent

Chromosome = tuple[int, ...]


class Evaluation(Protocol):
    fitness: tuple[int, int]
    conflict_event_indexes: tuple[int, ...]


class PlacementGuide:
    """Cheap placement ordering only; the shared evaluator decides feasibility."""

    def __init__(self, events: tuple[MeetingEvent, ...], chromosome: Chromosome, preference_weight: int = 1):
        self.preference_weight = preference_weight
        self.events = events
        self.occupancy: dict[tuple[str, str, str], set[int]] = defaultdict(set)
        self.groups: dict[tuple[str, str], set[int]] = defaultdict(set)
        for index, (event, gene) in enumerate(zip(events, chromosome, strict=True)):
            candidate = event.candidates[gene]
            for atom in candidate.occupied_atom_ids:
                self.occupancy[("room", candidate.room_id, atom)].add(index)
                for resource in event.instructor_ids:
                    self.occupancy[("instructor", resource, atom)].add(index)
                for resource in event.section_ids:
                    self.occupancy[("section", resource, atom)].add(index)
            if event.distinct_day_group:
                self.groups[(event.distinct_day_group, candidate.day_id)].add(index)

    def blockers(self, index: int, candidate: CandidatePlacement) -> set[int]:
        event = self.events[index]
        result: set[int] = set()
        for atom in candidate.occupied_atom_ids:
            result.update(self.occupancy.get(("room", candidate.room_id, atom), ()))
            for resource in event.instructor_ids:
                result.update(self.occupancy.get(("instructor", resource, atom), ()))
            for resource in event.section_ids:
                result.update(self.occupancy.get(("section", resource, atom), ()))
        if event.distinct_day_group:
            result.update(self.groups.get((event.distinct_day_group, candidate.day_id), ()))
        result.discard(index)
        return result

    def alternatives(
        self, index: int, current: int, rng: Random,
        clock: Callable[[], float] | None = None, deadline: float = float("inf"),
    ) -> list[int]:
        candidates = self.events[index].candidates
        order = [gene for gene in range(len(candidates)) if gene != current]
        rng.shuffle(order)
        ranked = []
        for gene in order:
            if clock is not None and clock() >= deadline:
                return []
            candidate = candidates[gene]
            ranked.append((len(self.blockers(index, candidate)),
                           self.preference_weight * candidate.preference_penalty, gene))
        # Stable ties retain the seeded shuffled order, not the candidate index.
        ranked.sort(key=lambda item: item[:2])
        return [item[2] for item in ranked]

    def moves(self, index: int, current: int, rng: Random,
              clock: Callable[[], float], deadline: float) -> Iterator[tuple[int, int]]:
        # Generator body runs only when this event's stream is actually consumed.
        for gene in self.alternatives(index, current, rng, clock, deadline):
            yield index, gene



def _round_robin(streams: list[Iterator]) -> Iterator:
    pending = deque(streams)
    while pending:
        stream = pending.popleft()
        try:
            yield next(stream)
        except StopIteration:
            continue
        pending.append(stream)


def repair(
    events: tuple[MeetingEvent, ...], chromosome: Chromosome, locked: dict[int, int],
    attempts: int, evaluate: Callable[[Chromosome], Evaluation], rng: Random,
    deadline: float, diagnostics: dict[str, int], clock: Callable[[], float],
    preference_weight: int = 1,
) -> Chromosome:
    def inc(key: str, amount: int = 1) -> None:
        diagnostics[key] = diagnostics.get(key, 0) + amount

    inc("repair_calls")
    if clock() >= deadline:
        inc("repair_deadline_skips")
        return chromosome
    current = chromosome
    evaluation = evaluate(current)
    single_requests, second_requests = 1, 0
    inc("repair_total_evaluation_requests")
    inc("repair_single_move_evaluations")
    initial = evaluation.fitness
    needed = initial[0] > 0
    if needed:
        inc("repair_needed")

    def finish() -> Chromosome:
        diagnostics["repair_max_evaluation_requests"] = max(
            diagnostics.get("repair_max_evaluation_requests", 0), single_requests + second_requests
        )
        if single_requests >= 96 or second_requests >= 32:
            inc("repair_budget_exhaustions")
        if needed:
            inc("repair_successes" if evaluation.fitness[0] == 0 else "repair_failures")
            if evaluation.fitness < initial:
                inc("repair_improvements")
        return current

    for _ in range(attempts):
        if not evaluation.fitness[0] or clock() >= deadline:
            break
        inc("repair_iterations")
        involved = sorted(set(evaluation.conflict_event_indexes) - locked.keys(),
                          key=lambda index: (len(events[index].candidates), index))
        if not involved:
            break
        guide = PlacementGuide(events, current, preference_weight)
        best, best_eval = current, evaluation
        bridges: list[tuple[int, Chromosome, Evaluation]] = []
        # Bind each event index in a lazy stream; unused domains are never ranked.
        streams = [guide.moves(index, current[index], rng, clock, deadline)
                   for index in involved]
        for index, gene in _round_robin(streams):
            if clock() >= deadline:
                return finish()
            if single_requests >= 96:
                break
            trial = list(current)
            trial[index] = gene
            proposal = tuple(trial)
            single_requests += 1
            inc("repair_candidate_evaluations")
            inc("repair_single_move_evaluations")
            inc("repair_total_evaluation_requests")
            observed = evaluate(proposal)
            if clock() >= deadline:
                return finish()
            if observed.fitness < best_eval.fitness:
                best, best_eval = proposal, observed
            if observed.fitness[0] <= evaluation.fitness[0]:
                bridges.append((index, proposal, observed))
                bridges.sort(key=lambda item: item[2].fitness)
                del bridges[4:]
            if best_eval.fitness[0] < evaluation.fitness[0]:
                break

        def second_moves(changed: int, bridge: Chromosome, observed: Evaluation) -> Iterator[Chromosome]:
            bridge_guide = PlacementGuide(events, bridge, preference_weight)
            blockers = bridge_guide.blockers(changed, events[changed].candidates[bridge[changed]])
            # Raw validation groups include non-overlapping daily-load conflicts.
            for left, right in getattr(observed, "conflict_pairs", ()):
                if left == changed:
                    blockers.add(right)
                elif right == changed:
                    blockers.add(left)
            partners = list(set(observed.conflict_event_indexes) - locked.keys() - {changed})
            rng.shuffle(partners)
            partners.sort(key=lambda index: index not in blockers)
            streams = [bridge_guide.moves(index, bridge[index], rng, clock, deadline)
                       for index in partners]
            for index, gene in _round_robin(streams):
                proposal = list(bridge)
                proposal[index] = gene
                yield tuple(proposal)

        best_is_second = False
        if best_eval.fitness[0] >= evaluation.fitness[0] and second_requests < 32:
            for proposal in _round_robin([second_moves(*bridge) for bridge in bridges]):
                if clock() >= deadline:
                    return finish()
                if second_requests >= 32:
                    break
                second_requests += 1
                inc("repair_second_move_evaluations")
                inc("repair_total_evaluation_requests")
                observed = evaluate(proposal)
                if clock() >= deadline:
                    return finish()
                if observed.fitness < best_eval.fitness:
                    best, best_eval = proposal, observed
                    best_is_second = True
                if best_eval.fitness[0] < evaluation.fitness[0]:
                    break
        if best_eval.fitness >= evaluation.fitness:
            break
        current, evaluation = best, best_eval
        if best_is_second:
            inc("repair_second_move_improvements")
        if single_requests >= 96 and second_requests >= 32:
            break
    return finish()


def improve_feasible(
    events: tuple[MeetingEvent, ...], chromosome: Chromosome, fitness: tuple[int, int],
    locked: dict[int, int], evaluate: Callable[[Chromosome], Evaluation], rng: Random,
    deadline: float, diagnostics: dict[str, int], clock: Callable[[], float],
    preference_weight: int = 1,
) -> Chromosome:
    """Up to 64 complete single/swap trials; only feasible strict gains survive."""
    def inc(key: str) -> None:
        diagnostics[key] = diagnostics.get(key, 0) + 1

    if fitness[0] or clock() >= deadline:
        return chromosome
    inc("feasible_improvement_calls")
    current, best_fitness = chromosome, fitness
    mutable = [index for index in range(len(events)) if index not in locked]
    rng.shuffle(mutable)
    guide = PlacementGuide(events, current, preference_weight)
    streams = [guide.moves(index, current[index], rng, clock, deadline)
               for index in mutable]
    requests = 0

    def consider(proposal: Chromosome) -> None:
        nonlocal current, best_fitness, requests, guide
        requests += 1
        inc("feasible_improvement_evaluations")
        observed = evaluate(proposal)
        if clock() < deadline and observed.fitness[0] == 0 and observed.fitness < best_fitness:
            current, best_fitness = proposal, observed.fitness
            guide = PlacementGuide(events, current, preference_weight)
            inc("feasible_improvements")

    for index, gene in _round_robin(streams):
        if requests >= 64 or clock() >= deadline:
            break
        if gene == current[index]:
            continue
        origin = current
        old_placement = events[index].candidates[origin[index]]
        destination = events[index].candidates[gene]
        blockers = sorted(guide.blockers(index, destination) - locked.keys())
        proposal = list(origin)
        proposal[index] = gene
        consider(tuple(proposal))
        # A swap is eligible only when the partner has this exact old placement
        # in its own domain. Room eligibility and meeting lengths remain intact.
        for partner in blockers:
            if requests >= 64 or clock() >= deadline:
                break
            alternatives = [
                other for other, candidate in enumerate(events[partner].candidates)
                if (candidate.room_id, candidate.day_id, candidate.occupied_atom_ids)
                == (old_placement.room_id, old_placement.day_id, old_placement.occupied_atom_ids)
            ]
            if not alternatives:
                continue
            swap = list(proposal)
            swap[partner] = min(alternatives, key=lambda other: preference_weight * events[partner].candidates[other].preference_penalty)
            consider(tuple(swap))
    diagnostics["feasible_improvement_max_requests"] = max(
        diagnostics.get("feasible_improvement_max_requests", 0), requests
    )
    return current
