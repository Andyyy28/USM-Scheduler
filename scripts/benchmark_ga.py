r"""Serial synthetic GA diagnostics; never formal thesis evaluation evidence.

Run from any directory with the project's Python 3.12+ environment::

    .venv\Scripts\python.exe scripts/benchmark_ga.py --output experiment-results/ga-before.json
    .venv\Scripts\python.exe scripts/benchmark_ga.py --output experiment-results/ga-replay.json \
        --solver-source experiment-results/ga-before-artifacts/genetic.py

Every scenario has an independently validated feasible witness. The witness is
saved for auditing, but is never passed to the solver (except declared locks).
Scenarios contain invented frozen evidence, not institutional records. Timed
search is sensitive to machine load: seeds reproduce inputs, not exact timed
incumbents. Run before/after comparisons serially with matching configurations.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import importlib.util
import json
import math
import platform
import pstats
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from types import ModuleType

# Direct script execution puts scripts/, not the repository, on sys.path.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scheduler.domain.contracts import (  # noqa: E402
    Assignment,
    CandidatePlacement,
    InstructorEvidence,
    MeetingEvent,
    ObjectiveProfile,
    ProblemInstance,
    RoomAuthorizationGrant,
    RoomAuthorizationRequirement,
    RoomEvidence,
    SolverAlgorithm,
    SolverConfig,
    TimeAtom,
)
from scheduler.domain.hashing import canonical_sha256  # noqa: E402
from scheduler.domain.scoring import score_schedule  # noqa: E402
from scheduler.domain.validation import validate_schedule  # noqa: E402

EVIDENCE_CLASS = "diagnostic_not_formal_thesis_evidence"
BENCHMARK_VERSION = "synthetic-ga-development-v1"
SCENARIO_IDS = ("moderate_mixed", "tight_contention", "daily_limit_stress")
HOLDOUT_IDS = ("unseen_dense", "unseen_daily")


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    description: str
    problem: ProblemInstance
    witness: tuple[Assignment, ...]


def _authorization(section_id: str) -> RoomAuthorizationRequirement:
    return RoomAuthorizationRequirement(
        section_id=section_id,
        classification="MAJOR",
        authoritative_college_id="SYN-COLLEGE",
        authoritative_department_id="SYN-DEPARTMENT",
        offering_college_id="SYN-COLLEGE",
        offering_department_id="SYN-DEPARTMENT",
    )


def build_scenarios(*, include_holdouts: bool = False) -> tuple[Scenario, ...]:
    """Construct deterministic complete schema-1.2 fixtures and feasible witnesses."""
    definitions = (
        ("moderate_mixed", 5, 8, 3, 2,
         "30 meetings with mixed room capabilities and crossing section/instructor use."),
        ("tight_contention", 4, 6, 4, 3,
         "48 meetings filling every room atom; laboratory and classroom domains differ."),
        ("daily_limit_stress", 5, 8, 4, 2,
         "40 meetings with binding instructor daily limits, 10 team-taught/shared meetings and locks."),
    )
    if include_holdouts:
        definitions += (
            ("unseen_dense", 5, 6, 4, 3, "Unseen 60-meeting fully occupied five-day grid."),
            ("unseen_daily", 4, 8, 4, 2, "Unseen 32-meeting four-day grid with binding daily limits."),
        )
    scenarios = []
    for scenario_id, day_count, atoms_per_day, room_count, waves, description in definitions:
        daily_stress = scenario_id in {"daily_limit_stress", "unseen_daily"}
        section_count = room_count * 2
        atoms = tuple(
            TimeAtom(f"D{day}-A{order}", f"D{day}", day, order)
            for day in range(day_count)
            for order in range(atoms_per_day)
        )
        all_atom_ids = tuple(atom.atom_id for atom in atoms)
        # The last atom is reserved in the two less dense cases. The tight
        # case deliberately uses its entire teaching grid.
        reserved = tuple(
            atom.atom_id for atom in atoms
            if atoms_per_day == 8 and atom.order == atoms_per_day - 1
        )
        rooms = tuple(
            RoomEvidence(
                room_id=f"SYN-R{room}",
                room_kind="LABORATORY" if room == 0 else "CLASSROOM",
                available_atom_ids=all_atom_ids,
                capability_ids=("PROJECTOR", "LAB-KIT") if room == 0 else ("PROJECTOR",),
                authorization_grants=(
                    RoomAuthorizationGrant("MAJOR", department_id="SYN-DEPARTMENT"),
                ),
                has_laboratory_profile=room == 0,
            )
            for room in range(room_count)
        )
        instructors = tuple(
            InstructorEvidence(
                instructor_id=f"SYN-I{instructor}",
                available_atom_ids=all_atom_ids,
                max_daily_teaching_atoms=(4 if instructor in (4, 5) else 2) if daily_stress else None,
                acknowledge_no_daily_limit=not daily_stress,
                daily_load_policy_hash=canonical_sha256({
                    "synthetic": True,
                    "scenario": scenario_id,
                    "instructor": instructor,
                    "daily_atoms": (4 if instructor in (4, 5) else 2) if daily_stress else None,
                }),
            )
            for instructor in range(section_count)
        )
        events = []
        witness = []
        for day in range(day_count):
            for wave in range(waves):
                for room in range(room_count):
                    event_index = len(events)
                    event_id = f"SYN-E{event_index:03d}"
                    section = room + (wave % 2) * room_count
                    section_ids = (f"SYN-S{section}",)
                    instructor = section if daily_stress else (section + day) % section_count
                    instructor_ids = (f"SYN-I{instructor}",)
                    if daily_stress and section in (0, 1):
                        section_ids += (f"SYN-S{section + 4}",)
                        instructor_ids += (f"SYN-I{instructor + 4}",)
                    headcounts = tuple((item, 22) for item in section_ids)
                    is_laboratory = room == 0
                    allowed_rooms = (0,) if is_laboratory else tuple(range(1, room_count))
                    candidates = []
                    for candidate_day in range(day_count):
                        for start in range(atoms_per_day - 1):
                            occupied = (f"D{candidate_day}-A{start}", f"D{candidate_day}-A{start + 1}")
                            if set(occupied) & set(reserved):
                                continue
                            for candidate_room in allowed_rooms:
                                candidates.append(CandidatePlacement(
                                    candidate_id=f"{event_id}-D{candidate_day}-A{start}-R{candidate_room}",
                                    room_id=f"SYN-R{candidate_room}",
                                    day_id=f"D{candidate_day}",
                                    start_atom_id=occupied[0],
                                    occupied_atom_ids=occupied,
                                    preference_penalty=(
                                        3 * ((candidate_day - event_index % day_count) % day_count)
                                        + abs(start - event_index % 3)
                                        + int(candidate_room != room)
                                    ),
                                ))
                    # Moderate/stress witnesses use spaced starts 0/4; tight
                    # witnesses tile all three two-atom blocks 0/2/4.
                    witness_start = wave * (2 if waves == 3 else 4)
                    witness.append(Assignment(
                        event_id=event_id,
                        candidate_id=f"{event_id}-D{day}-A{witness_start}-R{room}",
                    ))
                    events.append(MeetingEvent(
                        event_id=event_id,
                        offering_id=f"SYN-O{event_index:03d}",
                        duration_atoms=2,
                        section_ids=section_ids,
                        instructor_ids=instructor_ids,
                        candidates=tuple(candidates),
                        required_capability_ids=("LAB-KIT",) if is_laboratory else ("PROJECTOR",),
                        requires_laboratory_room=is_laboratory,
                        authorization_requirements=tuple(_authorization(item) for item in section_ids),
                        section_headcounts=headcounts,
                        meeting_headcount=sum(count for _, count in headcounts),
                        fixed_student_limit=50,
                        reserved_atom_ids=reserved,
                    ))
        locks = (witness[0], witness[len(witness) // 2], witness[-1]) if daily_stress else ()
        problem = ProblemInstance(
            schema_version="1.2",
            term_revision_id=f"{BENCHMARK_VERSION}-{scenario_id}",
            time_atoms=atoms,
            events=tuple(events),
            room_evidence=rooms,
            instructor_evidence=instructors,
            locked_assignments=locks,
            objective_profile=ObjectiveProfile(
                profile_id="synthetic-development-only-v1",
                preference_weight=1,
                section_gap_weight=2,
                instructor_gap_weight=2,
                load_imbalance_weight=1,
                preference_normalizer=len(events) * 20,
                section_gap_normalizer=section_count * day_count * atoms_per_day,
                instructor_gap_normalizer=section_count * day_count * atoms_per_day,
                load_imbalance_normalizer=len(events) * day_count * 8,
            ),
            metadata=(("evidence_class", EVIDENCE_CLASS), ("synthetic", "true")),
        )
        validation = validate_schedule(problem, tuple(witness))
        if not validation.feasible:
            raise ValueError(f"Scenario {scenario_id} witness is invalid: {validation.to_dict()}")
        scenarios.append(Scenario(scenario_id, description, problem, tuple(witness)))
    return tuple(scenarios)


def _load_solver(source: Path) -> ModuleType:
    """Load an explicit source snapshot without replacing the application's solver."""
    name = "_ga_diagnostic_" + hashlib.sha256(source.read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load Python solver source: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolves annotations through this registry
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_benchmark(
    output: Path,
    *,
    seconds: float = 2.0,
    seeds: tuple[int, ...] = (5001, 5002, 5003),
    solver_source: Path | None = None,
    profile: bool = False,
    cases: tuple[str, ...] = SCENARIO_IDS,
) -> dict:
    if not math.isfinite(seconds) or seconds <= 0 or not seeds:
        raise ValueError("seconds must be finite and positive; at least one seed is required")
    if len(seeds) != len(set(seeds)):
        raise ValueError("seeds must be unique")
    if not cases or len(cases) != len(set(cases)) or set(cases) - set(SCENARIO_IDS + HOLDOUT_IDS):
        raise ValueError("cases must be nonempty unique known scenario IDs")
    output = output.resolve()
    source = (solver_source or ROOT / "scheduler/solvers/genetic.py").resolve()
    source_bytes = source.read_bytes()
    solver_module = _load_solver(source)
    artifacts = output.parent / f"{output.stem}-artifacts"
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "genetic.py").write_bytes(source_bytes)
    source_files = (
        "scheduler/domain/contracts.py", "scheduler/domain/validation.py",
        "scheduler/domain/scoring.py", "scheduler/domain/hashing.py", "scheduler/solvers/tracing.py",
        "scheduler/domain/prepared.py", "scheduler/solvers/neighborhood.py",
    )
    report = {
        "benchmark_version": BENCHMARK_VERSION,
        "evidence_class": EVIDENCE_CLASS,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version, "executable": sys.executable,
            "platform": platform.platform(), "machine": platform.machine(),
        },
        "solver_source": str(source),
        "solver_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "solver_source_snapshot": str(artifacts / "genetic.py"),
        "supporting_source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files
        },
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "execution": {
            "mode": "serial", "worker_count": 1, "seeds": list(seeds),
            "seconds_per_measured_run": seconds, "profiled": profile,
            "warmup_seconds_per_case": min(seconds, 0.25),
            "warmup_seed": seeds[0], "cases": list(cases),
            "limitations": [
                "Synthetic diagnostic fixtures are not formal thesis evaluation evidence.",
                "No DB or institutional records are accessed; no profiles are frozen or winners selected.",
                "Time-bounded incumbents vary with machine load despite identical seeds.",
                "Profiler overhead changes the search budget; compare only matching profile modes.",
                "Replay also requires matching harness and supporting source hashes.",
            ],
        },
        "scenarios": [],
    }
    profiler = cProfile.Profile() if profile else None
    for scenario in build_scenarios(include_holdouts=bool(set(cases) & set(HOLDOUT_IDS))):
        if scenario.scenario_id not in cases:
            continue
        problem = scenario.problem
        problem_path = artifacts / f"{scenario.scenario_id}.problem.json"
        witness_path = artifacts / f"{scenario.scenario_id}.witness.json"
        _write_json(problem_path, problem.to_dict())
        _write_json(witness_path, [item.to_dict() for item in scenario.witness])
        scenario_report = {
            "scenario_id": scenario.scenario_id, "description": scenario.description,
            "problem_hash": problem.canonical_hash, "problem_snapshot": str(problem_path),
            "witness_snapshot": str(witness_path), "witness_feasible": True,
            "witness_objective": score_schedule(problem, scenario.witness).to_dict(),
            "event_count": len(problem.events),
            "candidate_count": sum(len(event.candidates) for event in problem.events),
            "candidate_domain_sizes": dict(Counter(len(event.candidates) for event in problem.events)),
            "locked_event_count": len(problem.locked_assignments),
            "runs": [],
        }
        report["scenarios"].append(scenario_report)
        # Warm the same implementation serially; its result is excluded from
        # all measured summaries and starts no persistent solver state.
        warm_config = SolverConfig(
            algorithm=SolverAlgorithm.GENETIC_ALGORITHM, seed=seeds[0],
            time_limit_seconds=min(seconds, 0.25), population_size=40, repair_attempts=20,
        )
        solver_module.GeneticAlgorithmSolver().solve(problem, warm_config)
        for seed in seeds:
            config = SolverConfig(
                algorithm=SolverAlgorithm.GENETIC_ALGORITHM, seed=seed,
                time_limit_seconds=seconds, worker_count=1, population_size=40,
                tournament_size=3, crossover_rate=0.9, mutation_rate=None,
                elite_fraction=0.05, repair_attempts=20, max_generations=None,
                diagnostic_trace=True,
            )
            started = perf_counter()
            if profiler is not None:
                profiler.enable()
            result = solver_module.GeneticAlgorithmSolver().solve(problem, config)
            if profiler is not None:
                profiler.disable()
            wall_seconds = perf_counter() - started
            validation = validate_schedule(problem, result.assignments)
            try:
                objective = score_schedule(problem, result.assignments)
            except ValueError:
                objective = None  # incomplete/no-incumbent results cannot be scored
            if validation != result.validation or objective != result.objective:
                raise RuntimeError(f"Independent result validation disagrees for {scenario.scenario_id}/{seed}")
            if result.problem_hash != problem.canonical_hash or result.config_hash != config.canonical_hash:
                raise RuntimeError("Solver returned mismatching problem/config provenance")
            record = {
                "seed": seed, "config": asdict(config), "config_hash": config.canonical_hash,
                "wall_seconds": wall_seconds, "feasible": validation.feasible,
                "hard_violations": validation.hard_violation_count,
                "raw_penalty": objective.weighted_total if objective is not None else None,
                "independently_validated": True, "result": result.to_dict(),
            }
            scenario_report["runs"].append(record)
            _write_json(output, report)  # completed observations survive later interruption
            print(
                f"{scenario.scenario_id} seed={seed} feasible={validation.feasible} "
                f"hard={validation.hard_violation_count} penalty={record['raw_penalty']} "
                f"wall={wall_seconds:.3f}s", flush=True,
            )
        feasible = [run for run in scenario_report["runs"] if run["feasible"]]
        scenario_report["summary"] = {
            "feasible_runs": len(feasible), "measured_runs": len(seeds),
            "median_feasible_raw_penalty": median(run["raw_penalty"] for run in feasible) if feasible else None,
            "median_wall_seconds": median(run["wall_seconds"] for run in scenario_report["runs"]),
        }
    if profiler is not None:
        profile_path = artifacts / "measured-runs.prof"
        profiler.dump_stats(str(profile_path))
        with (artifacts / "measured-runs-profile.txt").open("w", encoding="utf-8") as stream:
            pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(40)
        report["profile_path"] = str(profile_path)
    _write_json(output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True, help="JSON report path; snapshots go into a sibling artifacts directory")
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--seeds", type=int, nargs="+", default=[5001, 5002, 5003])
    parser.add_argument("--solver-source", type=Path, help="Saved trusted genetic.py source for a before/after replay")
    parser.add_argument("--profile", action="store_true", help="Profile measured solver calls; overhead changes timed search")
    parser.add_argument("--cases", choices=SCENARIO_IDS + HOLDOUT_IDS, nargs="+", default=list(SCENARIO_IDS))
    args = parser.parse_args()
    run_benchmark(args.output, seconds=args.seconds, seeds=tuple(args.seeds),
                  solver_source=args.solver_source, profile=args.profile, cases=tuple(args.cases))


if __name__ == "__main__":
    main()
