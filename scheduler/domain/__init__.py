"""Public, persistence-agnostic scheduling domain API."""

from .contracts import (
    Assignment,
    CandidatePlacement,
    InstructorEvidence,
    MeetingEvent,
    ObjectiveBreakdown,
    ObjectiveProfile,
    ProblemInstance,
    RoomAuthorizationGrant,
    RoomAuthorizationRequirement,
    RoomEvidence,
    SolverAlgorithm,
    SolverConfig,
    SolverResult,
    SolverStatus,
    TimeAtom,
    ValidationReport,
    Violation,
    ViolationCode,
)
from .hashing import canonical_json, canonical_sha256
from .prepared import PreparedProblem
from .scoring import resolve_assignments, score_schedule
from .validation import validate_schedule

__all__ = [
    "Assignment",
    "CandidatePlacement",
    "InstructorEvidence",
    "MeetingEvent",
    "ObjectiveBreakdown",
    "ObjectiveProfile",
    "PreparedProblem",
    "ProblemInstance",
    "RoomAuthorizationGrant",
    "RoomAuthorizationRequirement",
    "RoomEvidence",
    "SolverAlgorithm",
    "SolverConfig",
    "SolverResult",
    "SolverStatus",
    "TimeAtom",
    "ValidationReport",
    "Violation",
    "ViolationCode",
    "canonical_json",
    "canonical_sha256",
    "resolve_assignments",
    "score_schedule",
    "validate_schedule",
]
