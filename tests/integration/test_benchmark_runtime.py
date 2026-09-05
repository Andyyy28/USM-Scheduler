from __future__ import annotations

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from scheduler import models
from scheduler.services import experiments, runs
from scheduler.services import workflow as workflow_services
from scheduler.services.workflow import cancel_run
from tests.integration.test_experiments import _experiment_graph

pytestmark = pytest.mark.django_db


def _batch(suffix: str) -> models.ExperimentBatch:
    graph = _experiment_graph(suffix)
    return experiments.create_experiment_batch(
        graph["snapshot"],
        graph["user"],
        seeds=(11, 12),
        time_limit=7,
        order_seed=29,
    )


def test_atomic_claim_records_order_lease_and_worker_provenance() -> None:
    batch = _batch("runtime-claim")
    first, second, *_ = experiments.ordered_experiment_runs(batch)

    claimed = runs._mark_started(
        first.pk,
        task_context={"task_id": "task-claim", "hostname": "test-worker"},
    )

    assert claimed.status == models.RunStatus.RUNNING
    assert claimed.claim_token is not None
    assert claimed.lease_expires_at > claimed.started_at
    assert claimed.actual_order == 1
    assert claimed.configuration_hash == runs.run_configuration_hash(
        algorithm=claimed.algorithm,
        seed=claimed.seed,
        configuration=claimed.configuration,
    )
    assert claimed.host_identity
    assert claimed.process_identity
    assert claimed.dependency_versions["Django"]
    assert claimed.worker_manifest["task"]["task_id"] == "task-claim"
    assert len(claimed.worker_manifest["manifest_hash"]) == 64

    with pytest.raises(runs.RunClaimBusy):
        runs._mark_started(first.pk)

    models.ScheduleRun.objects.filter(pk=first.pk).update(
        status=models.RunStatus.NO_SOLUTION,
        finished_at=timezone.now(),
        lease_expires_at=None,
    )
    second_claim = runs._mark_started(second.pk)
    assert second_claim.actual_order == 2


def test_stale_reconciliation_is_auditable_and_refreshes_batch() -> None:
    batch = _batch("runtime-stale")
    run = experiments.ordered_experiment_runs(batch)[0]
    claimed = runs._mark_started(run.pk)
    expired_at = timezone.now() - timedelta(seconds=1)
    models.ScheduleRun.objects.filter(pk=claimed.pk).update(
        lease_expires_at=expired_at,
        heartbeat_at=expired_at,
    )

    assert runs.stale_run_ids() == [claimed.pk]
    assert runs.reconcile_stale_runs() == [claimed.pk]

    claimed.refresh_from_db()
    batch.refresh_from_db()
    assert claimed.status == models.RunStatus.FAILED
    assert claimed.failure_category == models.FailureCategory.UNCLASSIFIED
    assert claimed.lease_expires_at is None
    assert "audited failure classification" in claimed.error_message
    # Other planned runs remain queued, so the batch remains active while the
    # stale observation itself is terminal and awaits audit.
    assert batch.status == models.ExperimentStatus.RUNNING
    audit = models.AuditLog.objects.get(action="run.lease_expired", entity_id=str(claimed.pk))
    assert audit.details["previous_claim_token"] == str(claimed.claim_token)


def test_queue_dispatch_is_idempotent_and_uses_deadline_plus_grace(monkeypatch) -> None:
    batch = _batch("runtime-dispatch")
    run = experiments.ordered_experiment_runs(batch)[0]
    calls: list[dict[str, object]] = []

    def fake_apply_async(*, args, **options):
        calls.append({"args": args, **options})
        return SimpleNamespace(id=options["task_id"])

    from scheduler import tasks

    monkeypatch.setattr(tasks.execute_schedule_run, "apply_async", fake_apply_async)

    queued = runs.queue_run(run)
    again = runs.queue_run(queued)

    assert again.task_id == str(run.dispatch_key)
    assert len(calls) == 1
    assert calls[0]["args"] == [run.pk]
    assert calls[0]["time_limit"] == 67


def test_failed_queue_publish_releases_dispatch_claim_for_retry(monkeypatch) -> None:
    batch = _batch("runtime-dispatch-failure")
    run = experiments.ordered_experiment_runs(batch)[0]

    from scheduler import tasks

    def fail_publish(*, args, **options):
        raise ConnectionError("synthetic broker outage")

    monkeypatch.setattr(tasks.execute_schedule_run, "apply_async", fail_publish)

    with pytest.raises(runs.RunDispatchError, match="worker could not be reached"):
        runs.queue_run(run)

    run.refresh_from_db()
    assert run.status == models.RunStatus.QUEUED
    assert run.task_id == ""


def test_unhandled_execution_records_process_evidence(monkeypatch) -> None:
    batch = _batch("runtime-process")
    run = experiments.ordered_experiment_runs(batch)[0]

    class BrokenSolver:
        def solve(self, problem, config):
            raise RuntimeError("synthetic solver crash")

    monkeypatch.setattr(runs, "load_problem", lambda snapshot: object())
    monkeypatch.setattr(runs, "_solver_for", lambda algorithm: BrokenSolver())

    with pytest.raises(RuntimeError, match="synthetic solver crash"):
        runs.execute_run(run.pk)

    run.refresh_from_db()
    assert run.status == models.RunStatus.FAILED
    assert run.failure_category == models.FailureCategory.UNCLASSIFIED
    assert run.process_cpu_seconds is not None and run.process_cpu_seconds >= 0
    assert run.peak_rss_mb is None or run.peak_rss_mb >= 0
    assert run.finished_at is not None
    assert run.lease_expires_at is None
    audit = models.AuditLog.objects.get(action="run.execution_failed", entity_id=str(run.pk))
    assert audit.details["error_type"] == "RuntimeError"
    assert audit.details["failure_category"] == models.FailureCategory.UNCLASSIFIED


@override_settings(CELERY_TASK_TIME_LIMIT=10)
def test_reconciliation_recovers_legacy_running_row_without_a_lease() -> None:
    batch = _batch("runtime-legacy-lease")
    run = experiments.ordered_experiment_runs(batch)[0]
    claimed = runs._mark_started(run.pk)
    old_heartbeat = timezone.now() - timedelta(seconds=11)
    models.ScheduleRun.objects.filter(pk=claimed.pk).update(
        lease_expires_at=None,
        heartbeat_at=old_heartbeat,
    )

    assert runs.stale_run_ids() == [claimed.pk]
    assert runs.reconcile_stale_runs() == [claimed.pk]
    claimed.refresh_from_db()
    assert claimed.status == models.RunStatus.FAILED
    assert claimed.failure_category == models.FailureCategory.UNCLASSIFIED


def test_reconcile_management_command_supports_dry_run() -> None:
    batch = _batch("runtime-command")
    run = experiments.ordered_experiment_runs(batch)[0]
    runs._mark_started(run.pk)
    models.ScheduleRun.objects.filter(pk=run.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    output = StringIO()

    call_command("reconcile_stale_runs", dry_run=True, stdout=output)

    assert "1 stale schedule-run lease" in output.getvalue()
    assert models.ScheduleRun.objects.get(pk=run.pk).status == models.RunStatus.RUNNING


def test_cancelling_a_queued_trial_refreshes_its_batch_lifecycle() -> None:
    batch = _batch("runtime-cancel-refresh")
    run = experiments.ordered_experiment_runs(batch)[0]
    models.ExperimentBatch.objects.filter(pk=batch.pk).update(
        status=models.ExperimentStatus.QUEUED
    )

    cancelled = cancel_run(run, run.requested_by)

    batch.refresh_from_db()
    assert cancelled.status == models.RunStatus.CANCELLED
    assert cancelled.lease_expires_at is None
    assert batch.status == models.ExperimentStatus.RUNNING


def test_cancellation_remains_durable_when_broker_revoke_fails(monkeypatch) -> None:
    batch = _batch("runtime-cancel-broker-down")
    run = experiments.ordered_experiment_runs(batch)[0]
    models.ScheduleRun.objects.filter(pk=run.pk).update(task_id="published-task")
    run.refresh_from_db()

    def fail_revoke(*args, **kwargs):
        raise ConnectionError("synthetic broker outage")

    monkeypatch.setattr(workflow_services.current_app.control, "revoke", fail_revoke)

    cancelled = cancel_run(run, run.requested_by)

    assert cancelled.status == models.RunStatus.CANCELLED
    assert cancelled.lease_expires_at is None
    audit = models.AuditLog.objects.get(action="run.cancelled", entity_id=str(run.pk))
    assert audit.details["task_revoke_error_type"] == "ConnectionError"
