from celery import shared_task

from scheduler.services.runs import execute_run


@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True)
def execute_schedule_run(self, run_id: int) -> int:
    run = execute_run(run_id)
    if run.experiment_batch_id:
        # Imported lazily to keep the service dependency graph acyclic.
        from scheduler.services.experiments import refresh_experiment_status

        refresh_experiment_status(run.experiment_batch)
    return run_id
