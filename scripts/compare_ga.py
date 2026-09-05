"""Resume-safe serial GA development comparisons; no database or formal profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_ga import HOLDOUT_IDS, SCENARIO_IDS, run_benchmark, solver_support  # noqa: E402


def compare(output: Path, sources: dict[str, Path], budgets: list[float], seeds: list[int], cases: list[str]):
    if not sources or not budgets or len(set(budgets)) != len(budgets) or any(
        not math.isfinite(seconds) or seconds <= 0 for seconds in budgets
    ):
        raise ValueError("sources and unique finite positive budgets are required")
    if not seeds or len(set(seeds)) != len(seeds) or any(type(seed) is not int or seed < 0 for seed in seeds):
        raise ValueError("seeds must be unique non-negative integers")
    if not cases or len(set(cases)) != len(cases) or set(cases) - set(SCENARIO_IDS + HOLDOUT_IDS):
        raise ValueError("cases must be unique known scenario IDs")
    output.mkdir(parents=True, exist_ok=True)
    harness_hash = hashlib.sha256((ROOT / "scripts/benchmark_ga.py").read_bytes()).hexdigest()
    completed = []
    for budget_index, seconds in enumerate(budgets):
        for seed in seeds:
            for case in cases:
                # Reverse execution order between budget/seed blocks to reduce
                # a consistent first/last implementation advantage.
                labels = list(sources)
                if (budget_index + seeds.index(seed)) % 2:
                    labels.reverse()
                for label in labels:
                    source = sources[label].resolve()
                    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                    support_hash = hashlib.sha256(solver_support(source).read_bytes()).hexdigest()
                    path = output / f"{label}-{seconds:g}s-{seed}-{case}.json"
                    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
                    if existing is not None:
                        support_matches = all(
                            hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
                            for name, digest in existing["supporting_source_sha256"].items()
                        )
                        if not (existing["solver_source_sha256"] == source_hash
                                and existing["harness_sha256"] == harness_hash and support_matches
                                and existing.get("solver_support_sha256", {}).get("neighborhood.py") == support_hash):
                            raise ValueError(f"Source mismatch in {path}; use a new output directory")
                        if not (existing["environment"]["python"] == sys.version
                                and existing["environment"]["platform"] == platform.platform()
                                and existing["execution"]["seeds"] == [seed]
                                and existing["execution"]["cases"] == [case]
                                and existing["execution"]["seconds_per_measured_run"] == seconds
                                and existing["execution"]["profiled"] is False):
                            raise ValueError(f"Execution mismatch in {path}; use a new output directory")
                        complete = (len(existing["scenarios"]) == 1
                                    and len(existing["scenarios"][0]["runs"]) == 1
                                    and "summary" in existing["scenarios"][0])
                        if not complete:
                            raise ValueError(f"Incomplete observation in {path}; preserve it and rerun in a new directory")
                        print(f"Retained complete observation: {path.name}", flush=True)
                    else:
                        print(f"Measuring {label}, {seconds:g}s, seed {seed}, {case}", flush=True)
                        run_benchmark(path, seconds=seconds, seeds=(seed,), solver_source=source, cases=(case,))
                    completed.append(path.name)
                    (output / "matrix.json").write_text(json.dumps({
                        "evidence_class": "diagnostic_not_formal_thesis_evidence",
                        "sources": {name: str(value.resolve()) for name, value in sources.items()},
                        "budgets": budgets, "seeds": seeds, "cases": cases,
                        "completed_reports": completed,
                        "expected_reports": len(sources) * len(budgets) * len(seeds) * len(cases),
                    }, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", action="append", required=True, help="Safe-label=trusted-genetic.py path")
    parser.add_argument("--budgets", nargs="+", type=float, default=[3, 10, 30])
    parser.add_argument("--seeds", nargs="+", type=int, default=[5001])
    parser.add_argument("--cases", nargs="+", choices=SCENARIO_IDS + HOLDOUT_IDS, default=list(SCENARIO_IDS))
    args = parser.parse_args()
    sources = {}
    for value in args.source:
        label, separator, path = value.partition("=")
        if not separator or not label or not all(char.isalnum() or char in "-_" for char in label):
            parser.error("--source must be label=path, with letters/digits/hyphens/underscores in the label")
        if label in sources:
            parser.error("source labels must be unique")
        sources[label] = Path(path)
    compare(args.output, sources, args.budgets, args.seeds, args.cases)


if __name__ == "__main__":
    main()
