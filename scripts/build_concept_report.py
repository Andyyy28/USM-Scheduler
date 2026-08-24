"""Build the canonical portable-report artifact from the concept paper."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

TITLE = (
    "A Comparative Evaluation of CP-SAT and Genetic Algorithm for University "
    "Timetabling and College-Boundary-Aware Room Assignment at the University "
    "of Southern Mindanao"
)

PLANNED_RUNS_SQL = """WITH scales(instance_scale_percent) AS (
    VALUES (25), (50), (75), (100)
)
SELECT
    instance_scale_percent,
    30 AS "CP-SAT runs",
    30 AS "GA runs"
FROM scales
ORDER BY instance_scale_percent"""

SOURCES = [
    {
        "id": "concept-paper-protocol",
        "label": "USM Scheduler repository — concept-paper research protocol",
        "href": "https://github.com/Andyyy28/USM-Scheduler/blob/main/docs/concept-paper.md",
    },
    {
        "id": "usm-about",
        "label": "University of Southern Mindanao — About USM",
        "href": "https://www.usm.edu.ph/about-usm/",
    },
    {
        "id": "usm-mandates",
        "label": "University of Southern Mindanao — Mandates, Vision and Mission",
        "href": "https://www.usm.edu.ph/about-usm/mandates-vision-mission/",
    },
    {
        "id": "usm-privacy",
        "label": "University of Southern Mindanao — University Data Protection Office",
        "href": "https://www.usm.edu.ph/administration/university-data-protection-office/",
    },
    {
        "id": "usm-research-ethics",
        "label": "University of Southern Mindanao — RDE Link Center",
        "href": "https://www.usm.edu.ph/rde-link-center/",
    },
    {
        "id": "usm-logo-governance",
        "label": "University of Southern Mindanao — Proper trademark and logo use",
        "href": "https://www.usm.edu.ph/usm-ipttbdo-convenes-consultation-meeting-on-proper-use-of-university-trademark-and-logo-2/",
    },
    {
        "id": "ortools-cp-sat",
        "label": "Google OR-Tools — CP-SAT Solver",
        "href": "https://developers.google.com/optimization/cp/cp_solver",
    },
    {
        "id": "itc-benchmarking",
        "label": "International Timetabling Competition 2007 — Benchmarking guidance",
        "href": "https://www.eeecs.qub.ac.uk/itc2007/index_files/benchmarking.htm",
    },
]


def _slug(value: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return compact[:64] or "section"


def _split_major_sections(markdown: str) -> list[tuple[str, str]]:
    lines = markdown.strip().splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title = "Title and research summary"
    current_lines: list[str] = []
    for line in lines:
        if line.startswith("## "):
            sections.append((current_title, current_lines))
            current_title = line[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    sections.append((current_title, current_lines))
    return [
        (heading, "\n".join(body).strip())
        for heading, body in sections
        if any(line.strip() for line in body)
    ]


def _planned_runs() -> list[dict[str, int]]:
    with sqlite3.connect(":memory:") as connection:
        cursor = connection.execute(PLANNED_RUNS_SQL)
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def build_artifact(markdown: str, generated_at: str) -> dict[str, object]:
    sources = json.loads(json.dumps(SOURCES))
    protocol_source = next(
        source for source in sources if source["id"] == "concept-paper-protocol"
    )
    protocol_source["query"] = {
        "engine": "SQLite",
        "sql": PLANNED_RUNS_SQL,
        "description": (
            "Materializes the prespecified 30 CP-SAT and 30 GA measured runs "
            "at each deterministic 25%, 50%, 75%, and 100% scaling instance."
        ),
        "executed_at": generated_at,
        "language": "sql",
        "filters": [
            "Protocol quantities only; excludes one unmeasured warm-up per solver.",
            "Does not contain or imply algorithm performance results.",
        ],
        "metric_definitions": [
            "Measured runs = 30 fixed seeds per algorithm per scaling instance."
        ],
    }
    blocks = []
    used_ids: set[str] = set()
    for heading, body in _split_major_sections(markdown):
        base_id = _slug(heading)
        block_id = base_id
        suffix = 2
        while block_id in used_ids:
            block_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(block_id)
        blocks.append(
            {
                "id": block_id,
                "type": "markdown",
                "body": body,
                "layout": "full",
            }
        )

        if heading == "Methodology":
            blocks.append(
                {
                    "id": "planned-runs-chart",
                    "type": "chart",
                    "chartId": "planned-measured-runs",
                    "layout": "full",
                }
            )

    return {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": TITLE,
            "description": (
                "Evidence-bounded BS Computer Science thesis concept paper for a "
                "Kabacan Main Campus scheduling decision-support prototype."
            ),
            "generatedAt": generated_at,
            "blocks": blocks,
            "charts": [
                {
                    "id": "planned-measured-runs",
                    "title": "Planned measured runs per scaling instance",
                    "subtitle": (
                        "Protocol quantities, not performance results: 30 fixed seeds "
                        "for each algorithm at every deterministic scale."
                    ),
                    "type": "bar",
                    "dataset": "planned_runs",
                    "encodings": {
                        "x": {
                            "field": "instance_scale_percent",
                            "type": "ordinal",
                            "label": "Offering subset (%)",
                        },
                        "y": {
                            "fields": ["CP-SAT runs", "GA runs"],
                            "type": "quantitative",
                            "label": "Measured runs",
                        },
                    },
                    "xAxisTitle": "Offering subset (% of authorized term)",
                    "yAxisTitle": "Measured runs",
                    "layout": "full",
                    "sourceId": "concept-paper-protocol",
                }
            ],
            "sources": sources,
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "research_design": [
                    {
                        "setting": "Kabacan Main Campus case study",
                        "real_terms": 1,
                        "algorithms": 2,
                        "measured_seeds_per_instance": 30,
                        "wall_clock_budget_seconds": 300,
                        "primary_decision_rule": "Feasibility before quality and time",
                    }
                ],
                "planned_runs": _planned_runs(),
            },
        },
        "sources": sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("docs/concept-paper.md"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/concept-paper-artifact.json"),
    )
    args = parser.parse_args()
    markdown = args.input.read_text(encoding="utf-8")
    expected_heading = f"# {TITLE}"
    if not markdown.startswith(expected_heading):
        raise SystemExit(f"The concept paper must begin with {expected_heading!r}.")
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    artifact = build_artifact(markdown, generated_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Created canonical concept-paper artifact: {args.output}")


if __name__ == "__main__":
    main()
