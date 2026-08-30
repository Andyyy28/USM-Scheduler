"""Read-only lookup indexes bound to one immutable problem, not cached verdicts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from types import MappingProxyType

from .contracts import CandidatePlacement, MeetingEvent, ProblemInstance


@dataclass(frozen=True, slots=True, init=False)
class PreparedProblem:
    problem: ProblemInstance
    events: tuple[MeetingEvent, ...]
    event_map: Mapping[str, MeetingEvent]
    candidates: Mapping[str, Mapping[str, CandidatePlacement]]
    atom_positions: Mapping[str, int]
    day_ids: tuple[str, ...]

    def __init__(
        self, problem: ProblemInstance, *, deadline: float = float("inf"),
        clock: Callable[[], float] = monotonic,
    ) -> None:
        def check() -> None:
            if clock() >= deadline:
                raise TimeoutError("problem preparation exceeded the solver deadline")

        check()
        candidate_maps = {}
        for event in problem.events:
            check()
            candidates = {}
            for index, candidate in enumerate(event.candidates):
                if index % 128 == 0:
                    check()
                candidates[candidate.candidate_id] = candidate
            candidate_maps[event.event_id] = MappingProxyType(candidates)
        positions = {}
        day_counts: dict[str, int] = {}
        for atom in sorted(problem.time_atoms, key=lambda item: (item.day_index, item.order, item.atom_id)):
            check()
            positions[atom.atom_id] = day_counts.get(atom.day_id, 0)
            day_counts[atom.day_id] = positions[atom.atom_id] + 1
        events = tuple(sorted(problem.events, key=lambda event: event.event_id))
        event_map = MappingProxyType(problem.event_map)
        check()
        object.__setattr__(self, "problem", problem)
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "event_map", event_map)
        object.__setattr__(self, "candidates", MappingProxyType(candidate_maps))
        object.__setattr__(self, "atom_positions", MappingProxyType(positions))
        object.__setattr__(self, "day_ids", tuple(day_counts))

    def require_problem(self, problem: ProblemInstance) -> None:
        if self.problem is not problem:
            raise ValueError("prepared context belongs to a different problem instance")
