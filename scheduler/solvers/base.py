"""Common solver interface."""

from __future__ import annotations

from typing import Protocol

from scheduler.domain.contracts import ProblemInstance, SolverConfig, SolverResult


class Solver(Protocol):
    def solve(self, problem: ProblemInstance, config: SolverConfig) -> SolverResult:
        """Solve one immutable problem snapshot using one immutable configuration."""
