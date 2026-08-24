"""Seeded custom Genetic Algorithm for university timetabling."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from random import Random
from time import monotonic

from scheduler.domain.contracts import (
    Assignment,
    CandidatePlacement,
    MeetingEvent,
    ProblemInstance,
    SolverAlgorithm,
    SolverConfig,
    SolverResult,
    SolverStatus,
)
from scheduler.domain.scoring import score_schedule
from scheduler.domain.validation import validate_schedule

Chromosome = tuple[int, ...]
Fitness = tuple[int, int]


@dataclass(frozen=True, slots=True)
class _Individual:
    chromosome: Chromosome
    fitness: Fitness


class GeneticAlgorithmSolver:
    """A transparent, hard-first, single-threaded GA implementation."""

    algorithm = SolverAlgorithm.GENETIC_ALGORITHM

    def solve(self, problem: ProblemInstance, config: SolverConfig) -> SolverResult:
        if config.algorithm is not self.algorithm:
            raise ValueError(
                f"GeneticAlgorithmSolver requires algorithm={self.algorithm.value}"
            )
        if config.worker_count != 1:
            raise ValueError("GeneticAlgorithmSolver is intentionally single-threaded")

        started_at = monotonic()
        deadline = started_at + config.time_limit_seconds
        rng = Random(config.seed)
        events = tuple(sorted(problem.events, key=lambda event: event.event_id))
        event_indexes = {event.event_id: index for index, event in enumerate(events)}
        locked_genes = {
            event_indexes[event_id]: _candidate_index(events[event_indexes[event_id]], candidate_id)
            for event_id, candidate_id in problem.lock_map.items()
        }
        mutation_rate = config.mutation_rate
        if mutation_rate is None:
            mutation_rate = 1.0 / max(1, len(events))

        fitness_cache: dict[Chromosome, Fitness] = {}
        evaluated_count = 0
        first_feasible_seconds: float | None = None

        def evaluate(chromosome: Chromosome) -> Fitness:
            nonlocal evaluated_count, first_feasible_seconds
            cached = fitness_cache.get(chromosome)
            if cached is not None:
                return cached
            assignments = _to_assignments(events, chromosome)
            validation = validate_schedule(problem, assignments)
            objective = score_schedule(problem, assignments)
            result = (validation.hard_violation_count, objective.weighted_total)
            fitness_cache[chromosome] = result
            evaluated_count += 1
            if result[0] == 0 and first_feasible_seconds is None:
                first_feasible_seconds = monotonic() - started_at
            return result

        population: list[_Individual] = []
        minimum_initial_size = max(2, config.tournament_size)
        while len(population) < config.population_size and (
            len(population) < minimum_initial_size or monotonic() < deadline
        ):
            chromosome = _randomized_greedy(events, locked_genes, rng)
            individual = _Individual(chromosome=chromosome, fitness=evaluate(chromosome))
            population.append(individual)
        population.sort(key=lambda individual: individual.fitness)
        best = population[0]
        generation = 0
        crossover_blocks = _crossover_blocks(events)

        while monotonic() < deadline and (
            config.max_generations is None or generation < config.max_generations
        ):
            elite_count = max(1, int(config.population_size * config.elite_fraction))
            next_population = population[:elite_count]
            while len(next_population) < config.population_size and monotonic() < deadline:
                left = _tournament(population, config.tournament_size, rng)
                right = _tournament(population, config.tournament_size, rng)
                if rng.random() < config.crossover_rate:
                    chromosome = _uniform_block_crossover(
                        left.chromosome, right.chromosome, crossover_blocks, rng
                    )
                else:
                    chromosome = left.chromosome
                chromosome = _mutate(
                    chromosome,
                    events,
                    locked_genes,
                    mutation_rate,
                    rng,
                )
                chromosome = _repair(
                    problem,
                    events,
                    chromosome,
                    locked_genes,
                    config.repair_attempts,
                    evaluate,
                    rng,
                    deadline,
                )
                next_population.append(
                    _Individual(chromosome=chromosome, fitness=evaluate(chromosome))
                )
            while len(next_population) < config.population_size:
                next_population.append(population[len(next_population) % len(population)])
            population = sorted(next_population, key=lambda individual: individual.fitness)
            generation += 1
            if population[0].fitness < best.fitness:
                best = population[0]

        runtime = monotonic() - started_at
        assignments = _to_assignments(events, best.chromosome)
        validation = validate_schedule(problem, assignments)
        objective = score_schedule(problem, assignments)
        if validation.feasible:
            status = SolverStatus.FEASIBLE
            stopping_reason = (
                "Configured generation limit reached with a feasible incumbent."
                if config.max_generations is not None and generation >= config.max_generations
                else "Time limit reached with a feasible incumbent."
            )
        else:
            status = SolverStatus.NO_SOLUTION
            stopping_reason = (
                "No feasible solution found before the configured generation limit."
                if config.max_generations is not None and generation >= config.max_generations
                else "No feasible solution found within the time limit."
            )

        return SolverResult(
            algorithm=self.algorithm,
            status=status,
            assignments=assignments,
            validation=validation,
            objective=objective,
            runtime_seconds=runtime,
            first_feasible_seconds=first_feasible_seconds,
            stopping_reason=stopping_reason,
            seed=config.seed,
            problem_hash=problem.canonical_hash,
            config_hash=config.canonical_hash,
            metrics=(
                ("evaluated_chromosomes", evaluated_count),
                ("final_hard_violations", best.fitness[0]),
                ("final_soft_penalty", best.fitness[1]),
                ("generations", generation),
                ("initial_population_size", len(population)),
                ("mutation_rate", mutation_rate),
                ("population_size", config.population_size),
                ("worker_count", 1),
            ),
        )


# Concise alias used by service code and command-line experiments.
GASolver = GeneticAlgorithmSolver


def _candidate_index(event: MeetingEvent, candidate_id: str) -> int:
    for index, candidate in enumerate(event.candidates):
        if candidate.candidate_id == candidate_id:
            return index
    raise ValueError(f"candidate {candidate_id!r} does not belong to event {event.event_id!r}")


def _to_assignments(events: tuple[MeetingEvent, ...], chromosome: Chromosome) -> tuple[Assignment, ...]:
    return tuple(
        Assignment(event_id=event.event_id, candidate_id=event.candidates[gene].candidate_id)
        for event, gene in zip(events, chromosome, strict=True)
    )


def _randomized_greedy(
    events: tuple[MeetingEvent, ...],
    locked_genes: dict[int, int],
    rng: Random,
) -> Chromosome:
    chromosome = [-1] * len(events)
    order = list(range(len(events)))
    rng.shuffle(order)
    order.sort(key=lambda index: len(events[index].candidates))
    room_occupancy: dict[tuple[str, str], set[str]] = {}
    instructor_occupancy: dict[tuple[str, str], set[str]] = {}
    section_occupancy: dict[tuple[str, str], set[str]] = {}
    distinct_days: dict[tuple[str, str], set[str]] = {}

    for event_index in order:
        event = events[event_index]
        if event_index in locked_genes:
            selected_index = locked_genes[event_index]
        else:
            candidate_indexes = list(range(len(event.candidates)))
            rng.shuffle(candidate_indexes)
            ranked = [
                (
                    (
                        _incremental_conflict_count(
                            event,
                            event.candidates[candidate_index],
                            room_occupancy,
                            instructor_occupancy,
                            section_occupancy,
                            distinct_days,
                        ),
                        event.candidates[candidate_index].preference_penalty,
                    ),
                    candidate_index,
                )
                for candidate_index in candidate_indexes
            ]
            best_fitness = min(item[0] for item in ranked)
            best_candidates = [index for fitness, index in ranked if fitness == best_fitness]
            selected_index = rng.choice(best_candidates)
        chromosome[event_index] = selected_index
        _occupy(
            event,
            event.candidates[selected_index],
            room_occupancy,
            instructor_occupancy,
            section_occupancy,
            distinct_days,
        )
    return tuple(chromosome)


def _incremental_conflict_count(
    event: MeetingEvent,
    candidate: CandidatePlacement,
    room_occupancy: dict[tuple[str, str], set[str]],
    instructor_occupancy: dict[tuple[str, str], set[str]],
    section_occupancy: dict[tuple[str, str], set[str]],
    distinct_days: dict[tuple[str, str], set[str]],
) -> int:
    conflicts: set[tuple[str, str, str]] = set()
    for atom_id in candidate.occupied_atom_ids:
        for other_event_id in room_occupancy.get((candidate.room_id, atom_id), ()):
            conflicts.add(("room", candidate.room_id, other_event_id))
        for instructor_id in event.instructor_ids:
            for other_event_id in instructor_occupancy.get((instructor_id, atom_id), ()):
                conflicts.add(("instructor", instructor_id, other_event_id))
        for section_id in event.section_ids:
            for other_event_id in section_occupancy.get((section_id, atom_id), ()):
                conflicts.add(("section", section_id, other_event_id))
    if event.distinct_day_group:
        for other_event_id in distinct_days.get(
            (event.distinct_day_group, candidate.day_id), ()
        ):
            conflicts.add(("distinct_day", event.distinct_day_group, other_event_id))
    return len(conflicts)


def _occupy(
    event: MeetingEvent,
    candidate: CandidatePlacement,
    room_occupancy: dict[tuple[str, str], set[str]],
    instructor_occupancy: dict[tuple[str, str], set[str]],
    section_occupancy: dict[tuple[str, str], set[str]],
    distinct_days: dict[tuple[str, str], set[str]],
) -> None:
    for atom_id in candidate.occupied_atom_ids:
        room_occupancy.setdefault((candidate.room_id, atom_id), set()).add(event.event_id)
        for instructor_id in event.instructor_ids:
            instructor_occupancy.setdefault((instructor_id, atom_id), set()).add(event.event_id)
        for section_id in event.section_ids:
            section_occupancy.setdefault((section_id, atom_id), set()).add(event.event_id)
    if event.distinct_day_group:
        distinct_days.setdefault((event.distinct_day_group, candidate.day_id), set()).add(
            event.event_id
        )


def _tournament(population: list[_Individual], size: int, rng: Random) -> _Individual:
    contestants = rng.sample(population, size)
    return min(contestants, key=lambda individual: individual.fitness)


def _crossover_blocks(events: tuple[MeetingEvent, ...]) -> tuple[tuple[int, ...], ...]:
    grouped: dict[tuple[str, ...], list[int]] = {}
    for index, event in enumerate(events):
        key = tuple(sorted(event.section_ids)) or (f"__event__{event.event_id}",)
        grouped.setdefault(key, []).append(index)
    return tuple(tuple(indexes) for _, indexes in sorted(grouped.items()))


def _uniform_block_crossover(
    left: Chromosome,
    right: Chromosome,
    blocks: tuple[tuple[int, ...], ...],
    rng: Random,
) -> Chromosome:
    child = list(left)
    for block in blocks:
        source = left if rng.random() < 0.5 else right
        for index in block:
            child[index] = source[index]
    return tuple(child)


def _mutate(
    chromosome: Chromosome,
    events: tuple[MeetingEvent, ...],
    locked_genes: dict[int, int],
    mutation_rate: float,
    rng: Random,
) -> Chromosome:
    mutated = list(chromosome)
    for index, event in enumerate(events):
        if index in locked_genes:
            mutated[index] = locked_genes[index]
        elif rng.random() < mutation_rate and len(event.candidates) > 1:
            alternatives = [gene for gene in range(len(event.candidates)) if gene != mutated[index]]
            mutated[index] = rng.choice(alternatives)
    return tuple(mutated)


def _repair(
    problem: ProblemInstance,
    events: tuple[MeetingEvent, ...],
    chromosome: Chromosome,
    locked_genes: dict[int, int],
    attempts: int,
    evaluate: Callable[[Chromosome], Fitness],
    rng: Random,
    deadline: float,
) -> Chromosome:
    current = chromosome
    current_fitness = evaluate(current)
    event_indexes = {event.event_id: index for index, event in enumerate(events)}
    for _ in range(attempts):
        if current_fitness[0] == 0 or monotonic() >= deadline:
            break
        report = validate_schedule(problem, _to_assignments(events, current))
        involved = {
            event_indexes[event_id]
            for violation in report.violations
            for event_id in violation.event_ids
            if event_id in event_indexes and event_indexes[event_id] not in locked_genes
        }
        if not involved:
            break
        ordered = sorted(involved, key=lambda index: (len(events[index].candidates), index))
        improved = False
        for index in ordered:
            candidate_indexes = list(range(len(events[index].candidates)))
            rng.shuffle(candidate_indexes)
            best_chromosome = current
            best_fitness = current_fitness
            for candidate_index in candidate_indexes:
                if monotonic() >= deadline:
                    return current
                if candidate_index == current[index]:
                    continue
                trial = list(current)
                trial[index] = candidate_index
                trial_chromosome = tuple(trial)
                trial_fitness = evaluate(trial_chromosome)
                if trial_fitness < best_fitness:
                    best_chromosome = trial_chromosome
                    best_fitness = trial_fitness
            if best_fitness < current_fitness:
                current = best_chromosome
                current_fitness = best_fitness
                improved = True
                break
        if not improved:
            break
    return current
