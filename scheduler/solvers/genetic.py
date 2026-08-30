"""Seeded custom Genetic Algorithm for university timetabling."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from math import prod
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
GA_IMPLEMENTATION_VERSION = "ga-v2"
_CACHE_GENE_BUDGET = 5_000_000
_MIN_CACHE_ENTRIES = 100
_MAX_CACHE_ENTRIES = 100_000
_MAX_OFFSPRING_ATTEMPT_MULTIPLIER = 4
_STAGNANT_GENERATION_LIMIT = 10


@dataclass(frozen=True, slots=True)
class _Individual:
    chromosome: Chromosome
    fitness: Fitness


@dataclass(frozen=True, slots=True)
class _Evaluation:
    fitness: Fitness
    conflict_event_indexes: tuple[int, ...]


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
        mutable_event_count = len(events) - len(locked_genes)
        mutation_rate = config.mutation_rate
        if mutation_rate is None:
            mutation_rate = (
                min(1.0, 2.0 / mutable_event_count) if mutable_event_count else 0.0
            )

        cache_capacity = _cache_capacity(len(events))
        fitness_cache: OrderedDict[Chromosome, _Evaluation] = OrderedDict()
        evaluated_count = 0
        cache_hits = 0
        cache_misses = 0
        cache_evictions = 0
        duplicates_suppressed = 0
        first_feasible_seconds: float | None = None

        def evaluate(chromosome: Chromosome) -> _Evaluation:
            nonlocal evaluated_count, cache_hits, cache_misses, cache_evictions
            nonlocal first_feasible_seconds
            cached = fitness_cache.get(chromosome)
            if cached is not None:
                cache_hits += 1
                fitness_cache.move_to_end(chromosome)
                return cached
            cache_misses += 1
            assignments = _to_assignments(events, chromosome)
            validation = validate_schedule(problem, assignments)
            objective = score_schedule(problem, assignments)
            involved = {
                event_indexes[event_id]
                for violation in validation.violations
                for event_id in violation.event_ids
                if event_id in event_indexes and event_indexes[event_id] not in locked_genes
            }
            result = _Evaluation(
                fitness=(validation.hard_violation_count, objective.weighted_total),
                conflict_event_indexes=tuple(sorted(involved)),
            )
            fitness_cache[chromosome] = result
            if len(fitness_cache) > cache_capacity:
                fitness_cache.popitem(last=False)
                cache_evictions += 1
            evaluated_count += 1
            if result.fitness[0] == 0 and first_feasible_seconds is None:
                first_feasible_seconds = monotonic() - started_at
            return result

        # Exactly one incumbent is always constructed. After that first evaluation,
        # every expensive boundary observes the caller's wall-clock deadline.
        first_chromosome = _randomized_greedy(events, locked_genes, rng)
        first_evaluation = evaluate(first_chromosome)
        population = [
            _Individual(chromosome=first_chromosome, fitness=first_evaluation.fitness)
        ]
        population_chromosomes = {first_chromosome}
        initial_attempts = 1
        initial_attempt_limit = max(
            config.population_size,
            config.population_size * _MAX_OFFSPRING_ATTEMPT_MULTIPLIER,
        )
        search_space_size = _search_space_size(events, locked_genes)
        small_search_space = search_space_size <= cache_capacity
        search_space_exhausted = small_search_space and evaluated_count >= search_space_size
        while (
            len(population) < config.population_size
            and initial_attempts < initial_attempt_limit
            and not search_space_exhausted
            and monotonic() < deadline
        ):
            chromosome = _randomized_greedy(events, locked_genes, rng)
            initial_attempts += 1
            if chromosome in population_chromosomes:
                duplicates_suppressed += 1
                continue
            if monotonic() >= deadline:
                break
            evaluation = evaluate(chromosome)
            population.append(_Individual(chromosome, evaluation.fitness))
            population_chromosomes.add(chromosome)
            search_space_exhausted = (
                small_search_space and evaluated_count >= search_space_size
            )
        population.sort(key=lambda individual: individual.fitness)
        initial_population_size = len(population)
        best = population[0]
        generation = 0
        generations_without_new_evaluation = 0
        maximum_stagnation = 0
        crossover_blocks = _crossover_blocks(events)

        while (
            monotonic() < deadline
            and not search_space_exhausted
            and generations_without_new_evaluation < _STAGNANT_GENERATION_LIMIT
            and (config.max_generations is None or generation < config.max_generations)
        ):
            evaluations_before_generation = evaluated_count
            target_size = config.population_size
            elite_count = min(
                len(population),
                max(1, int(target_size * config.elite_fraction)),
            )
            next_population = population[:elite_count]
            next_chromosomes = {individual.chromosome for individual in next_population}
            offspring_attempts = 0
            offspring_attempt_limit = target_size * _MAX_OFFSPRING_ATTEMPT_MULTIPLIER
            while (
                len(next_population) < target_size
                and offspring_attempts < offspring_attempt_limit
                and monotonic() < deadline
            ):
                offspring_attempts += 1
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
                if monotonic() >= deadline:
                    break
                chromosome = _repair(
                    events,
                    chromosome,
                    locked_genes,
                    config.repair_attempts,
                    evaluate,
                    rng,
                    deadline,
                )
                if monotonic() >= deadline:
                    break
                if chromosome in next_chromosomes:
                    duplicates_suppressed += 1
                    continue
                evaluation = evaluate(chromosome)
                next_population.append(_Individual(chromosome, evaluation.fitness))
                next_chromosomes.add(chromosome)
                search_space_exhausted = (
                    small_search_space and evaluated_count >= search_space_size
                )
                if search_space_exhausted:
                    break
            if len(next_population) < target_size:
                # A saturated generation may have fewer unique chromosomes than the
                # requested population. Reusing incumbents keeps selection well-defined
                # without performing or claiming additional evaluations.
                source = population or next_population
                while len(next_population) < target_size:
                    next_population.append(source[len(next_population) % len(source)])
            population = sorted(next_population, key=lambda individual: individual.fitness)
            generation += 1
            if population[0].fitness < best.fitness:
                best = population[0]
            if evaluated_count == evaluations_before_generation:
                generations_without_new_evaluation += 1
            else:
                generations_without_new_evaluation = 0
            maximum_stagnation = max(
                maximum_stagnation, generations_without_new_evaluation
            )

        runtime = monotonic() - started_at
        assignments = _to_assignments(events, best.chromosome)
        validation = validate_schedule(problem, assignments)
        objective = score_schedule(problem, assignments)
        if validation.feasible:
            status = SolverStatus.FEASIBLE
            if search_space_exhausted:
                stopping_reason = (
                    "The small search space was exhaustively evaluated; returning a "
                    "feasible incumbent without an optimality claim."
                )
            elif generations_without_new_evaluation >= _STAGNANT_GENERATION_LIMIT:
                stopping_reason = "Search stagnated with a feasible incumbent."
            elif config.max_generations is not None and generation >= config.max_generations:
                stopping_reason = "Configured generation limit reached with a feasible incumbent."
            else:
                stopping_reason = "Time limit reached with a feasible incumbent."
        else:
            status = SolverStatus.NO_SOLUTION
            if search_space_exhausted:
                stopping_reason = (
                    "No feasible solution was found after exhausting the search space; "
                    "the Genetic Algorithm does not claim infeasibility."
                )
            elif generations_without_new_evaluation >= _STAGNANT_GENERATION_LIMIT:
                stopping_reason = (
                    "No feasible solution was found before search stagnated; "
                    "no infeasibility is claimed."
                )
            elif config.max_generations is not None and generation >= config.max_generations:
                stopping_reason = "No feasible solution found before the generation limit."
            else:
                stopping_reason = "No feasible solution found within the time limit."

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
                ("implementation_version", GA_IMPLEMENTATION_VERSION),
                ("evaluated_chromosomes", evaluated_count),
                ("final_hard_violations", best.fitness[0]),
                ("final_soft_penalty", best.fitness[1]),
                ("generations", generation),
                ("initial_population_size", initial_population_size),
                ("mutable_event_count", mutable_event_count),
                ("mutation_rate", mutation_rate),
                ("population_size", config.population_size),
                ("cache_capacity", cache_capacity),
                ("cache_hits", cache_hits),
                ("cache_misses", cache_misses),
                ("cache_evictions", cache_evictions),
                ("duplicates_suppressed", duplicates_suppressed),
                ("stagnation_generations", maximum_stagnation),
                ("search_space_size", search_space_size),
                ("search_space_exhausted", search_space_exhausted),
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
    order = [index for index in range(len(events)) if index not in locked_genes]
    rng.shuffle(order)
    order.sort(key=lambda index: len(events[index].candidates))
    room_occupancy: dict[tuple[str, str], set[str]] = {}
    instructor_occupancy: dict[tuple[str, str], set[str]] = {}
    section_occupancy: dict[tuple[str, str], set[str]] = {}
    distinct_days: dict[tuple[str, str], set[str]] = {}

    # Locks are immutable parts of the partial schedule. Seed them first so
    # every mutable event is ranked against their occupancy regardless of the
    # randomized construction order.
    for event_index, selected_index in sorted(locked_genes.items()):
        event = events[event_index]
        chromosome[event_index] = selected_index
        _occupy(
            event,
            event.candidates[selected_index],
            room_occupancy,
            instructor_occupancy,
            section_occupancy,
            distinct_days,
        )

    for event_index in order:
        event = events[event_index]
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
    contestants = rng.sample(population, min(size, len(population)))
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
    events: tuple[MeetingEvent, ...],
    chromosome: Chromosome,
    locked_genes: dict[int, int],
    attempts: int,
    evaluate: Callable[[Chromosome], _Evaluation],
    rng: Random,
    deadline: float,
) -> Chromosome:
    current = chromosome
    if monotonic() >= deadline:
        return current
    current_evaluation = evaluate(current)
    current_fitness = current_evaluation.fitness
    for _ in range(attempts):
        if current_fitness[0] == 0 or monotonic() >= deadline:
            break
        involved = set(current_evaluation.conflict_event_indexes)
        if not involved:
            break
        ordered = sorted(involved, key=lambda index: (len(events[index].candidates), index))
        improved = False
        for index in ordered:
            candidate_indexes = list(range(len(events[index].candidates)))
            rng.shuffle(candidate_indexes)
            best_chromosome = current
            best_fitness = current_fitness
            best_evaluation = current_evaluation
            for candidate_index in candidate_indexes:
                if monotonic() >= deadline:
                    return current
                if candidate_index == current[index]:
                    continue
                trial = list(current)
                trial[index] = candidate_index
                trial_chromosome = tuple(trial)
                trial_evaluation = evaluate(trial_chromosome)
                trial_fitness = trial_evaluation.fitness
                if trial_fitness < best_fitness:
                    best_chromosome = trial_chromosome
                    best_fitness = trial_fitness
                    best_evaluation = trial_evaluation
            if best_fitness < current_fitness:
                current = best_chromosome
                current_fitness = best_fitness
                current_evaluation = best_evaluation
                improved = True
                break
        if not improved:
            break
    return current


def _cache_capacity(gene_count: int) -> int:
    approximate = _CACHE_GENE_BUDGET // max(1, gene_count)
    return min(_MAX_CACHE_ENTRIES, max(_MIN_CACHE_ENTRIES, approximate))


def _search_space_size(
    events: tuple[MeetingEvent, ...], locked_genes: dict[int, int]
) -> int:
    return prod(
        len(event.candidates)
        for index, event in enumerate(events)
        if index not in locked_genes
    )
