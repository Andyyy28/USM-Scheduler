from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command

from scheduler import models
from scheduler.management.commands import solver_tuning_grid
from tests.integration.test_experiments import _experiment_graph

pytestmark = pytest.mark.django_db


def test_tuning_queue_persists_excluded_runs_on_the_benchmark_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _experiment_graph("equal-budget-tuning")
    queued_ids: list[int] = []

    def capture_queue(run: models.ScheduleRun) -> models.ScheduleRun:
        queued_ids.append(run.pk)
        return run

    monkeypatch.setattr(solver_tuning_grid, "queue_run", capture_queue)
    output = StringIO()

    call_command(
        "solver_tuning_grid",
        graph["snapshot"].pk,
        mode="queue",
        user_id=graph["user"].pk,
        confirm_synthetic=True,
        stdout=output,
    )

    artifact = json.loads(output.getvalue())
    runs = list(
        models.ScheduleRun.objects.filter(pk__in=artifact["created_run_ids"]).order_by("pk")
    )
    assert len(runs) == len(queued_ids) == 60
    assert {run.purpose for run in runs} == {models.RunPurpose.TUNING}
    assert not any(run.included_in_analysis for run in runs)
    assert all("Excluded synthetic" in run.exclusion_reason for run in runs)
    assert {run.configuration["benchmark_queue"] for run in runs} == {"benchmark"}
