"""Allowlisted diagnostic convergence evidence; never used for inference."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping

from scheduler import models


def parse_trace(raw: object, deadline: float) -> list[dict]:
    """Reject malformed/non-monotone traces rather than invent missing evidence."""
    try:
        points = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(points, list) or len(points) > 512:
        return []
    clean = []
    last_time = -1.0
    best = (math.inf, math.inf)
    for point in points:
        if not isinstance(point, Mapping):
            return []
        elapsed, hard, penalty = (point.get(key) for key in (
            "elapsed_seconds", "hard_violations", "raw_penalty",
        ))
        if (type(elapsed) not in {int, float} or not math.isfinite(elapsed)
                or not 0 <= elapsed <= deadline or elapsed < last_time or type(hard) is not int or hard < 0):
            return []
        if hard == 0 and (type(penalty) is not int or penalty < 0):
            return []
        if hard and penalty is not None:
            return []
        fitness = (hard, penalty if penalty is not None else math.inf)
        if fitness > best:
            return []
        best, last_time = fitness, elapsed
        clean.append({"elapsed_seconds": float(elapsed), "hard_violations": hard, "raw_penalty": penalty})
    return clean


def study_convergence(study: models.ExperimentStudy) -> list[dict]:
    sections = []
    for batch in study.batches.order_by("planned_scale_percentage", "pk"):
        traces = []
        for run in batch.runs.filter(
            purpose=models.RunPurpose.DIAGNOSTIC, seed=9001, included_in_analysis=False,
        ).order_by("algorithm", "pair_attempt", "pk"):
            if (not run.is_terminal or run.configuration.get("formal_run_kind") != "trace"
                    or run.configuration.get("diagnostic_trace") is not True):
                continue
            metrics = run.diagnostics.get("metrics", {})
            points = parse_trace(metrics.get("convergence_trace_json"), float(study.deadline_seconds))
            traces.append({"algorithm": run.algorithm, "status": run.status, "points": points})
        scale = int(batch.planned_scale_percentage or 100)
        sections.append({
            "scale": scale,
            "traces": traces,
            "point_count": sum(len(trace["points"]) for trace in traces),
            "svg": convergence_svg(scale, traces, float(study.deadline_seconds)),
        })
    return sections


def convergence_svg(scale: int, traces: list[dict], deadline: float) -> str:
    """Two aligned panels: feasible penalty and hard violations, numeric data only."""
    title = f"Excluded convergence at {int(scale)}% demand"
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 400" role="img" '
        f'aria-labelledby="trace-title-{int(scale)} trace-desc-{int(scale)}">',
        f'<title id="trace-title-{int(scale)}">{title}</title>',
        f'<desc id="trace-desc-{int(scale)}">Seed 9001. Top: best feasible raw penalty. '
        'Bottom: hard violations in the retained incumbent. CP-SAT solid green; GA dashed gold. '
        'Empty traces mean no recorded incumbent, not zero penalty.</desc>',
        '<rect width="800" height="400" fill="white"/>',
        '<g font-family="Arial,sans-serif" font-size="14" fill="#193f32">',
        f'<text x="72" y="24">{title}</text>',
        '<text x="505" y="24">CP-SAT — solid</text><text x="650" y="24">GA -- dashed</text>',
    ]
    for field, label, top, height in (
        ("raw_penalty", "Feasible raw penalty", 68, 120),
        ("hard_violations", "Hard violations", 244, 100),
    ):
        maximum = max(1, max((
            point[field] for trace in traces for point in trace["points"] if point[field] is not None
        ), default=0))
        parts.extend([
            f'<text x="72" y="{top - 10}">{label} · lower is better</text>',
            f'<path d="M72 {top}V{top + height}H750" fill="none" stroke="#9bad9f"/>',
            f'<text x="60" y="{top + 5}" text-anchor="end">{maximum}</text>',
            f'<text x="60" y="{top + height}" text-anchor="end">0</text>',
        ])
        has_points = False
        for trace in traces:
            points = [point for point in trace["points"] if point[field] is not None]
            if not points:
                continue
            has_points = True
            color = "#176044" if trace["algorithm"] == "CP_SAT" else "#916617"
            dash = "" if trace["algorithm"] == "CP_SAT" else ' stroke-dasharray="7 4"'
            path = []
            for index, point in enumerate(points):
                x = 72 + 678 * point["elapsed_seconds"] / deadline
                y = top + height * (1 - point[field] / maximum)
                path.append(f"M{x:.3f} {y:.3f}" if index == 0 else f"H{x:.3f}V{y:.3f}")
                parts.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="3" fill="{color}"/>')
            parts.append(f'<path d="{" ".join(path)}" fill="none" stroke="{color}" stroke-width="2"{dash}/>')
        if not has_points:
            parts.append(f'<text x="390" y="{top + height / 2}" text-anchor="middle">No recorded incumbent</text>')
    parts.extend([
        '<text x="72" y="372">0 s</text>',
        f'<text x="750" y="372" text-anchor="end">{deadline:g} s · elapsed search time</text>',
        '</g></svg>',
    ])
    return "".join(parts)
