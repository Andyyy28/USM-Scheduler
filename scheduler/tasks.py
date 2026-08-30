from celery import shared_task

from scheduler.services.runs import (
    RunClaimBusy,
    execute_run,
    reconcile_stale_runs,
    refresh_run_containers,
)


@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True)
def execute_schedule_run(self, run_id: int) -> int:
    delivery = dict(getattr(self.request, "delivery_info", None) or {})
    task_context = {
        "task_id": str(getattr(self.request, "id", "") or ""),
        "hostname": str(getattr(self.request, "hostname", "") or ""),
        "retries": int(getattr(self.request, "retries", 0) or 0),
        "routing_key": str(delivery.get("routing_key") or ""),
        "redelivered": bool(delivery.get("redelivered", False)),
    }
    try:
        execute_run(run_id, task_context=task_context)
        return run_id
    except RunClaimBusy as exc:
        # A duplicate delivery must remain pending until the owning task
        # finishes or the lease reconciler records the lost-worker evidence.
        raise self.retry(
            exc=exc,
            countdown=exc.retry_after_seconds,
            max_retries=100,
        ) from exc
    finally:
        refresh_run_containers(run_id)


@shared_task(acks_late=True, reject_on_worker_lost=True)
def reconcile_stale_schedule_runs() -> int:
    """Periodic safety net for workers lost after claiming a solver run."""

    return len(reconcile_stale_runs())
