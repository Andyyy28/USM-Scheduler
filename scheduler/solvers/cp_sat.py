"""Constraint Programming-SAT scheduling engine."""

from __future__ import annotations

from collections import defaultdict
from time import monotonic
from typing import Any

from scheduler.domain.contracts import (
    Assignment,
    ProblemInstance,
    SolverAlgorithm,
    SolverConfig,
    SolverResult,
    SolverStatus,
)
from scheduler.domain.scoring import score_schedule
from scheduler.domain.validation import validate_schedule

try:  # Keep all other domain/GA functionality usable without this optional wheel.
    from ortools.sat.python import cp_model
except ImportError as exc:  # pragma: no cover - environment dependent
    cp_model = None  # type: ignore[assignment]
    _ORTOOLS_IMPORT_ERROR: ImportError | None = exc
else:
    _ORTOOLS_IMPORT_ERROR = None


class ORToolsUnavailableError(RuntimeError):
    """Raised only when CP-SAT is requested without OR-Tools installed."""


if cp_model is not None:

    class _FirstFeasibleCallback(cp_model.CpSolverSolutionCallback):
        def __init__(
            self,
            started_at: float,
            problem: ProblemInstance,
            event_order: tuple[Any, ...],
            variables: dict[tuple[str, str], Any],
        ) -> None:
            super().__init__()
            self._started_at = started_at
            self._problem = problem
            self._event_order = event_order
            self._variables = variables
            self.first_feasible_seconds: float | None = None

        def on_solution_callback(self) -> None:
            if self.first_feasible_seconds is not None:
                return
            assignments = tuple(
                Assignment(event_id=event.event_id, candidate_id=candidate.candidate_id)
                for event in self._event_order
                for candidate in event.candidates
                if self.BooleanValue(self._variables[(event.event_id, candidate.candidate_id)])
            )
            if validate_schedule(self._problem, assignments).feasible:
                self.first_feasible_seconds = monotonic() - self._started_at


class CpSatSolver:
    """Exact CP-SAT model over the shared event/candidate representation."""

    algorithm = SolverAlgorithm.CP_SAT

    def solve(self, problem: ProblemInstance, config: SolverConfig) -> SolverResult:
        if config.algorithm is not self.algorithm:
            raise ValueError(f"CpSatSolver requires algorithm={self.algorithm.value}")
        if cp_model is None:
            raise ORToolsUnavailableError(
                "CP-SAT requires the optional 'ortools' package; install the project's "
                "requirements.txt before requesting this solver."
            ) from _ORTOOLS_IMPORT_ERROR

        started_at = monotonic()
        deadline = started_at + config.time_limit_seconds
        model = cp_model.CpModel()
        event_order = tuple(sorted(problem.events, key=lambda event: event.event_id))
        variables: dict[tuple[str, str], Any] = {}

        for event in event_order:
            event_variables = []
            for candidate in sorted(event.candidates, key=lambda item: item.candidate_id):
                variable = model.NewBoolVar(f"x__{event.event_id}__{candidate.candidate_id}")
                variables[(event.event_id, candidate.candidate_id)] = variable
                event_variables.append(variable)
            model.AddExactlyOne(event_variables)

        resource_buckets = _resource_atom_buckets(problem, variables)
        for bucket_variables in resource_buckets.values():
            if len(bucket_variables) > 1:
                model.AddAtMostOne(bucket_variables)

        distinct_day_buckets: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for event in event_order:
            if event.distinct_day_group is None:
                continue
            for candidate in event.candidates:
                distinct_day_buckets[(event.distinct_day_group, candidate.day_id)].append(
                    variables[(event.event_id, candidate.candidate_id)]
                )
        for bucket_variables in distinct_day_buckets.values():
            if len(bucket_variables) > 1:
                model.AddAtMostOne(bucket_variables)

        for event_id, candidate_id in problem.lock_map.items():
            model.Add(variables[(event_id, candidate_id)] == 1)

        preference_expr = sum(
            candidate.preference_penalty * variables[(event.event_id, candidate.candidate_id)]
            for event in event_order
            for candidate in event.candidates
        )
        section_gap_expr = (
            _gap_expression(model, problem, variables, resource_kind="section")
            if problem.objective_profile.section_gap_weight
            else 0
        )
        instructor_gap_expr = (
            _gap_expression(model, problem, variables, resource_kind="instructor")
            if problem.objective_profile.instructor_gap_weight
            else 0
        )
        load_imbalance_expr = (
            _load_imbalance_expression(model, problem, variables)
            if problem.objective_profile.load_imbalance_weight
            else 0
        )

        profile = problem.objective_profile
        objective_expr = (
            profile.preference_weight * preference_expr
            + profile.section_gap_weight * section_gap_expr
            + profile.instructor_gap_weight * instructor_gap_expr
            + profile.load_imbalance_weight * load_imbalance_expr
        )
        model.Minimize(objective_expr)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(1e-6, deadline - monotonic())
        solver.parameters.random_seed = config.seed
        solver.parameters.num_search_workers = config.worker_count
        callback = _FirstFeasibleCallback(started_at, problem, event_order, variables)
        cp_status = solver.Solve(model, callback)
        runtime = monotonic() - started_at
        status = _map_status(cp_status)

        assignments: tuple[Assignment, ...] = ()
        if status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
            assignments = tuple(
                Assignment(event_id=event.event_id, candidate_id=candidate.candidate_id)
                for event in event_order
                for candidate in event.candidates
                if solver.Value(variables[(event.event_id, candidate.candidate_id)]) == 1
            )
        validation = validate_schedule(problem, assignments)
        objective = score_schedule(problem, assignments) if validation.feasible else None

        objective_value: float | None = None
        best_bound: float | None = None
        relative_gap: float | None = None
        if status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
            objective_value = solver.ObjectiveValue()
            best_bound = solver.BestObjectiveBound()
            relative_gap = max(0.0, objective_value - best_bound) / max(1.0, abs(objective_value))
            if not validation.feasible:
                status = SolverStatus.ERROR
                stopping_reason = "CP-SAT returned an assignment rejected by the independent validator."
            elif objective is None or round(objective_value) != objective.weighted_total:
                status = SolverStatus.ERROR
                stopping_reason = "CP-SAT objective disagrees with independent rescoring."
            else:
                stopping_reason = (
                    "Optimal solution proven."
                    if status is SolverStatus.OPTIMAL
                    else "Time/search limit reached with a feasible incumbent."
                )
        elif status is SolverStatus.INFEASIBLE:
            stopping_reason = "CP-SAT proved the problem infeasible."
            best_bound = solver.BestObjectiveBound()
        elif status is SolverStatus.ERROR:
            stopping_reason = "CP-SAT rejected the generated model as invalid."
        else:
            stopping_reason = (
                "Configured wall-clock time limit reached without a feasible incumbent or "
                "infeasibility proof."
            )

        metrics = (
            ("best_objective_bound", best_bound),
            ("branches", solver.NumBranches()),
            ("conflicts", solver.NumConflicts()),
            ("objective_value", objective_value),
            ("relative_gap", relative_gap),
            ("solver_wall_time_seconds", solver.WallTime()),
            ("worker_count", config.worker_count),
        )
        return SolverResult(
            algorithm=self.algorithm,
            status=status,
            assignments=assignments,
            validation=validation,
            objective=objective,
            runtime_seconds=runtime,
            first_feasible_seconds=callback.first_feasible_seconds,
            stopping_reason=stopping_reason,
            seed=config.seed,
            problem_hash=problem.canonical_hash,
            config_hash=config.canonical_hash,
            metrics=metrics,
        )


# Compatibility spelling for integrations that prefer the acronym in capitals.
CPSATSolver = CpSatSolver


def is_ortools_available() -> bool:
    return cp_model is not None


def _resource_atom_buckets(
    problem: ProblemInstance, variables: dict[tuple[str, str], Any]
) -> dict[tuple[str, str, str], list[Any]]:
    buckets: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for event in problem.events:
        for candidate in event.candidates:
            variable = variables[(event.event_id, candidate.candidate_id)]
            for atom_id in candidate.occupied_atom_ids:
                buckets[("room", candidate.room_id, atom_id)].append(variable)
                for instructor_id in event.instructor_ids:
                    buckets[("instructor", instructor_id, atom_id)].append(variable)
                for section_id in event.section_ids:
                    buckets[("section", section_id, atom_id)].append(variable)
    return buckets


def _gap_expression(
    model: Any,
    problem: ProblemInstance,
    variables: dict[tuple[str, str], Any],
    *,
    resource_kind: str,
) -> Any:
    if resource_kind == "section":
        event_resources = {event.event_id: set(event.section_ids) for event in problem.events}
    elif resource_kind == "instructor":
        event_resources = {event.event_id: set(event.instructor_ids) for event in problem.events}
    else:  # pragma: no cover - private programming error
        raise ValueError(f"unsupported resource kind {resource_kind!r}")

    atoms_by_day: dict[str, list[str]] = defaultdict(list)
    for atom in sorted(problem.time_atoms, key=lambda item: (item.day_index, item.order, item.atom_id)):
        atoms_by_day[atom.day_id].append(atom.atom_id)

    choices_by_resource_atom: dict[tuple[str, str], list[Any]] = defaultdict(list)
    resource_ids: set[str] = set()
    for event in problem.events:
        resources = event_resources[event.event_id]
        resource_ids.update(resources)
        for candidate in event.candidates:
            variable = variables[(event.event_id, candidate.candidate_id)]
            for resource_id in resources:
                for atom_id in candidate.occupied_atom_ids:
                    choices_by_resource_atom[(resource_id, atom_id)].append(variable)

    gap_variables = []
    for resource_id in sorted(resource_ids):
        for day_id in problem.day_ids:
            occupancy = []
            for atom_id in atoms_by_day[day_id]:
                choices = choices_by_resource_atom[(resource_id, atom_id)]
                occupied = model.NewBoolVar(
                    f"occupied__{resource_kind}__{resource_id}__{atom_id}"
                )
                model.Add(occupied == sum(choices))
                occupancy.append(occupied)

            for position, occupied in enumerate(occupancy):
                before = model.NewBoolVar(
                    f"before__{resource_kind}__{resource_id}__{day_id}__{position}"
                )
                after = model.NewBoolVar(
                    f"after__{resource_kind}__{resource_id}__{day_id}__{position}"
                )
                if position == 0:
                    model.Add(before == 0)
                else:
                    model.AddMaxEquality(before, occupancy[:position])
                if position == len(occupancy) - 1:
                    model.Add(after == 0)
                else:
                    model.AddMaxEquality(after, occupancy[position + 1 :])

                gap = model.NewBoolVar(
                    f"gap__{resource_kind}__{resource_id}__{day_id}__{position}"
                )
                model.Add(gap <= before)
                model.Add(gap <= after)
                model.Add(gap + occupied <= 1)
                model.Add(gap >= before + after - occupied - 1)
                gap_variables.append(gap)
    return sum(gap_variables)


def _load_imbalance_expression(
    model: Any, problem: ProblemInstance, variables: dict[tuple[str, str], Any]
) -> Any:
    deviations = []
    day_ids = problem.day_ids
    day_count = len(day_ids)
    if day_count == 0:
        return 0

    for resource_kind in ("section", "instructor"):
        if resource_kind == "section":
            resources = {event.event_id: set(event.section_ids) for event in problem.events}
        else:
            resources = {event.event_id: set(event.instructor_ids) for event in problem.events}

        weekly_load_by_resource: dict[str, int] = defaultdict(int)
        day_terms: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for event in problem.events:
            for resource_id in resources[event.event_id]:
                weekly_load_by_resource[resource_id] += event.duration_atoms
                for candidate in event.candidates:
                    day_terms[(resource_id, candidate.day_id)].append(
                        event.duration_atoms
                        * variables[(event.event_id, candidate.candidate_id)]
                    )

        for resource_id, weekly_load in sorted(weekly_load_by_resource.items()):
            for day_id in day_ids:
                day_load = sum(day_terms[(resource_id, day_id)])
                deviation = model.NewIntVar(
                    0,
                    weekly_load * day_count,
                    f"load_deviation__{resource_kind}__{resource_id}__{day_id}",
                )
                model.AddAbsEquality(deviation, day_load * day_count - weekly_load)
                deviations.append(deviation)
    return sum(deviations)


def _map_status(status: int) -> SolverStatus:
    mapping = {
        cp_model.OPTIMAL: SolverStatus.OPTIMAL,
        cp_model.FEASIBLE: SolverStatus.FEASIBLE,
        cp_model.INFEASIBLE: SolverStatus.INFEASIBLE,
        cp_model.UNKNOWN: SolverStatus.UNKNOWN,
        cp_model.MODEL_INVALID: SolverStatus.ERROR,
    }
    return mapping.get(status, SolverStatus.UNKNOWN)
