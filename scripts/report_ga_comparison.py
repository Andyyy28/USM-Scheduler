"""Revalidate saved synthetic observations and summarize development comparisons."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scheduler.domain import (  # noqa: E402
    Assignment,
    ProblemInstance,
    SolverConfig,
    SolverResult,
    score_schedule,
    validate_schedule,
)


def summarize(directories: list[Path], *, baseline: str = "ga-v4") -> dict:
    observations = []
    seen = set()
    identities = {}
    supporting_sources = None
    for directory in directories:
        directory = directory.resolve()
        matrix = json.loads((directory / "matrix.json").read_text(encoding="utf-8"))
        expected = {
            f"{label}-{seconds:g}s-{seed}-{case}.json": (label, seconds, seed, case)
            for label, seconds, seed, case in product(matrix["sources"], matrix["budgets"], matrix["seeds"], matrix["cases"])
        }
        if (len(matrix["completed_reports"]) != matrix["expected_reports"]
                or len(expected) != matrix["expected_reports"]
                or set(matrix["completed_reports"]) != set(expected)):
            raise ValueError(f"Incomplete comparison matrix: {directory}")
        for name in matrix["completed_reports"]:
            path = directory / name
            if path.resolve().parent != directory.resolve():
                raise ValueError("report path escapes comparison directory")
            report = json.loads(path.read_text(encoding="utf-8"))
            if report["execution"]["profiled"]:
                raise ValueError("Profiled runs cannot enter quality comparisons")
            label, seconds, seed, case = expected[name]
            if (report["execution"]["seeds"] != [seed]
                    or report["execution"]["cases"] != [case]
                    or report["execution"]["seconds_per_measured_run"] != seconds
                    or len(report["scenarios"]) != 1
                    or report["scenarios"][0]["scenario_id"] != case):
                raise ValueError(f"Mismatched matrix cell: {name}")
            for support, digest in report["supporting_source_sha256"].items():
                if hashlib.sha256((ROOT / support).read_bytes()).hexdigest() != digest:
                    raise ValueError(f"Changed supporting source: {support}")
            if supporting_sources is not None and supporting_sources != report["supporting_source_sha256"]:
                raise ValueError("Mixed supporting source identity")
            supporting_sources = report["supporting_source_sha256"]
            if hashlib.sha256((ROOT / "scripts/benchmark_ga.py").read_bytes()).hexdigest() != report["harness_sha256"]:
                raise ValueError("Changed benchmark harness")
            source = Path(report["solver_source_snapshot"])
            if hashlib.sha256(source.read_bytes()).hexdigest() != report["solver_source_sha256"]:
                raise ValueError("Changed archived solver source")
            solver_support = report.get("solver_support_sha256", {})
            for support, digest in solver_support.items():
                if support != "neighborhood.py" or hashlib.sha256((source.parent / support).read_bytes()).hexdigest() != digest:
                    raise ValueError("Changed archived solver support")
            identity = (report["solver_source_sha256"], report["harness_sha256"], report["environment"]["python"], report["environment"]["platform"], tuple(sorted(solver_support.items())))
            if label in identities and identities[label] != identity:
                raise ValueError(f"Mixed implementation/environment identity for {label}")
            identities[label] = identity
            for scenario in report["scenarios"]:
                problem = ProblemInstance.from_dict(json.loads(Path(scenario["problem_snapshot"]).read_text(encoding="utf-8")))
                witness = tuple(Assignment.from_dict(item) for item in json.loads(Path(scenario["witness_snapshot"]).read_text(encoding="utf-8")))
                if not validate_schedule(problem, witness).feasible:
                    raise ValueError("Invalid synthetic feasible witness")
                if len(scenario["runs"]) != len(report["execution"]["seeds"]) or "summary" not in scenario:
                    raise ValueError("Incomplete observations")
                for row in scenario["runs"]:
                    result = SolverResult.from_dict(row["result"])
                    config = SolverConfig.from_dict(row["config"])
                    validated = validate_schedule(problem, result.assignments)
                    objective = score_schedule(problem, result.assignments) if result.assignments else None
                    if (validated != result.validation or objective != result.objective
                            or problem.canonical_hash != result.problem_hash
                            or problem.canonical_hash != scenario["problem_hash"]
                            or config.canonical_hash != result.config_hash
                            or config.canonical_hash != row["config_hash"]
                            or config.seed != seed or result.seed != seed or row["seed"] != seed
                            or config.time_limit_seconds != seconds
                            or row["feasible"] != validated.feasible
                            or row["hard_violations"] != validated.hard_violation_count
                            or row["raw_penalty"] != (objective.weighted_total if objective is not None else None)):
                        raise ValueError(f"Independent evidence mismatch: {name}")
                    if validated.feasible and (result.first_feasible_seconds is None
                                               or result.first_feasible_seconds > config.time_limit_seconds):
                        raise ValueError("Feasible observation outside deadline")
                    cell = (label, config.time_limit_seconds, result.seed, scenario["scenario_id"])
                    if cell in seen:
                        raise ValueError(f"Duplicate observation: {cell}")
                    seen.add(cell)
                    observations.append({
                        "variant": label, "scenario": scenario["scenario_id"], "events": len(problem.events),
                        "seconds": config.time_limit_seconds, "seed": result.seed,
                        "problem_hash": problem.canonical_hash, "config_hash": config.canonical_hash,
                        "feasible": validated.feasible, "hard_violations": validated.hard_violation_count,
                        "raw_penalty": objective.weighted_total if objective is not None else None,
                        "first_feasible_seconds": result.first_feasible_seconds,
                        "runtime_seconds": result.runtime_seconds, "wall_seconds": row["wall_seconds"],
                        "metrics": {key: value for key, value in result.metrics if key != "convergence_trace_json"},
                        "report": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
                        "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    })
    environments = {(identity[1], identity[2], identity[3]) for identity in identities.values()}
    if len(environments) > 1:
        raise ValueError("Mixed comparison harness/environment identity")
    # Every comparison cell must describe the same input and configuration.
    matches = {}
    for row in observations:
        key = (row["scenario"], row["seconds"], row["seed"])
        hashes = (row["problem_hash"], row["config_hash"])
        if key in matches and matches[key] != hashes:
            raise ValueError(f"Unmatched comparison inputs: {key}")
        matches[key] = hashes
    groups = defaultdict(list)
    for row in observations:
        groups[(row["variant"], row["scenario"], row["seconds"])].append(row)
    summaries = []
    for (variant, case, seconds), rows in sorted(groups.items()):
        feasible = [row for row in rows if row["feasible"]]
        summaries.append({
            "variant": variant, "scenario": case, "seconds": seconds,
            "runs": len(rows), "feasible_runs": len(feasible),
            "median_feasible_penalty": median(row["raw_penalty"] for row in feasible) if feasible else None,
            "median_first_feasible_seconds": median(row["first_feasible_seconds"] for row in feasible) if feasible else None,
            "median_final_hard_violations": median(row["hard_violations"] for row in rows),
        })
    baselines = {(row["scenario"], row["seconds"], row["seed"]): row
                 for row in observations if row["variant"] == baseline}
    paired_comparisons = []
    for (variant, case, seconds), rows in sorted(groups.items()):
        if variant == baseline:
            continue
        pairs = [(baselines[(case, seconds, row["seed"])], row) for row in rows
                 if (case, seconds, row["seed"]) in baselines]
        if not pairs:
            continue
        both_feasible = [(before, after) for before, after in pairs if before["feasible"] and after["feasible"]]
        penalty_deltas = [after["raw_penalty"] - before["raw_penalty"] for before, after in both_feasible]
        paired_comparisons.append({
            "baseline": baseline, "candidate": variant, "scenario": case, "seconds": seconds,
            "matched_runs": len(pairs), "both_feasible": len(both_feasible),
            "gained_feasibility": sum(not before["feasible"] and after["feasible"] for before, after in pairs),
            "lost_feasibility": sum(before["feasible"] and not after["feasible"] for before, after in pairs),
            "neither_feasible": sum(not before["feasible"] and not after["feasible"] for before, after in pairs),
            "lower_penalty_pairs": sum(delta < 0 for delta in penalty_deltas),
            "equal_penalty_pairs": sum(delta == 0 for delta in penalty_deltas),
            "higher_penalty_pairs": sum(delta > 0 for delta in penalty_deltas),
            "median_paired_penalty_change": median(penalty_deltas) if penalty_deltas else None,
        })
    return {
        "evidence_class": "diagnostic_not_formal_thesis_evidence",
        "primary_development_budget_seconds": 30,
        "observation_count": len(observations),
        "supporting_source_sha256": supporting_sources,
        "identities": {label: {"source_sha256": value[0], "harness_sha256": value[1], "python": value[2], "platform": value[3], "solver_support_sha256": dict(value[4])} for label, value in identities.items()},
        "summaries": summaries, "observations": observations, "paired_comparisons": paired_comparisons,
        "failed_observations": [
            {key: row[key] for key in ("variant", "scenario", "seconds", "seed", "hard_violations", "raw_penalty", "report")}
            for row in observations if not row["feasible"]
        ],
        "limitations": [
            "Synthetic development observations; no formal profiles frozen or CP-SAT comparison performed.",
            "Inspect each group's run count: the planned stage screen uses one seed and final validation uses five.",
            "Raw reports, witnesses and source snapshots are local ignored artifacts; preserve their directories.",
            "Phase timings overlap: initialization includes preparation/evaluation, repair includes validation/scoring.",
            "Windows deadline-clock values can be quantized. Profiling runs are excluded from these comparisons.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", default="ga-v4")
    args = parser.parse_args()
    result = summarize(args.directories, baseline=args.baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summaries"], indent=2))


if __name__ == "__main__":
    main()
