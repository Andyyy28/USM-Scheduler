"""Optimization engines sharing the same immutable domain contracts."""

from .base import Solver
from .cp_sat import (
    CP_SAT_IMPLEMENTATION_VERSION,
    CPSATSolver,
    CpSatSolver,
    ORToolsUnavailableError,
    is_ortools_available,
)
from .genetic import GA_IMPLEMENTATION_VERSION, GASolver, GeneticAlgorithmSolver

__all__ = [
    "CPSATSolver",
    "CP_SAT_IMPLEMENTATION_VERSION",
    "GASolver",
    "GA_IMPLEMENTATION_VERSION",
    "CpSatSolver",
    "GeneticAlgorithmSolver",
    "ORToolsUnavailableError",
    "Solver",
    "is_ortools_available",
]
