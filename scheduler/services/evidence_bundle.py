"""Deterministic, de-identified formal-study evidence bundles."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from statistics import median
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from scheduler import models
from scheduler.services.convergence import study_convergence
from scheduler.services.research_metrics import (
    ALGORITHMS,
    NO_FORMAL_CONCLUSION,
    analyze_experiment_study,
    trial_observations_from_study,
)

_ZIP_TIMESTAMP = (2000, 1, 1, 0, 0, 0)
_PROHIBITED_TERMS = (
    "chair",
    "seat_utilization",
    "seat utilization",
    "floor_space",
    "floor space",
    "room_capacity",
    "room capacity",
    "physical_dimension",
)


def build_study_evidence_bundle(study: models.ExperimentStudy) -> bytes:
    """Build the same ZIP bytes for the same immutable study evidence."""

    summary = analyze_experiment_study(study)
    observations = trial_observations_from_study(study)
    manifest = _public_study_manifest(study)
    instances = _instances_csv(study)
    trials = _trials_csv(observations)
    figures = _figures(summary, observations)
    convergence = study_convergence(study)
    figures.update({f"convergence-{section['scale']}.svg": section["svg"] for section in convergence})
    report = _report_html(manifest, summary, figures)
    files: dict[str, bytes] = {
        "README.md": _readme().encode("utf-8"),
        "instances.csv": instances,
        "report.html": report.encode("utf-8"),
        "study-manifest.json": _json_bytes(manifest),
        "summary.json": _json_bytes(summary),
        "trials.csv": trials,
        "convergence.csv": _csv_bytes(
            ["scale_percentage", "algorithm", "seed", "elapsed_seconds", "hard_violations", "raw_penalty"],
            [
                {"scale_percentage": section["scale"], "algorithm": trace["algorithm"], "seed": 9001, **point}
                for section in convergence for trace in section["traces"] for point in trace["points"]
            ],
        ),
        **{f"figures/{name}": value.encode("utf-8") for name, value in figures.items()},
    }
    _assert_deidentified(files)
    files["checksums.sha256"] = _checksum_file(files)

    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = ZipInfo(name, _ZIP_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])
    return output.getvalue()


def _public_study_manifest(study: models.ExperimentStudy) -> dict[str, Any]:
    instances = []
    source_instances = {
        row.get("planned_percentage"): row
        for row in study.protocol_manifest.get("instances", [])
        if isinstance(row, Mapping)
    }
    for batch in study.batches.order_by("planned_scale_percentage", "pk"):
        row = source_instances.get(batch.planned_scale_percentage, {})
        instances.append(
            {
                "planned_scale_percentage": batch.planned_scale_percentage,
                "actual_scale_percentage": batch.actual_scale_percentage,
                "snapshot_hash": batch.snapshot.snapshot_hash,
                "selection_hash": row.get("selection_hash"),
                "selected_offering_count": len(row.get("selected_offering_ids", ())),
                "locked_offering_count": row.get("locked_offering_count", 0),
                "retained_locked_offering_count": row.get(
                    "retained_locked_offering_count", 0
                ),
            }
        )
    return {
        "artifact_schema": "usm-thesis-evidence-v2",
        "study_id": study.pk,
        "mode": study.mode,
        "status": study.status,
        "protocol_version": study.protocol_version,
        "manifest_hash": study.manifest_hash,
        "source_snapshot_hash": study.source_snapshot.snapshot_hash,
        "constraint_manifest_hash": study.source_snapshot.constraint_manifest_hash,
        "objective_profile_hash": study.source_snapshot.objective_profile.profile_hash,
        "fixed_student_limit": 50,
        "scale_percentages": list(study.scale_percentages),
        "seeds": list(study.seeds),
        "order_seed": study.order_seed,
        "deadline_seconds": study.deadline_seconds,
        "cpu_limit": study.cpu_limit,
        "memory_limit_mb": study.memory_limit_mb,
        "warmups_per_algorithm_scale": study.warmups_per_algorithm_scale,
        "instances": instances,
        "expected_counts": study.protocol_manifest.get("expected_counts", {}),
        "protocol_integrity": study.protocol_integrity,
        "conclusion_boundary": (
            "Applies only to this de-identified authorized term, frozen build, policies, "
            "machine limits, and nested instances."
        ),
    }


def _instances_csv(study: models.ExperimentStudy) -> bytes:
    fields = [
        "planned_scale_percentage",
        "actual_scale_percentage",
        "snapshot_hash",
        "offerings",
        "meetings",
        "sections",
        "instructors",
        "rooms",
        "time_atoms",
        "locks",
        "candidate_total",
        "candidate_min",
        "candidate_median",
        "candidate_mean",
        "candidate_max",
        "required_meeting_atoms",
        "available_room_atoms",
        "candidate_domain_density",
        "room_time_demand_pressure",
        "availability_density",
        "headcount_min",
        "headcount_median",
        "headcount_mean",
        "headcount_max",
        "meetings_approaching_50",
    ]
    rows: list[dict[str, Any]] = []
    for batch in study.batches.select_related("snapshot").order_by(
        "planned_scale_percentage", "pk"
    ):
        characteristics = batch.snapshot.instance_characteristics or {}
        candidates = characteristics.get("candidates", {})
        headcounts = characteristics.get("section_headcounts", {})
        rows.append(
            {
                "planned_scale_percentage": batch.planned_scale_percentage,
                "actual_scale_percentage": batch.actual_scale_percentage,
                "snapshot_hash": batch.snapshot.snapshot_hash,
                "offerings": characteristics.get("offerings"),
                "meetings": characteristics.get("meetings"),
                "sections": characteristics.get("sections"),
                "instructors": characteristics.get("instructors"),
                "rooms": characteristics.get("rooms"),
                "time_atoms": characteristics.get("time_atoms"),
                "locks": characteristics.get("locks"),
                "candidate_total": candidates.get("total"),
                "candidate_min": candidates.get("min"),
                "candidate_median": candidates.get("median"),
                "candidate_mean": candidates.get("mean"),
                "candidate_max": candidates.get("max"),
                "required_meeting_atoms": characteristics.get("required_meeting_atoms"),
                "available_room_atoms": characteristics.get("available_room_atoms"),
                "candidate_domain_density": characteristics.get("candidate_domain_density"),
                "room_time_demand_pressure": characteristics.get(
                    "room_time_demand_pressure"
                ),
                "availability_density": characteristics.get("availability_density"),
                "headcount_min": headcounts.get("min"),
                "headcount_median": headcounts.get("median"),
                "headcount_mean": headcounts.get("mean"),
                "headcount_max": headcounts.get("max"),
                "meetings_approaching_50": characteristics.get(
                    "meetings_approaching_50"
                ),
            }
        )
    return _csv_bytes(fields, rows)


def _trials_csv(observations: Sequence[Any]) -> bytes:
    fields = [
        "scale_percentage",
        "seed",
        "algorithm",
        "purpose",
        "pair_attempt",
        "status",
        "eligible",
        "independently_feasible",
        "first_feasible_seconds",
        "execution_seconds",
        "shared_preprocessing_seconds",
        "independent_validation_seconds",
        "process_cpu_seconds",
        "peak_rss_mb",
        "stopping_reason",
        "placement_diversity_mean_hamming",
        "placement_diversity_peer_count",
        "placement_signature",
        "room_time_utilization",
        "occupied_room_atoms",
        "available_room_atoms",
        "solver_diagnostics_json",
        "raw_penalty",
        "penalty_per_meeting",
        "meeting_count",
        "faculty_preference_penalty",
        "section_gaps",
        "instructor_gaps",
        "daily_load_imbalance",
        "hard_violation_categories_json",
        "failure_category",
        "exclusion_code",
        "planned_order",
        "actual_order",
        "snapshot_hash",
        "rule_manifest_hash",
        "objective_profile_hash",
        "configuration_hash",
        "source_commit",
        "container_image",
        "dependency_versions_json",
    ]
    rows = []
    for item in sorted(
        observations,
        key=lambda value: (
            value.scale_percentage,
            value.purpose,
            value.seed,
            value.algorithm,
            value.pair_attempt,
        ),
    ):
        metadata = dict(item.metadata)
        components = dict(item.objective_components)
        rows.append(
            {
                "scale_percentage": item.scale_percentage,
                "seed": item.seed,
                "algorithm": item.algorithm,
                "purpose": item.purpose,
                "pair_attempt": item.pair_attempt,
                "status": item.status,
                "eligible": item.eligible,
                "independently_feasible": item.independently_feasible,
                "first_feasible_seconds": item.first_feasible_seconds,
                "execution_seconds": metadata.get("execution_seconds"),
                "shared_preprocessing_seconds": metadata.get("shared_preprocessing_seconds"),
                "independent_validation_seconds": metadata.get("independent_validation_seconds"),
                "process_cpu_seconds": metadata.get("process_cpu_seconds"),
                "peak_rss_mb": metadata.get("peak_rss_mb"),
                "stopping_reason": metadata.get("stopping_reason"),
                "placement_diversity_mean_hamming": metadata.get("placement_diversity_mean_hamming"),
                "placement_diversity_peer_count": metadata.get("placement_diversity_peer_count"),
                "placement_signature": metadata.get("placement_signature"),
                "room_time_utilization": metadata.get("room_time_utilization"),
                "occupied_room_atoms": metadata.get("occupied_room_atoms"),
                "available_room_atoms": metadata.get("available_room_atoms"),
                "solver_diagnostics_json": _compact_json(metadata.get("solver_diagnostics", {})),
                "raw_penalty": item.raw_penalty,
                "penalty_per_meeting": item.penalty_per_meeting,
                "meeting_count": item.meeting_count,
                "faculty_preference_penalty": components.get(
                    "faculty_preference_penalty"
                ),
                "section_gaps": components.get("section_gaps"),
                "instructor_gaps": components.get("instructor_gaps"),
                "daily_load_imbalance": components.get("daily_load_imbalance"),
                "hard_violation_categories_json": _compact_json(
                    dict(item.hard_violation_categories)
                ),
                "failure_category": item.failure_category,
                "exclusion_code": _exclusion_code(item),
                "planned_order": metadata.get("planned_order"),
                "actual_order": metadata.get("actual_order"),
                "snapshot_hash": metadata.get("snapshot_hash"),
                "rule_manifest_hash": metadata.get("rule_manifest_hash"),
                "objective_profile_hash": metadata.get("objective_profile_hash"),
                "configuration_hash": metadata.get("configuration_hash"),
                "source_commit": metadata.get("source_commit"),
                "container_image": metadata.get("container_image"),
                "dependency_versions_json": _compact_json(
                    metadata.get("dependency_versions", {})
                ),
            }
        )
    return _csv_bytes(fields, rows)


def _exclusion_code(item: Any) -> str:
    if item.eligible:
        return ""
    if item.failure_category:
        return item.failure_category
    if item.purpose in {"TUNING", "WARMUP", "DIAGNOSTIC"}:
        return f"PROTOCOL_EXCLUDED_{item.purpose}"
    if item.pair_attempt == 1 and item.exclusion_reason:
        return "SUPERSEDED_PAIR_ATTEMPT"
    return "NOT_ELIGIBLE"


def _figures(summary: Mapping[str, Any], observations: Sequence[Any]) -> dict[str, str]:
    return {
        "feasibility.svg": _feasibility_svg(summary),
        "time-to-feasibility.svg": _km_svg(summary),
        "feasible-penalty.svg": _penalty_svg(summary),
        "execution-time.svg": _execution_svg(observations),
        "objective-components.svg": _components_svg(summary),
    }


def _svg_frame(title: str, content: str, description: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="420" '
        'viewBox="0 0 900 420" role="img">'
        f"<title>{html.escape(title)}</title><desc>{html.escape(description)}</desc>"
        '<rect width="900" height="420" fill="#fffdf7"/>'
        '<style>text{font-family:Arial,sans-serif;fill:#173b2c}.t{font-size:22px;font-weight:700}'
        '.l{font-size:13px}.g{stroke:#d8dfd9;stroke-width:1}.cp{stroke:#08664a;fill:#08664a}'
        '.ga{stroke:#b37a00;fill:#b37a00}.dash{stroke-dasharray:8 6}.axis{stroke:#355c4b;stroke-width:1.5}'
        '</style>'
        f'<text class="t" x="36" y="34">{html.escape(title)}</text>{content}</svg>'
    )


def _feasibility_svg(summary: Mapping[str, Any]) -> str:
    points: dict[str, list[tuple[float, float, float | None, float | None]]] = {
        algorithm: [] for algorithm in ALGORITHMS
    }
    for index, scale in enumerate((25, 50, 75, 100)):
        outcome = _primary(summary, scale, "feasibility")
        for algorithm, offset in (("CP_SAT", -12), ("GA", 12)):
            row = outcome.get("by_algorithm", {}).get(algorithm, {})
            if not row.get("eligible_trials"):
                continue
            interval = row.get("wilson_95")
            low = high = None
            if isinstance(interval, list) and len(interval) == 2:
                low = 350 - 2.8 * 100 * float(interval[0])
                high = 350 - 2.8 * 100 * float(interval[1])
            points[algorithm].append(
                (
                    120 + index * 220 + offset,
                    350 - 2.8 * float(row.get("percentage") or 0),
                    low,
                    high,
                )
            )
    content = _axes_percent()
    for algorithm, css in (("CP_SAT", "cp"), ("GA", "ga dash")):
        if not points[algorithm]:
            continue
        coords = " ".join(f"{x:.1f},{y:.1f}" for x, y, _low, _high in points[algorithm])
        content += f'<polyline class="{css}" fill="none" stroke-width="3" points="{coords}"/>'
        for x, y, low, high in points[algorithm]:
            if low is not None and high is not None:
                content += (
                    f'<line class="{css}" x1="{x}" y1="{high:.1f}" '
                    f'x2="{x}" y2="{low:.1f}" stroke-width="2"/>'
                    f'<line class="{css}" x1="{x-5}" y1="{high:.1f}" '
                    f'x2="{x+5}" y2="{high:.1f}" stroke-width="2"/>'
                    f'<line class="{css}" x1="{x-5}" y1="{low:.1f}" '
                    f'x2="{x+5}" y2="{low:.1f}" stroke-width="2"/>'
                )
            content += f'<circle class="{css.split()[0]}" cx="{x}" cy="{y}" r="5"/>'
        last_x, last_y, _low, _high = points[algorithm][-1]
        label = "CP-SAT" if algorithm == "CP_SAT" else "GA"
        content += (
            f'<text class="l" x="{last_x + 9}" y="{last_y - 8:.1f}">'
            f'{label}</text>'
        )
    return _svg_frame(
        "Independent feasibility across demand scales",
        content,
        "Feasibility percentages and Wilson 95 percent intervals for CP-SAT and GA at "
        "25, 50, 75, and 100 percent demand.",
    )


def _axes_percent() -> str:
    content = '<line class="axis" x1="80" y1="70" x2="80" y2="350"/><line class="axis" x1="80" y1="350" x2="830" y2="350"/>'
    for value in (0, 25, 50, 75, 100):
        y = 350 - 2.8 * value
        content += f'<line class="g" x1="80" y1="{y}" x2="830" y2="{y}"/><text class="l" x="42" y="{y + 4}">{value}%</text>'
    for index, scale in enumerate((25, 50, 75, 100)):
        content += f'<text class="l" x="{105 + index * 220}" y="375">{scale}% demand</text>'
    return content


def _km_svg(summary: Mapping[str, Any]) -> str:
    outcome = _primary(summary, 100, "time_to_feasibility")
    deadline = float(outcome.get("deadline_seconds") or 300)
    content = '<line class="axis" x1="80" y1="70" x2="80" y2="350"/><line class="axis" x1="80" y1="350" x2="830" y2="350"/>'
    for algorithm, css in (("CP_SAT", "cp"), ("GA", "ga dash")):
        rows = outcome.get("by_algorithm", {}).get(algorithm, {}).get("kaplan_meier", [])
        points = [
            (
                80 + 750 * float(row.get("time_seconds", 0)) / max(1, deadline),
                350 - 280 * float(row.get("survival_probability", 1)),
            )
            for row in rows
        ]
        if points:
            first_x, first_y = points[0]
            path = [f"M {first_x:.1f} {first_y:.1f}"]
            for x, y in points[1:]:
                path.extend((f"H {x:.1f}", f"V {y:.1f}"))
            content += (
                f'<path class="{css}" fill="none" stroke-width="3" '
                f'd="{" ".join(path)}"/>'
            )
            last_x, last_y = points[-1]
            label = "CP-SAT" if algorithm == "CP_SAT" else "GA"
            content += (
                f'<text class="l" x="{min(last_x + 8, 842):.1f}" '
                f'y="{max(last_y - 7, 76):.1f}">{label}</text>'
            )
    content += (
        f'<text class="l" x="760" y="375">{deadline:g}s</text>'
        '<text class="l" x="95" y="62">Probability not yet feasible</text>'
    )
    return _svg_frame(
        "Kaplan–Meier time to first feasible schedule — 100% instance",
        content,
        "Right-censored time-to-feasibility curves at the complete demand instance.",
    )


def _penalty_svg(summary: Mapping[str, Any]) -> str:
    outcome = _primary(summary, 100, "schedule_quality")
    values = {
        algorithm: outcome.get("by_algorithm", {})
        .get(algorithm, {})
        .get("raw_weighted_soft_penalty", {})
        .get("values", [])
        for algorithm in ALGORITHMS
    }
    maximum = max(1.0, max((float(value) for rows in values.values() for value in rows), default=1.0))
    content = '<line class="axis" x1="100" y1="350" x2="830" y2="350"/>'
    for index, (algorithm, css) in enumerate((("CP_SAT", "cp"), ("GA", "ga"))):
        x = 290 + index * 320
        rows = sorted(float(value) for value in values[algorithm])
        if rows:
            q1, med, q3 = _quartiles(rows)
            y1, ym, y3 = (350 - 260 * value / maximum for value in (q1, med, q3))
            content += f'<rect x="{x - 45}" y="{y3}" width="90" height="{max(1, y1-y3)}" fill="none" class="{css}" stroke-width="3"/>'
            content += f'<line x1="{x-45}" y1="{ym}" x2="{x+45}" y2="{ym}" class="{css}" stroke-width="3"/>'
            for point_index, value in enumerate(rows):
                jitter = ((point_index * 17) % 61) - 30
                y = 350 - 260 * value / maximum
                content += f'<circle cx="{x+jitter}" cy="{y}" r="3" class="{css}" opacity=".55"/>'
        content += f'<text class="l" x="{x-25}" y="378">{algorithm}</text>'
    return _svg_frame(
        "Feasible raw soft penalties — 100% instance",
        content,
        "Box-and-point distributions include independently feasible trials only; lower is better.",
    )


def _execution_svg(observations: Sequence[Any]) -> str:
    values = {
        algorithm: [
            float(item.metadata["execution_seconds"])
            for item in observations
            if item.algorithm == algorithm
            and item.purpose == "MEASURED"
            and item.eligible
            and item.metadata.get("execution_seconds") is not None
        ]
        for algorithm in ALGORITHMS
    }
    maximum = max(1.0, max((value for rows in values.values() for value in rows), default=1.0))
    content = '<line class="axis" x1="100" y1="350" x2="830" y2="350"/>'
    for index, (algorithm, css) in enumerate((("CP_SAT", "cp"), ("GA", "ga"))):
        x = 290 + index * 320
        for point_index, value in enumerate(sorted(values[algorithm])):
            jitter = ((point_index * 19) % 121) - 60
            y = 350 - 260 * value / maximum
            content += f'<circle cx="{x+jitter}" cy="{y}" r="3" class="{css}" opacity=".55"/>'
        label = median(values[algorithm]) if values[algorithm] else None
        content += f'<text class="l" x="{x-70}" y="378">{algorithm} median: {label if label is not None else "pending"}</text>'
    return _svg_frame(
        "Secondary execution-time distributions",
        content,
        "Measured execution times by algorithm. This is secondary evidence, not success-only time to feasibility.",
    )


def _components_svg(summary: Mapping[str, Any]) -> str:
    outcome = _primary(summary, 100, "schedule_quality")
    names = (
        "faculty_preference_penalty",
        "section_gaps",
        "instructor_gaps",
        "daily_load_imbalance",
    )
    medians = {
        algorithm: [
            outcome.get("by_algorithm", {})
            .get(algorithm, {})
            .get("objective_components", {})
            .get(name, {})
            .get("median")
            for name in names
        ]
        for algorithm in ALGORITHMS
    }
    maximum = max(1.0, max(
        (
            float(value)
            for rows in medians.values()
            for value in rows
            if value is not None
        ),
        default=1.0,
    ))
    content = '<line class="axis" x1="80" y1="350" x2="840" y2="350"/>'
    for index, name in enumerate(names):
        x = 140 + index * 190
        for offset, (algorithm, css) in zip((-18, 18), (("CP_SAT", "cp"), ("GA", "ga")), strict=True):
            value = medians[algorithm][index]
            height = 0 if value is None else 230 * float(value) / maximum
            content += f'<rect x="{x+offset-14}" y="{350-height}" width="28" height="{height}" class="{css}"/>'
        content += f'<text class="l" x="{x-70}" y="378">{html.escape(name.replace("_", " "))}</text>'
    return _svg_frame(
        "Objective-component medians — 100% instance",
        content,
        "Small-multiple component comparison for independently feasible schedules.",
    )


def _primary(summary: Mapping[str, Any], scale: int, outcome: str) -> Mapping[str, Any]:
    return (
        summary.get("scales", {})
        .get(str(scale), {})
        .get("primary_outcomes", {})
        .get(outcome, {})
    )


def _report_html(
    manifest: Mapping[str, Any],
    summary: Mapping[str, Any],
    figures: Mapping[str, str],
) -> str:
    conclusion = str(summary.get("formal_conclusion") or NO_FORMAL_CONCLUSION)
    status = summary.get("integrity", {})
    cards = []
    for scale in (25, 50, 75, 100):
        data = summary.get("scales", {}).get(str(scale), {})
        eligible = data.get("eligible_by_algorithm", {})
        cards.append(
            f'<article><h3>{scale}% demand</h3><p>Eligible: CP-SAT {eligible.get("CP_SAT", 0)} · GA {eligible.get("GA", 0)}</p></article>'
        )
    figure_html = "".join(
        f'<section class="figure">{svg}</section>' for _name, svg in sorted(figures.items())
    )
    evidence_rows = []
    for scale in (25, 50, 75, 100):
        for algorithm in ALGORITHMS:
            feasible = _primary(summary, scale, "feasibility").get("by_algorithm", {}).get(algorithm, {})
            quality = _primary(summary, scale, "schedule_quality").get("by_algorithm", {}).get(algorithm, {})
            timing = _primary(summary, scale, "time_to_feasibility").get("by_algorithm", {}).get(algorithm, {})
            interval = feasible.get("wilson_95")
            cells = (
                f'{feasible.get("independently_feasible", 0)} / {feasible.get("eligible_trials", 0)}',
                f"{interval[0]:.3f}–{interval[1]:.3f}" if interval else "Not estimated",
                quality.get("raw_weighted_soft_penalty", {}).get("median"),
                quality.get("penalty_per_meeting", {}).get("median"),
                timing.get("rmst_seconds"),
                timing.get("right_censored_trials", 0),
            )
            evidence_rows.append(
                f'<tr><th scope="row">{scale}% · {algorithm}</th>'
                + ''.join(f'<td>{html.escape(str(value)) if value is not None else "Not estimated"}</td>' for value in cells)
                + '</tr>'
            )
    evidence_table = (
        '<div class="table-scroll" tabindex="0" role="region" aria-label="Primary outcomes table">'
        '<table><caption>Primary evidence by algorithm and demand level</caption><thead><tr>'
        '<th scope="col">Demand / algorithm</th><th scope="col">Feasible / eligible</th>'
        '<th scope="col">Wilson 95%</th><th scope="col">Median raw penalty</th>'
        '<th scope="col">Penalty / meeting</th><th scope="col">RMST seconds</th>'
        '<th scope="col">Censored trials</th></tr></thead><tbody>'
        + ''.join(evidence_rows) + '</tbody></table></div>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>USM Scheduler formal study evidence</title>
<style>
:root{{--green:#073a2a;--gold:#b37a00;--paper:#fffdf7;--ink:#173b2c}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 Arial,sans-serif}}main{{max-width:1120px;margin:auto;padding:32px}}header{{border-bottom:4px solid var(--gold);padding-bottom:20px}}h1,h2,h3{{color:var(--green)}}.verdict{{padding:18px;border-left:6px solid var(--gold);background:#f6f1df;font-size:1.1rem}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}article{{border:1px solid #cad5ce;padding:12px;background:white}}.figure{{overflow-x:auto;margin:28px 0}}svg{{max-width:100%;height:auto}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #cad5ce;padding:8px;text-align:left}}@media(max-width:720px){{.cards{{grid-template-columns:1fr 1fr}}main{{padding:18px}}}}@media(max-width:390px){{.cards{{grid-template-columns:1fr}}}}@media print{{body{{background:white}}main{{max-width:none;padding:0}}.figure{{break-inside:avoid}}}}
.table-scroll{{overflow-x:auto}}.table-scroll:focus-visible{{outline:3px solid var(--gold);outline-offset:4px}}header p{{overflow-wrap:anywhere}}caption{{text-align:left;font-weight:bold;padding:10px 0}}@media print{{.table-scroll{{overflow:visible}}table{{font-size:9pt}}thead{{display:table-header-group}}}}
</style></head><body><main><header><p>USM Scheduler · thesis experimental platform v2</p><h1>CP-SAT versus Genetic Algorithm</h1><p>Manifest {html.escape(str(manifest.get("manifest_hash", "")))}</p></header>
<h2>Formal conclusion</h2><p class="verdict">{html.escape(conclusion)}</p>
<h2>Protocol integrity</h2><table><tbody><tr><th>Mode</th><td>{html.escape(str(manifest.get("mode")))}</td></tr><tr><th>Status</th><td>{html.escape(str(manifest.get("status")))}</td></tr><tr><th>Matrix complete</th><td>{html.escape(str(status.get("matrix_complete", False)))}</td></tr><tr><th>Protocol valid</th><td>{html.escape(str(status.get("effective_protocol_valid", False)))}</td></tr><tr><th>Fixed student limit</th><td>50 for every section and combined meeting</td></tr></tbody></table>
<h2>Demand instances</h2><div class="cards">{''.join(cards)}</div>
<h2>Primary evidence</h2>{evidence_table}
<p>Initialization and shared validation/scoring count toward the 300-second deadline. Late incumbents do not enter feasibility or quality results. Incomplete studies have no formal conclusion.</p>
<h2>Figures</h2>{figure_html}
<h2>Interpretation boundary</h2><p>Results apply only to the frozen authorized term, policies, objective, implementation, machine limits, and nested instances recorded in this bundle. Room-time utilization, where reported, means occupied scheduling periods only.</p>
</main></body></html>"""


def _readme() -> str:
    return """# USM Scheduler thesis evidence bundle

This deterministic bundle contains de-identified protocol, instance, trial, summary, report, and SVG evidence for a CP-SAT versus Genetic Algorithm study.

## Primary metrics

1. Independent feasibility: independently feasible eligible trials found and scored within the solver deadline divided by all eligible trials, with Wilson 95% intervals. Initialization and shared validation/scoring count toward the deadline; infrastructure grace cannot improve a schedule.
2. Schedule quality: raw weighted soft penalty among independently feasible schedules, with penalty per meeting, component medians, bootstrap intervals, A12, and the preregistered permutation result.
3. Time to feasibility: all eligible trials, with unsuccessful trials right-censored at 300 seconds, Kaplan-Meier coordinates, and RMST.

Holm adjustment is applied across exactly these three comparisons within each demand scale. The normalized 0-100 score is secondary.

## Secondary metrics

The trial table includes shared snapshot preprocessing, independent validation, execution and CPU seconds, peak RSS, and the stopping reason. Missing measurements remain blank, not zero.

Placement diversity is the mean normalized Hamming distance to other eligible, independently feasible schedules from the same algorithm and frozen scale instance. The peer count is reported; a singleton has no estimate. Placement signatures are one-way hashes, not raw assignments.

Room-time utilization is unique occupied room-periods divided by all available room-periods in the frozen resource pool. It is reported only for independently feasible complete assignments with frozen room-availability evidence. It measures scheduling periods only.

## Exclusions

Tuning, warm-up, and diagnostic runs are excluded by design. Audited infrastructure failures require one complete paired replacement; original attempts remain preserved but excluded. User cancellations and unclassified failures invalidate formal inference. Algorithm observations stay in the denominator.

The convergence CSV and four separate convergence SVGs contain only excluded seed 9001 diagnostic traces. Each trace records improving validated/scored incumbents, with at most 512 points (deterministically thinned when necessary). Feasible penalty and hard violations use separate panels. Missing traces are missing evidence, never zero penalty. Trace logging is disabled for measured trials.

The bundle omits uploaded workbooks, user names, comments, direct identifiers, unpublished offering identifiers, host/process identities, and physical-room attributes. Room-time utilization means occupied scheduling periods only.
"""


def _csv_bytes(fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _checksum_file(files: Mapping[str, bytes]) -> bytes:
    rows = [f"{hashlib.sha256(files[name]).hexdigest()}  {name}" for name in sorted(files)]
    return ("\n".join(rows) + "\n").encode("ascii")


def _quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    lower = ordered[:midpoint]
    upper = ordered[midpoint + (len(ordered) % 2) :]
    return (
        float(median(lower or ordered)),
        float(median(ordered)),
        float(median(upper or ordered)),
    )


def _assert_deidentified(files: Mapping[str, bytes]) -> None:
    searchable = "\n".join(
        payload.decode("utf-8", errors="ignore").casefold() for payload in files.values()
    )
    for term in _PROHIBITED_TERMS:
        if term in searchable:
            raise ValueError(f"Evidence bundle contains prohibited physical-space field: {term}")
    if not all(math.isfinite(float(value)) for value in (50,)):
        raise AssertionError("fixed study constant must be finite")


__all__ = ["build_study_evidence_bundle"]
