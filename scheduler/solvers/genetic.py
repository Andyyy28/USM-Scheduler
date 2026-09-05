"""Seeded custom Genetic Algorithm for university timetabling."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from math import prod
from random import Random
from time import monotonic, perf_counter

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
from scheduler.domain.prepared import PreparedProblem
from scheduler.domain.scoring import score_schedule
from scheduler.domain.validation import validate_schedule
from scheduler.solvers.neighborhood import improve_feasible
from scheduler.solvers.neighborhood import repair as bounded_repair
from scheduler.solvers.tracing import IncumbentTrace

Chromosome = tuple[int, ...]
Fitness = tuple[int, int]
GA_IMPLEMENTATION_VERSION = "ga-v7"
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
    conflict_pairs: tuple[tuple[int, int], ...] = ()


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
        phase_start = perf_counter() if config.diagnostic_trace else 0.0
        timings = {name: 0.0 for name in (
            "initialization_seconds", "preparation_seconds", "validation_seconds",
            "scoring_seconds", "repair_seconds", "feasible_improvement_seconds",
        )}
        preparation_expired = False
        try:
            prepared = PreparedProblem(problem, deadline=deadline, clock=monotonic)
        except TimeoutError:
            prepared = None
            preparation_expired = True
        if config.diagnostic_trace:
            timings["preparation_seconds"] = perf_counter() - phase_start
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
        search_diagnostics = {
            "mutation_operations": 0,
            "mutated_offspring": 0,
            "repair_calls": 0,
            "repair_needed": 0,
            "repair_iterations": 0,
            "repair_candidate_evaluations": 0,
            "repair_improvements": 0,
            "repair_successes": 0,
            "repair_failures": 0,
            "repair_deadline_skips": 0,
            "repair_second_move_evaluations": 0,
            "repair_second_move_improvements": 0,
            "incumbent_rechecks": 0,
            "completed_offspring": 0,
            "completed_generations": 0,
            "repair_total_evaluation_requests": 0,
            "repair_single_move_evaluations": 0,
            "repair_max_evaluation_requests": 0,
            "repair_budget_exhaustions": 0,
            "feasible_improvement_calls": 0,
            "feasible_improvement_evaluations": 0,
            "feasible_improvement_max_requests": 0,
            "feasible_improvements": 0,
        }
        first_feasible_seconds: float | None = None
        timely_best: _Individual | None = None
        trace = IncumbentTrace(config.diagnostic_trace)
        daily_limits = (
            {
                evidence.instructor_id: evidence.max_daily_teaching_atoms
                for evidence in problem.instructor_evidence
                if evidence.max_daily_teaching_atoms is not None
            }
            if problem.supports_thesis_v2_rules
            else {}
        )

        def construct() -> Chromosome | None:
            if preparation_expired:
                return None
            return _randomized_greedy(
                events,
                locked_genes,
                rng,
                daily_limits=daily_limits,
                preference_weight=problem.objective_profile.preference_weight,
                deadline=deadline,
            )

        def evaluate(chromosome: Chromosome) -> _Evaluation:
            nonlocal evaluated_count, cache_hits, cache_misses, cache_evictions
            nonlocal first_feasible_seconds, timely_best
            cached = fitness_cache.get(chromosome)
            if cached is not None:
                cache_hits += 1
                fitness_cache.move_to_end(chromosome)
                return cached
            cache_misses += 1
            assignments = _to_assignments(events, chromosome)
            phase_at = perf_counter() if config.diagnostic_trace else 0.0
            validation = validate_schedule(problem, assignments, prepared=prepared)
            if config.diagnostic_trace:
                timings["validation_seconds"] += perf_counter() - phase_at
                phase_at = perf_counter()
            objective = score_schedule(problem, assignments, prepared=prepared)
            if config.diagnostic_trace:
                timings["scoring_seconds"] += perf_counter() - phase_at
            involved = {
                event_indexes[event_id]
                for violation in validation.violations
                for event_id in violation.event_ids
                if event_id in event_indexes and event_indexes[event_id] not in locked_genes
            }
            result = _Evaluation(
                fitness=(validation.hard_violation_count, objective.weighted_total),
                conflict_event_indexes=tuple(sorted(involved)),
                conflict_pairs=tuple(sorted({
                    tuple(sorted((event_indexes[left], event_indexes[right])))
                    for violation in validation.violations
                    for left, right in combinations(violation.event_ids, 2)
                    if left in event_indexes and right in event_indexes
                })),
            )
            fitness_cache[chromosome] = result
            if len(fitness_cache) > cache_capacity:
                fitness_cache.popitem(last=False)
                cache_evictions += 1
            evaluated_count += 1
            completed_at = monotonic()
            if completed_at <= deadline and (timely_best is None or result.fitness < timely_best.fitness):
                # The prepared path is an optimization, never the authority for
                # a returned incumbent. Recheck without indexes within budget.
                search_diagnostics["incumbent_rechecks"] += 1
                phase_at = perf_counter() if config.diagnostic_trace else 0.0
                checked_validation = validate_schedule(problem, assignments)
                if config.diagnostic_trace:
                    timings["validation_seconds"] += perf_counter() - phase_at
                    phase_at = perf_counter()
                checked_objective = score_schedule(problem, assignments)
                if config.diagnostic_trace:
                    timings["scoring_seconds"] += perf_counter() - phase_at
                if checked_validation != validation or checked_objective != objective:
                    raise ValueError("prepared evaluation disagrees with independent incumbent check")
                completed_at = monotonic()
                if completed_at <= deadline:
                    timely_best = _Individual(chromosome, result.fitness)
                    if result.fitness[0] == 0 and first_feasible_seconds is None:
                        first_feasible_seconds = completed_at - started_at
                    trace.observe(completed_at - started_at, result.fitness)
            return result

        # Initialization is part of the budget. A completed evaluation can only
        # become the returned incumbent if it finishes within that same budget.
        first_chromosome = construct()
        population = []
        if first_chromosome is not None and monotonic() < deadline:
            first_evaluation = evaluate(first_chromosome)
            population.append(_Individual(first_chromosome, first_evaluation.fitness))
        population_chromosomes = {individual.chromosome for individual in population}
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
            and not (config.first_feasible_only and timely_best is not None and timely_best.fitness[0] == 0)
            and initial_attempts < initial_attempt_limit
            and not search_space_exhausted
            and monotonic() < deadline
        ):
            chromosome = construct()
            initial_attempts += 1
            if chromosome is None:
                break
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
        if config.diagnostic_trace:
            timings["initialization_seconds"] = perf_counter() - phase_start
        generation = 0
        generations_without_new_evaluation = 0
        maximum_stagnation = 0
        crossover_blocks = _crossover_blocks(events)

        while (
            monotonic() < deadline
            and not (config.first_feasible_only and timely_best is not None and timely_best.fitness[0] == 0)
            and population
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
                and not (config.first_feasible_only and timely_best is not None and timely_best.fitness[0] == 0)
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
                before_mutation = chromosome
                chromosome = _mutate(
                    chromosome,
                    events,
                    locked_genes,
                    mutation_rate,
                    rng,
                )
                changed_genes = sum(
                    before != after
                    for index, (before, after) in enumerate(zip(before_mutation, chromosome, strict=True))
                    if index not in locked_genes
                )
                search_diagnostics["mutation_operations"] += changed_genes
                search_diagnostics["mutated_offspring"] += int(changed_genes > 0)
                if monotonic() >= deadline:
                    break
                phase_at = perf_counter() if config.diagnostic_trace else 0.0
                chromosome = _repair(
                    events,
                    chromosome,
                    locked_genes,
                    config.repair_attempts,
                    evaluate,
                    rng,
                    deadline,
                    search_diagnostics,
                    problem.objective_profile.preference_weight,
                )
                if config.diagnostic_trace:
                    timings["repair_seconds"] += perf_counter() - phase_at
                if monotonic() >= deadline:
                    break
                if chromosome in next_chromosomes:
                    duplicates_suppressed += 1
                    continue
                evaluation = evaluate(chromosome)
                if monotonic() >= deadline:
                    break
                next_population.append(_Individual(chromosome, evaluation.fitness))
                search_diagnostics["completed_offspring"] += 1
                next_chromosomes.add(chromosome)
                search_space_exhausted = (
                    small_search_space and evaluated_count >= search_space_size
                )
                if search_space_exhausted:
                    break
            generation_completed = len(next_population) >= target_size and monotonic() < deadline
            if generation_completed:
                search_diagnostics["completed_generations"] += 1
                if not config.first_feasible_only and timely_best is not None and timely_best.fitness[0] == 0:
                    phase_at = perf_counter() if config.diagnostic_trace else 0.0
                    improve_feasible(
                        events, timely_best.chromosome, timely_best.fitness,
                        locked_genes, evaluate, rng, deadline, search_diagnostics, monotonic,
                        problem.objective_profile.preference_weight,
                    )
                    if config.diagnostic_trace:
                        timings["feasible_improvement_seconds"] += perf_counter() - phase_at
                    # evaluate() already independently certifies the new global
                    # incumbent. Let the next generation inherit it as an elite.
                    if timely_best.chromosome not in next_chromosomes:
                        worst = max(range(len(next_population)), key=lambda index: next_population[index].fitness)
                        next_population[worst] = timely_best
                    search_space_exhausted = small_search_space and evaluated_count >= search_space_size
            if len(next_population) < target_size:
                # A saturated generation may have fewer unique chromosomes than the
                # requested population. Reusing incumbents keeps selection well-defined
                # without performing or claiming additional evaluations.
                source = population or next_population
                while len(next_population) < target_size:
                    next_population.append(source[len(next_population) % len(source)])
            population = sorted(next_population, key=lambda individual: individual.fitness)
            generation += 1
            if evaluated_count == evaluations_before_generation:
                generations_without_new_evaluation += 1
            else:
                generations_without_new_evaluation = 0
            maximum_stagnation = max(
                maximum_stagnation, generations_without_new_evaluation
            )

        runtime = monotonic() - started_at
        assignments = _to_assignments(events, timely_best.chromosome) if timely_best else ()
        validation = validate_schedule(problem, assignments)
        objective = score_schedule(problem, assignments) if timely_best else None
        if validation.feasible:
            status = SolverStatus.FEASIBLE
            if config.first_feasible_only:
                stopping_reason = "Stopped after finding and validating a complete timetable."
            elif search_space_exhausted:
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
                ("final_hard_violations", validation.hard_violation_count),
                ("final_soft_penalty", objective.weighted_total if objective else None),
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
            ) + tuple(search_diagnostics.items()) + trace.metrics()
            + (tuple(timings.items()) if config.diagnostic_trace else ()),
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
    *,
    daily_limits: dict[str, int] | None = None,
    preference_weight: int = 1,
    deadline: float = float("inf"),
) -> Chromosome | None:
    if monotonic() >= deadline:
        return None
    chromosome = [-1] * len(events)
    order = [index for index in range(len(events)) if index not in locked_genes]
    rng.shuffle(order)
    order.sort(key=lambda index: len(events[index].candidates))
    room_occupancy: dict[tuple[str, str], set[str]] = {}
    instructor_occupancy: dict[tuple[str, str], set[str]] = {}
    section_occupancy: dict[tuple[str, str], set[str]] = {}
    distinct_days: dict[tuple[str, str], set[str]] = {}
    daily_loads: dict[tuple[str, str], int] = {}
    daily_limits = daily_limits or {}

    # Locks are immutable parts of the partial schedule. Seed them first so
    # every mutable event is ranked against their occupancy regardless of the
    # randomized construction order.
    for event_index, selected_index in sorted(locked_genes.items()):
        if monotonic() >= deadline:
            return None
        event = events[event_index]
        chromosome[event_index] = selected_index
        _occupy(
            event,
            event.candidates[selected_index],
            room_occupancy,
            instructor_occupancy,
            section_occupancy,
            distinct_days,
            daily_loads,
        )

    for event_index in order:
        if monotonic() >= deadline:
            return None
        event = events[event_index]
        candidate_indexes = list(range(len(event.candidates)))
        rng.shuffle(candidate_indexes)
        # Room alternatives at the same time share all non-room conflicts.
        # Keep this cache local to the current partial schedule: occupancy changes
        # after each event, so reusing it across placements would be incorrect.
        time_costs: dict[tuple[str, tuple[str, ...]], tuple[int, int]] = {}
        best_fitness: tuple[int, int, int] | None = None
        best_candidates: list[int] = []
        for position, candidate_index in enumerate(candidate_indexes):
            if position % 64 == 0 and monotonic() >= deadline:
                return None
            candidate = event.candidates[candidate_index]
            time_key = (candidate.day_id, candidate.occupied_atom_ids)
            time_cost = time_costs.get(time_key)
            if time_cost is None:
                conflicts = _incremental_conflict_count(
                    event, candidate, {}, instructor_occupancy, section_occupancy, distinct_days
                )
                added_excess = 0
                for instructor_id in event.instructor_ids:
                    limit = daily_limits.get(instructor_id)
                    if limit is None:
                        continue
                    before = daily_loads.get((instructor_id, candidate.day_id), 0)
                    after = before + len(candidate.occupied_atom_ids)
                    # The validator counts one violation per instructor/day,
                    # even if several meetings exceed that day's limit.
                    conflicts += int(before <= limit < after)
                    added_excess += max(0, after - limit) - max(0, before - limit)
                time_cost = (conflicts, added_excess)
                time_costs[time_key] = time_cost
            room_conflicts: set[str] = set()
            for atom_id in candidate.occupied_atom_ids:
                room_conflicts.update(room_occupancy.get((candidate.room_id, atom_id), ()))
            fitness = (
                time_cost[0] + len(room_conflicts),
                time_cost[1],
                candidate.preference_penalty * preference_weight,
            )
            if best_fitness is None or fitness < best_fitness:
                best_fitness = fitness
                best_candidates = [candidate_index]
            elif fitness == best_fitness:
                best_candidates.append(candidate_index)
            # Every term is nonnegative. The first zero in a uniformly shuffled
            # candidate list is uniform over all zero-cost placements, including
            # time groups with different numbers of rooms.
            if fitness == (0, 0, 0):
                break
        selected_index = rng.choice(best_candidates)
        chromosome[event_index] = selected_index
        _occupy(
            event,
            event.candidates[selected_index],
            room_occupancy,
            instructor_occupancy,
            section_occupancy,
            distinct_days,
            daily_loads,
        )
    if monotonic() >= deadline:
        return None
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
    daily_loads: dict[tuple[str, str], int] | None = None,
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
    if daily_loads is not None:
        for instructor_id in event.instructor_ids:
            key = (instructor_id, candidate.day_id)
            daily_loads[key] = daily_loads.get(key, 0) + len(candidate.occupied_atom_ids)


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
            # Uniform over every allele except the current one, without allocating
            # a domain-sized list for each mutated gene.
            alternative = rng.randrange(len(event.candidates) - 1)
            mutated[index] = alternative + int(alternative >= mutated[index])
    return tuple(mutated)


def _repair(
    events: tuple[MeetingEvent, ...], chromosome: Chromosome, locked_genes: dict[int, int],
    attempts: int, evaluate: Callable[[Chromosome], _Evaluation], rng: Random,
    deadline: float, diagnostics: dict[str, int] | None = None, preference_weight: int = 1,
) -> Chromosome:
    return bounded_repair(events, chromosome, locked_genes, attempts, evaluate, rng,
                          deadline, diagnostics if diagnostics is not None else {}, monotonic, preference_weight)


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
