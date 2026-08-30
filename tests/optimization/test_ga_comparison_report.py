from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from scheduler.domain import (
    Assignment,
    SolverAlgorithm,
    SolverConfig,
    SolverResult,
    SolverStatus,
    score_schedule,
    validate_schedule,
)
from scripts import compare_ga, report_ga_comparison


@pytest.fixture
def saved_comparison(tmp_path, monkeypatch, balanced_problem):
    """A recorded result with real independent validation, without a timed run."""
    monkeypatch.setattr(report_ga_comparison, "ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    harness = tmp_path / "scripts/benchmark_ga.py"
    harness.write_text("# trusted test harness\n", encoding="utf-8")
    source = tmp_path / "genetic.py"
    source.write_text("# trusted test solver\n", encoding="utf-8")
    config = SolverConfig(algorithm=SolverAlgorithm.GENETIC_ALGORITHM, seed=5001, time_limit_seconds=30)
    assignments = tuple(Assignment(event.event_id, event.candidates[0].candidate_id) for event in balanced_problem.events)
    validation = validate_schedule(balanced_problem, assignments)
    objective = score_schedule(balanced_problem, assignments)
    result = SolverResult(
        algorithm=config.algorithm, status=SolverStatus.FEASIBLE, assignments=assignments,
        validation=validation, objective=objective, runtime_seconds=30, first_feasible_seconds=1,
        stopping_reason="test fixture", seed=config.seed, problem_hash=balanced_problem.canonical_hash,
        config_hash=config.canonical_hash,
    )
    problem_path, witness_path = tmp_path / "problem.json", tmp_path / "witness.json"
    problem_path.write_text(json.dumps(balanced_problem.to_dict()), encoding="utf-8")
    witness_path.write_text(json.dumps([item.to_dict() for item in assignments]), encoding="utf-8")
    report = {
        "execution": {"profiled": False, "seeds": [5001], "cases": ["moderate_mixed"], "seconds_per_measured_run": 30},
        "supporting_source_sha256": {}, "harness_sha256": hashlib.sha256(harness.read_bytes()).hexdigest(),
        "solver_source_snapshot": str(source), "solver_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "environment": {"python": sys.version, "platform": platform.platform()},
        "scenarios": [{
            "scenario_id": "moderate_mixed", "problem_snapshot": str(problem_path), "witness_snapshot": str(witness_path),
            "problem_hash": balanced_problem.canonical_hash, "summary": {},
            "runs": [{"result": result.to_dict(), "config": asdict(config), "config_hash": config.canonical_hash,
                      "seed": config.seed, "feasible": True, "hard_violations": 0, "raw_penalty": objective.weighted_total,
                      "wall_seconds": 30.01}],
        }],
    }
    directory = tmp_path / "comparison"
    directory.mkdir()
    name = "ga-v5-30s-5001-moderate_mixed.json"
    matrix = {"completed_reports": [name], "expected_reports": 1, "sources": {"ga-v5": str(source)},
              "budgets": [30], "seeds": [5001], "cases": ["moderate_mixed"]}
    (directory / "matrix.json").write_text(json.dumps(matrix), encoding="utf-8")
    (directory / name).write_text(json.dumps(report), encoding="utf-8")
    return directory, directory / name, report


def test_report_revalidates_results_and_rejects_duplicate_observations(saved_comparison):
    directory, _, report = saved_comparison
    summary = report_ga_comparison.summarize([directory])
    assert summary["summaries"][0]["feasible_runs"] == 1
    assert summary["observations"][0]["raw_penalty"] == report["scenarios"][0]["runs"][0]["raw_penalty"]
    assert summary["failed_observations"] == []
    with pytest.raises(ValueError, match="Duplicate observation"):
        report_ga_comparison.summarize([directory, directory])


def test_report_accepts_relative_cli_paths(saved_comparison, monkeypatch):
    directory, _, _ = saved_comparison
    monkeypatch.chdir(directory.parent)
    assert report_ga_comparison.summarize([Path("comparison")])["summaries"][0]["feasible_runs"] == 1


@pytest.mark.parametrize("tamper", ["score", "seed", "deadline", "witness", "source", "incomplete", "missing_cell", "profile"])
def test_report_refuses_inconsistent_or_incomplete_evidence(saved_comparison, tamper):
    directory, path, report = saved_comparison
    row = report["scenarios"][0]["runs"][0]
    if tamper == "score":
        row["raw_penalty"] = -1
    elif tamper == "seed":
        row["result"]["seed"] += 1
    elif tamper == "deadline":
        row["result"]["first_feasible_seconds"] = 31
    elif tamper == "witness":
        Path(report["scenarios"][0]["witness_snapshot"]).write_text("[]", encoding="utf-8")
    elif tamper == "source":
        report["solver_source_sha256"] = "not-the-recorded-hash"
    elif tamper == "incomplete":
        report["scenarios"][0]["runs"] = []
    elif tamper == "missing_cell":
        matrix_path = directory / "matrix.json"
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        matrix["seeds"].append(5002)
        matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    else:
        report["execution"]["profiled"] = True
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        report_ga_comparison.summarize([directory])


def test_comparison_resume_preserves_complete_cells_and_rejects_source_drift(saved_comparison, monkeypatch):
    directory, path, report = saved_comparison
    monkeypatch.setattr(compare_ga, "ROOT", directory.parent)

    def unexpected_run(*args, **kwargs):
        pytest.fail("A completed observation must not be overwritten or rerun")

    monkeypatch.setattr(compare_ga, "run_benchmark", unexpected_run)
    source = Path(report["solver_source_snapshot"])
    original = path.read_bytes()
    compare_ga.compare(directory, {"ga-v5": source}, [30], [5001], ["moderate_mixed"])
    assert path.read_bytes() == original
    source.write_text("# changed implementation\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Source mismatch"):
        compare_ga.compare(directory, {"ga-v5": source}, [30], [5001], ["moderate_mixed"])
    assert path.read_bytes() == original
