"""Immutable, JSON-serializable contracts for the scheduling engines.

The optimization package deliberately has no dependency on Django.  The web and
persistence layers translate database rows into these contracts, persist the
canonical snapshot, and translate :class:`SolverResult` back to ORM models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any, Self

from .hashing import canonical_sha256

type MetricValue = str | int | float | bool | None


class SolverAlgorithm(StrEnum):
    CP_SAT = "CP_SAT"
    GENETIC_ALGORITHM = "GENETIC_ALGORITHM"


class SolverStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    NO_SOLUTION = "NO_SOLUTION"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class ViolationCode(StrEnum):
    MISSING_ASSIGNMENT = "MISSING_ASSIGNMENT"
    DUPLICATE_ASSIGNMENT = "DUPLICATE_ASSIGNMENT"
    UNKNOWN_EVENT = "UNKNOWN_EVENT"
    INVALID_PLACEMENT = "INVALID_PLACEMENT"
    ROOM_CONFLICT = "ROOM_CONFLICT"
    INSTRUCTOR_CONFLICT = "INSTRUCTOR_CONFLICT"
    SECTION_CONFLICT = "SECTION_CONFLICT"
    DISTINCT_DAY_CONFLICT = "DISTINCT_DAY_CONFLICT"
    LOCK_VIOLATION = "LOCK_VIOLATION"
    DURATION_MISMATCH = "DURATION_MISMATCH"
    MISSING_ROOM_EVIDENCE = "MISSING_ROOM_EVIDENCE"
    MISSING_INSTRUCTOR_EVIDENCE = "MISSING_INSTRUCTOR_EVIDENCE"
    ROOM_UNAVAILABLE = "ROOM_UNAVAILABLE"
    INSTRUCTOR_UNAVAILABLE = "INSTRUCTOR_UNAVAILABLE"
    ROOM_CAPABILITY_MISMATCH = "ROOM_CAPABILITY_MISMATCH"
    LABORATORY_ROOM_REQUIRED = "LABORATORY_ROOM_REQUIRED"
    MISSING_AUTHORIZATION_EVIDENCE = "MISSING_AUTHORIZATION_EVIDENCE"
    ROOM_AUTHORIZATION_VIOLATION = "ROOM_AUTHORIZATION_VIOLATION"


@dataclass(frozen=True, slots=True)
class TimeAtom:
    """One schedulable atom in a term's weekly grid.

    ``order`` is compared only within a day. Breaks should be omitted from the
    grid, so they are not counted as avoidable vacant periods.
    """

    atom_id: str
    day_id: str
    day_index: int
    order: int

    def __post_init__(self) -> None:
        _require_text("atom_id", self.atom_id)
        _require_text("day_id", self.day_id)
        if self.day_index < 0:
            raise ValueError("day_index must be non-negative")
        if self.order < 0:
            raise ValueError("order must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "day_id": self.day_id,
            "day_index": self.day_index,
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            atom_id=str(value["atom_id"]),
            day_id=str(value["day_id"]),
            day_index=int(value["day_index"]),
            order=int(value["order"]),
        )


@dataclass(frozen=True, slots=True)
class RoomAuthorizationRequirement:
    """One attached section's room-policy requirement.

    Both curriculum-authoritative and offering-unit identifiers are retained so
    the validator can choose the applicable policy from the raw classification,
    without trusting preprocessing's candidate decision.
    """

    section_id: str
    classification: str
    authoritative_college_id: str
    offering_college_id: str
    offering_department_id: str
    authoritative_department_id: str | None = None

    def __post_init__(self) -> None:
        _require_text("section_id", self.section_id)
        _require_text("classification", self.classification)
        _require_text("authoritative_college_id", self.authoritative_college_id)
        _require_text("offering_college_id", self.offering_college_id)
        _require_text("offering_department_id", self.offering_department_id)
        if self.authoritative_department_id is not None:
            _require_text("authoritative_department_id", self.authoritative_department_id)

    @property
    def applicable_college_id(self) -> str:
        return (
            self.authoritative_college_id
            if self.classification == "MAJOR"
            else self.offering_college_id
        )

    @property
    def applicable_department_id(self) -> str | None:
        return (
            self.authoritative_department_id
            if self.classification == "MAJOR"
            else self.offering_department_id
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "classification": self.classification,
            "authoritative_college_id": self.authoritative_college_id,
            "authoritative_department_id": self.authoritative_department_id,
            "offering_college_id": self.offering_college_id,
            "offering_department_id": self.offering_department_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            section_id=str(value["section_id"]),
            classification=str(value["classification"]),
            authoritative_college_id=str(value["authoritative_college_id"]),
            authoritative_department_id=_optional_text(
                value.get("authoritative_department_id")
            ),
            offering_college_id=str(value["offering_college_id"]),
            offering_department_id=str(value["offering_department_id"]),
        )


@dataclass(frozen=True, slots=True)
class RoomAuthorizationGrant:
    """One revision-specific room authorization granted to exactly one unit."""

    classification: str
    college_id: str | None = None
    department_id: str | None = None

    def __post_init__(self) -> None:
        _require_text("classification", self.classification)
        if (self.college_id is None) == (self.department_id is None):
            raise ValueError("an authorization grant must target exactly one unit")
        if self.college_id is not None:
            _require_text("college_id", self.college_id)
        if self.department_id is not None:
            _require_text("department_id", self.department_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "college_id": self.college_id,
            "department_id": self.department_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            classification=str(value["classification"]),
            college_id=_optional_text(value.get("college_id")),
            department_id=_optional_text(value.get("department_id")),
        )


@dataclass(frozen=True, slots=True)
class RoomEvidence:
    """Raw room facts frozen into schema 1.1+ snapshots for revalidation."""

    room_id: str
    room_kind: str
    available_atom_ids: tuple[str, ...]
    capability_ids: tuple[str, ...] = ()
    authorization_grants: tuple[RoomAuthorizationGrant, ...] = ()
    has_laboratory_profile: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "available_atom_ids", tuple(self.available_atom_ids))
        object.__setattr__(self, "capability_ids", tuple(self.capability_ids))
        object.__setattr__(self, "authorization_grants", tuple(self.authorization_grants))
        _require_text("room_id", self.room_id)
        _require_text("room_kind", self.room_kind)
        if len(self.available_atom_ids) != len(set(self.available_atom_ids)):
            raise ValueError("room available atom IDs must be unique")
        if len(self.capability_ids) != len(set(self.capability_ids)):
            raise ValueError("room capability IDs must be unique")
        if type(self.has_laboratory_profile) is not bool:
            raise ValueError("has_laboratory_profile must be Boolean")
        grant_keys = [
            (grant.classification, grant.college_id, grant.department_id)
            for grant in self.authorization_grants
        ]
        if len(grant_keys) != len(set(grant_keys)):
            raise ValueError("room authorization grants must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "room_kind": self.room_kind,
            "available_atom_ids": sorted(self.available_atom_ids),
            "capability_ids": sorted(self.capability_ids),
            "authorization_grants": [
                grant.to_dict()
                for grant in sorted(
                    self.authorization_grants,
                    key=lambda item: (
                        item.classification,
                        item.college_id or "",
                        item.department_id or "",
                    ),
                )
            ],
            "has_laboratory_profile": self.has_laboratory_profile,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            room_id=str(value["room_id"]),
            room_kind=str(value["room_kind"]),
            available_atom_ids=tuple(str(item) for item in value.get("available_atom_ids", ())),
            capability_ids=tuple(str(item) for item in value.get("capability_ids", ())),
            authorization_grants=tuple(
                RoomAuthorizationGrant.from_dict(item)
                for item in value.get("authorization_grants", ())
            ),
            has_laboratory_profile=value.get("has_laboratory_profile", False),
        )


@dataclass(frozen=True, slots=True)
class InstructorEvidence:
    """Raw instructor availability frozen into schema 1.1+ snapshots."""

    instructor_id: str
    available_atom_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "available_atom_ids", tuple(self.available_atom_ids))
        _require_text("instructor_id", self.instructor_id)
        if len(self.available_atom_ids) != len(set(self.available_atom_ids)):
            raise ValueError("instructor available atom IDs must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "instructor_id": self.instructor_id,
            "available_atom_ids": sorted(self.available_atom_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            instructor_id=str(value["instructor_id"]),
            available_atom_ids=tuple(str(item) for item in value.get("available_atom_ids", ())),
        )


@dataclass(frozen=True, slots=True)
class CandidatePlacement:
    """A locally legal room/time placement for one meeting event.

    Candidate domains are produced once by preprocessing and shared verbatim by
    CP-SAT and GA. ``preference_penalty`` is the already aggregated instructor
    preference cost for this placement; lower is better.
    """

    candidate_id: str
    room_id: str
    day_id: str
    start_atom_id: str
    occupied_atom_ids: tuple[str, ...]
    preference_penalty: int = 0
    eligibility_metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "occupied_atom_ids", tuple(self.occupied_atom_ids))
        object.__setattr__(
            self,
            "eligibility_metadata",
            tuple((str(key), str(value)) for key, value in self.eligibility_metadata),
        )
        _require_text("candidate_id", self.candidate_id)
        _require_text("room_id", self.room_id)
        _require_text("day_id", self.day_id)
        _require_text("start_atom_id", self.start_atom_id)
        if not self.occupied_atom_ids:
            raise ValueError("occupied_atom_ids must not be empty")
        if len(set(self.occupied_atom_ids)) != len(self.occupied_atom_ids):
            raise ValueError("occupied_atom_ids must be unique")
        if self.start_atom_id != self.occupied_atom_ids[0]:
            raise ValueError("start_atom_id must be the first occupied atom")
        if self.preference_penalty < 0:
            raise ValueError("preference_penalty must be non-negative")
        keys = [key for key, _ in self.eligibility_metadata]
        if len(keys) != len(set(keys)):
            raise ValueError("eligibility_metadata keys must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "room_id": self.room_id,
            "day_id": self.day_id,
            "start_atom_id": self.start_atom_id,
            "occupied_atom_ids": list(self.occupied_atom_ids),
            "preference_penalty": self.preference_penalty,
            "eligibility_metadata": [list(item) for item in sorted(self.eligibility_metadata)],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            candidate_id=str(value["candidate_id"]),
            room_id=str(value["room_id"]),
            day_id=str(value["day_id"]),
            start_atom_id=str(value["start_atom_id"]),
            occupied_atom_ids=tuple(str(item) for item in value["occupied_atom_ids"]),
            preference_penalty=int(value.get("preference_penalty", 0)),
            eligibility_metadata=tuple(
                (str(item[0]), str(item[1])) for item in value.get("eligibility_metadata", ())
            ),
        )


@dataclass(frozen=True, slots=True)
class MeetingEvent:
    """One meeting occurrence that must receive exactly one placement."""

    event_id: str
    duration_atoms: int
    section_ids: tuple[str, ...]
    instructor_ids: tuple[str, ...]
    candidates: tuple[CandidatePlacement, ...]
    distinct_day_group: str | None = None
    offering_id: str | None = None
    required_capability_ids: tuple[str, ...] = ()
    requires_laboratory_room: bool = False
    authorization_requirements: tuple[RoomAuthorizationRequirement, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "section_ids", tuple(self.section_ids))
        object.__setattr__(self, "instructor_ids", tuple(self.instructor_ids))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "required_capability_ids", tuple(self.required_capability_ids))
        object.__setattr__(
            self,
            "authorization_requirements",
            tuple(self.authorization_requirements),
        )
        _require_text("event_id", self.event_id)
        if self.duration_atoms <= 0:
            raise ValueError("duration_atoms must be positive")
        if len(set(self.section_ids)) != len(self.section_ids):
            raise ValueError("section_ids must be unique")
        if len(set(self.instructor_ids)) != len(self.instructor_ids):
            raise ValueError("instructor_ids must be unique")
        if len(set(self.required_capability_ids)) != len(self.required_capability_ids):
            raise ValueError("required capability IDs must be unique")
        if type(self.requires_laboratory_room) is not bool:
            raise ValueError("requires_laboratory_room must be Boolean")
        requirement_sections = [
            requirement.section_id for requirement in self.authorization_requirements
        ]
        if len(requirement_sections) != len(set(requirement_sections)):
            raise ValueError("only one authorization requirement is allowed per section")
        if any(section_id not in self.section_ids for section_id in requirement_sections):
            raise ValueError("authorization requirements must refer to attached sections")
        if not self.candidates:
            raise ValueError(f"event {self.event_id!r} has no legal candidate placements")
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(f"event {self.event_id!r} contains duplicate candidate IDs")

    @property
    def candidate_map(self) -> dict[str, CandidatePlacement]:
        return {candidate.candidate_id: candidate for candidate in self.candidates}

    def to_dict(self) -> dict[str, Any]:
        result = {
            "event_id": self.event_id,
            "duration_atoms": self.duration_atoms,
            "section_ids": sorted(self.section_ids),
            "instructor_ids": sorted(self.instructor_ids),
            "candidates": [
                candidate.to_dict()
                for candidate in sorted(self.candidates, key=lambda candidate: candidate.candidate_id)
            ],
            "distinct_day_group": self.distinct_day_group,
            "offering_id": self.offering_id,
        }
        # Omitting empty schema-1.1 additions preserves canonical schema-1.0
        # payloads and allows old synthetic snapshots to round-trip unchanged.
        if self.required_capability_ids:
            result["required_capability_ids"] = sorted(self.required_capability_ids)
        if self.requires_laboratory_room:
            result["requires_laboratory_room"] = True
        if self.authorization_requirements:
            result["authorization_requirements"] = [
                requirement.to_dict()
                for requirement in sorted(
                    self.authorization_requirements,
                    key=lambda item: item.section_id,
                )
            ]
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            event_id=str(value["event_id"]),
            duration_atoms=int(value["duration_atoms"]),
            section_ids=tuple(str(item) for item in value.get("section_ids", ())),
            instructor_ids=tuple(str(item) for item in value.get("instructor_ids", ())),
            candidates=tuple(CandidatePlacement.from_dict(item) for item in value["candidates"]),
            distinct_day_group=_optional_text(value.get("distinct_day_group")),
            offering_id=_optional_text(value.get("offering_id")),
            required_capability_ids=tuple(
                str(item) for item in value.get("required_capability_ids", ())
            ),
            requires_laboratory_room=value.get("requires_laboratory_room", False),
            authorization_requirements=tuple(
                RoomAuthorizationRequirement.from_dict(item)
                for item in value.get("authorization_requirements", ())
            ),
        )


@dataclass(frozen=True, slots=True)
class Assignment:
    event_id: str
    candidate_id: str

    def __post_init__(self) -> None:
        _require_text("event_id", self.event_id)
        _require_text("candidate_id", self.candidate_id)

    def to_dict(self) -> dict[str, str]:
        return {"event_id": self.event_id, "candidate_id": self.candidate_id}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(event_id=str(value["event_id"]), candidate_id=str(value["candidate_id"]))


@dataclass(frozen=True, slots=True)
class ObjectiveProfile:
    """Versioned integer weights and display normalizers for soft objectives."""

    profile_id: str = "development-equal-weights-v1"
    preference_weight: int = 1
    section_gap_weight: int = 1
    instructor_gap_weight: int = 1
    load_imbalance_weight: int = 1
    preference_normalizer: int = 1
    section_gap_normalizer: int = 1
    instructor_gap_normalizer: int = 1
    load_imbalance_normalizer: int = 1

    def __post_init__(self) -> None:
        _require_text("profile_id", self.profile_id)
        for name in (
            "preference_weight",
            "section_gap_weight",
            "instructor_gap_weight",
            "load_imbalance_weight",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in (
            "preference_normalizer",
            "section_gap_normalizer",
            "instructor_gap_normalizer",
            "load_imbalance_normalizer",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    @property
    def canonical_hash(self) -> str:
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "preference_weight": self.preference_weight,
            "section_gap_weight": self.section_gap_weight,
            "instructor_gap_weight": self.instructor_gap_weight,
            "load_imbalance_weight": self.load_imbalance_weight,
            "preference_normalizer": self.preference_normalizer,
            "section_gap_normalizer": self.section_gap_normalizer,
            "instructor_gap_normalizer": self.instructor_gap_normalizer,
            "load_imbalance_normalizer": self.load_imbalance_normalizer,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(**{name: value[name] for name in cls.__dataclass_fields__ if name in value})


@dataclass(frozen=True, slots=True)
class ObjectiveBreakdown:
    preference_penalty: int
    section_gap_atoms: int
    instructor_gap_atoms: int
    load_imbalance: int
    weighted_total: int
    quality_score: float

    def __post_init__(self) -> None:
        for name in (
            "preference_penalty",
            "section_gap_atoms",
            "instructor_gap_atoms",
            "load_imbalance",
            "weighted_total",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if not isfinite(self.quality_score) or not 0.0 <= self.quality_score <= 100.0:
            raise ValueError("quality_score must be a finite value from 0 to 100")

    def to_dict(self) -> dict[str, Any]:
        return {
            "preference_penalty": self.preference_penalty,
            "section_gap_atoms": self.section_gap_atoms,
            "instructor_gap_atoms": self.instructor_gap_atoms,
            "load_imbalance": self.load_imbalance,
            "weighted_total": self.weighted_total,
            "quality_score": self.quality_score,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            preference_penalty=int(value["preference_penalty"]),
            section_gap_atoms=int(value["section_gap_atoms"]),
            instructor_gap_atoms=int(value["instructor_gap_atoms"]),
            load_imbalance=int(value["load_imbalance"]),
            weighted_total=int(value["weighted_total"]),
            quality_score=float(value["quality_score"]),
        )


@dataclass(frozen=True, slots=True)
class Violation:
    code: ViolationCode
    message: str
    event_ids: tuple[str, ...] = ()
    resource_id: str | None = None
    atom_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", ViolationCode(self.code))
        object.__setattr__(self, "event_ids", tuple(self.event_ids))
        object.__setattr__(self, "atom_ids", tuple(self.atom_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "event_ids": list(self.event_ids),
            "resource_id": self.resource_id,
            "atom_ids": list(self.atom_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            code=ViolationCode(value["code"]),
            message=str(value["message"]),
            event_ids=tuple(str(item) for item in value.get("event_ids", ())),
            resource_id=_optional_text(value.get("resource_id")),
            atom_ids=tuple(str(item) for item in value.get("atom_ids", ())),
        )


@dataclass(frozen=True, slots=True)
class ValidationReport:
    feasible: bool
    violations: tuple[Violation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "violations", tuple(self.violations))
        if self.feasible != (not self.violations):
            raise ValueError("feasible must be true exactly when violations is empty")

    @property
    def counts(self) -> tuple[tuple[str, int], ...]:
        totals: dict[str, int] = {}
        for violation in self.violations:
            totals[violation.code.value] = totals.get(violation.code.value, 0) + 1
        return tuple(sorted(totals.items()))

    @property
    def hard_violation_count(self) -> int:
        return len(self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "violations": [violation.to_dict() for violation in self.violations],
            "counts": dict(self.counts),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            feasible=bool(value["feasible"]),
            violations=tuple(Violation.from_dict(item) for item in value.get("violations", ())),
        )


@dataclass(frozen=True, slots=True)
class ProblemInstance:
    schema_version: str
    term_revision_id: str
    time_atoms: tuple[TimeAtom, ...]
    events: tuple[MeetingEvent, ...]
    objective_profile: ObjectiveProfile = field(default_factory=ObjectiveProfile)
    room_evidence: tuple[RoomEvidence, ...] = ()
    instructor_evidence: tuple[InstructorEvidence, ...] = ()
    locked_assignments: tuple[Assignment, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_atoms", tuple(self.time_atoms))
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "room_evidence", tuple(self.room_evidence))
        object.__setattr__(self, "instructor_evidence", tuple(self.instructor_evidence))
        object.__setattr__(self, "locked_assignments", tuple(self.locked_assignments))
        object.__setattr__(
            self,
            "metadata",
            tuple((str(key), str(value)) for key, value in self.metadata),
        )
        _require_text("schema_version", self.schema_version)
        _require_text("term_revision_id", self.term_revision_id)
        atom_ids = [atom.atom_id for atom in self.time_atoms]
        if len(atom_ids) != len(set(atom_ids)):
            raise ValueError("time atom IDs must be unique")
        day_order_pairs = [(atom.day_id, atom.order) for atom in self.time_atoms]
        if len(day_order_pairs) != len(set(day_order_pairs)):
            raise ValueError("time atom order must be unique within each day")
        day_indexes: dict[str, int] = {}
        for atom in self.time_atoms:
            previous = day_indexes.setdefault(atom.day_id, atom.day_index)
            if previous != atom.day_index:
                raise ValueError(f"day {atom.day_id!r} has inconsistent day indexes")
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event IDs must be unique")
        room_ids = [evidence.room_id for evidence in self.room_evidence]
        if len(room_ids) != len(set(room_ids)):
            raise ValueError("room evidence IDs must be unique")
        instructor_ids = [evidence.instructor_id for evidence in self.instructor_evidence]
        if len(instructor_ids) != len(set(instructor_ids)):
            raise ValueError("instructor evidence IDs must be unique")
        atom_map = self.atom_map
        for event in self.events:
            for candidate in event.candidates:
                if (
                    not self.supports_independent_hard_rule_validation
                    and len(candidate.occupied_atom_ids) != event.duration_atoms
                ):
                    raise ValueError(
                        f"candidate {candidate.candidate_id!r} does not match event duration"
                    )
                try:
                    occupied_atoms = [atom_map[atom_id] for atom_id in candidate.occupied_atom_ids]
                except KeyError as exc:
                    raise ValueError(
                        f"candidate {candidate.candidate_id!r} references an unknown time atom"
                    ) from exc
                if any(atom.day_id != candidate.day_id for atom in occupied_atoms):
                    raise ValueError(
                        f"candidate {candidate.candidate_id!r} spans multiple or inconsistent days"
                    )
                orders = [atom.order for atom in occupied_atoms]
                if orders != list(range(orders[0], orders[0] + len(orders))):
                    raise ValueError(
                        f"candidate {candidate.candidate_id!r} must occupy contiguous time atoms"
                    )
        event_map = self.event_map
        lock_event_ids = [lock.event_id for lock in self.locked_assignments]
        if len(lock_event_ids) != len(set(lock_event_ids)):
            raise ValueError("only one lock is allowed per event")
        for lock in self.locked_assignments:
            if lock.event_id not in event_map:
                raise ValueError(f"lock references unknown event {lock.event_id!r}")
            if lock.candidate_id not in event_map[lock.event_id].candidate_map:
                raise ValueError(
                    f"lock for {lock.event_id!r} references an invalid candidate"
                )
        metadata_keys = [key for key, _ in self.metadata]
        if len(metadata_keys) != len(set(metadata_keys)):
            raise ValueError("metadata keys must be unique")

    @property
    def atom_map(self) -> dict[str, TimeAtom]:
        return {atom.atom_id: atom for atom in self.time_atoms}

    @property
    def event_map(self) -> dict[str, MeetingEvent]:
        return {event.event_id: event for event in self.events}

    @property
    def lock_map(self) -> dict[str, str]:
        return {lock.event_id: lock.candidate_id for lock in self.locked_assignments}

    @property
    def room_evidence_map(self) -> dict[str, RoomEvidence]:
        return {evidence.room_id: evidence for evidence in self.room_evidence}

    @property
    def instructor_evidence_map(self) -> dict[str, InstructorEvidence]:
        return {
            evidence.instructor_id: evidence for evidence in self.instructor_evidence
        }

    @property
    def supports_independent_hard_rule_validation(self) -> bool:
        return _schema_version_at_least(self.schema_version, 1, 1)

    @property
    def day_ids(self) -> tuple[str, ...]:
        day_indexes: dict[str, int] = {}
        for atom in self.time_atoms:
            day_indexes[atom.day_id] = atom.day_index
        return tuple(day for day, _ in sorted(day_indexes.items(), key=lambda item: (item[1], item[0])))

    @property
    def canonical_hash(self) -> str:
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "term_revision_id": self.term_revision_id,
            "time_atoms": [
                atom.to_dict()
                for atom in sorted(
                    self.time_atoms,
                    key=lambda atom: (atom.day_index, atom.order, atom.atom_id),
                )
            ],
            "events": [
                event.to_dict() for event in sorted(self.events, key=lambda event: event.event_id)
            ],
            "objective_profile": self.objective_profile.to_dict(),
            "locked_assignments": [
                lock.to_dict()
                for lock in sorted(self.locked_assignments, key=lambda lock: lock.event_id)
            ],
            "metadata": [list(item) for item in sorted(self.metadata)],
        }
        if self.room_evidence:
            result["room_evidence"] = [
                evidence.to_dict()
                for evidence in sorted(self.room_evidence, key=lambda item: item.room_id)
            ]
        if self.instructor_evidence:
            result["instructor_evidence"] = [
                evidence.to_dict()
                for evidence in sorted(
                    self.instructor_evidence,
                    key=lambda item: item.instructor_id,
                )
            ]
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            schema_version=str(value["schema_version"]),
            term_revision_id=str(value["term_revision_id"]),
            time_atoms=tuple(TimeAtom.from_dict(item) for item in value["time_atoms"]),
            events=tuple(MeetingEvent.from_dict(item) for item in value["events"]),
            objective_profile=ObjectiveProfile.from_dict(value["objective_profile"]),
            room_evidence=tuple(
                RoomEvidence.from_dict(item) for item in value.get("room_evidence", ())
            ),
            instructor_evidence=tuple(
                InstructorEvidence.from_dict(item)
                for item in value.get("instructor_evidence", ())
            ),
            locked_assignments=tuple(
                Assignment.from_dict(item) for item in value.get("locked_assignments", ())
            ),
            metadata=tuple((str(item[0]), str(item[1])) for item in value.get("metadata", ())),
        )


@dataclass(frozen=True, slots=True)
class SolverConfig:
    algorithm: SolverAlgorithm
    seed: int = 0
    time_limit_seconds: float = 300.0
    worker_count: int = 1
    population_size: int = 200
    tournament_size: int = 3
    crossover_rate: float = 0.9
    mutation_rate: float | None = None
    elite_fraction: float = 0.05
    repair_attempts: int = 20
    max_generations: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "algorithm", SolverAlgorithm(self.algorithm))
        object.__setattr__(self, "time_limit_seconds", float(self.time_limit_seconds))
        object.__setattr__(self, "crossover_rate", float(self.crossover_rate))
        if self.mutation_rate is not None:
            object.__setattr__(self, "mutation_rate", float(self.mutation_rate))
        object.__setattr__(self, "elite_fraction", float(self.elite_fraction))
        if not isfinite(self.time_limit_seconds) or self.time_limit_seconds <= 0:
            raise ValueError("time_limit_seconds must be finite and positive")
        if self.worker_count <= 0:
            raise ValueError("worker_count must be positive")
        if self.population_size < 2:
            raise ValueError("population_size must be at least 2")
        if not 2 <= self.tournament_size <= self.population_size:
            raise ValueError("tournament_size must be between 2 and population_size")
        for name in ("crossover_rate", "elite_fraction"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.mutation_rate is not None and not 0.0 <= self.mutation_rate <= 1.0:
            raise ValueError("mutation_rate must be between 0 and 1")
        if self.repair_attempts < 0:
            raise ValueError("repair_attempts must be non-negative")
        if self.max_generations is not None and self.max_generations <= 0:
            raise ValueError("max_generations must be positive when provided")

    @property
    def canonical_hash(self) -> str:
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm.value,
            "seed": self.seed,
            "time_limit_seconds": self.time_limit_seconds,
            "worker_count": self.worker_count,
            "population_size": self.population_size,
            "tournament_size": self.tournament_size,
            "crossover_rate": self.crossover_rate,
            "mutation_rate": self.mutation_rate,
            "elite_fraction": self.elite_fraction,
            "repair_attempts": self.repair_attempts,
            "max_generations": self.max_generations,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        data = dict(value)
        data["algorithm"] = SolverAlgorithm(data["algorithm"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class SolverResult:
    algorithm: SolverAlgorithm
    status: SolverStatus
    assignments: tuple[Assignment, ...]
    validation: ValidationReport
    objective: ObjectiveBreakdown | None
    runtime_seconds: float
    first_feasible_seconds: float | None
    stopping_reason: str
    seed: int
    problem_hash: str
    config_hash: str
    metrics: tuple[tuple[str, MetricValue], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "algorithm", SolverAlgorithm(self.algorithm))
        object.__setattr__(self, "status", SolverStatus(self.status))
        object.__setattr__(self, "assignments", tuple(self.assignments))
        object.__setattr__(self, "metrics", tuple(self.metrics))
        object.__setattr__(self, "runtime_seconds", float(self.runtime_seconds))
        if self.first_feasible_seconds is not None:
            object.__setattr__(
                self, "first_feasible_seconds", float(self.first_feasible_seconds)
            )
        if self.runtime_seconds < 0 or not isfinite(self.runtime_seconds):
            raise ValueError("runtime_seconds must be finite and non-negative")
        if self.first_feasible_seconds is not None and (
            self.first_feasible_seconds < 0 or not isfinite(self.first_feasible_seconds)
        ):
            raise ValueError("first_feasible_seconds must be finite and non-negative")
        metric_names = [name for name, _ in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("metric names must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm.value,
            "status": self.status.value,
            "assignments": [
                assignment.to_dict()
                for assignment in sorted(self.assignments, key=lambda item: item.event_id)
            ],
            "validation": self.validation.to_dict(),
            "objective": None if self.objective is None else self.objective.to_dict(),
            "runtime_seconds": self.runtime_seconds,
            "first_feasible_seconds": self.first_feasible_seconds,
            "stopping_reason": self.stopping_reason,
            "seed": self.seed,
            "problem_hash": self.problem_hash,
            "config_hash": self.config_hash,
            "metrics": {name: value for name, value in sorted(self.metrics)},
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        objective = value.get("objective")
        metrics = value.get("metrics", {})
        metric_items = metrics.items() if isinstance(metrics, dict) else metrics
        return cls(
            algorithm=SolverAlgorithm(value["algorithm"]),
            status=SolverStatus(value["status"]),
            assignments=tuple(Assignment.from_dict(item) for item in value.get("assignments", ())),
            validation=ValidationReport.from_dict(value["validation"]),
            objective=None if objective is None else ObjectiveBreakdown.from_dict(objective),
            runtime_seconds=float(value["runtime_seconds"]),
            first_feasible_seconds=(
                None
                if value.get("first_feasible_seconds") is None
                else float(value["first_feasible_seconds"])
            ),
            stopping_reason=str(value["stopping_reason"]),
            seed=int(value["seed"]),
            problem_hash=str(value["problem_hash"]),
            config_hash=str(value["config_hash"]),
            metrics=tuple((str(name), metric) for name, metric in metric_items),
        )


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value)
    return result if result else None


def _schema_version_at_least(value: str, major: int, minor: int) -> bool:
    try:
        parts = value.split(".", 2)
        current = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (AttributeError, TypeError, ValueError):
        return False
    return current >= (major, minor)
