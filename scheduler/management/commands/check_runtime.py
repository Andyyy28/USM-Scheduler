"""Exercise real queued solvers in a disposable, initially empty database."""

import json
import socket
from io import StringIO
from time import monotonic, sleep

from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command

from scheduler import models
from scheduler.services.problem_builder import build_and_store_snapshot
from scheduler.services.runs import create_run, queue_run
from scheduler.services.workflow import validate_schedule_version


class Command(BaseCommand):
    help = "Seed an empty disposable database and verify both engines through a real Celery worker."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-empty-database", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm_empty_database"]:
            raise CommandError("Requires --confirm-empty-database; use a disposable database only.")
        if settings.CELERY_TASK_ALWAYS_EAGER:
            raise CommandError("Real queue verification requires CELERY_TASK_ALWAYS_EAGER=false.")
        if models.AcademicTerm.objects.exists() or models.User.objects.exists():
            raise CommandError("Database is not empty; no records were changed.")
        output = StringIO()
        call_command("seed_demo", stdout=output)
        ids = json.loads(output.getvalue().strip().splitlines()[-1])
        actor = models.User.objects.get(pk=ids["central_user_id"])
        snapshot, _ = build_and_store_snapshot(
            models.TermDatasetRevision.objects.get(pk=ids["revision_id"]),
            models.ObjectiveProfile.objects.get(pk=ids["objective_profile_id"]), actor,
        )
        results = []
        for algorithm in models.SolverAlgorithm.values:
            run = create_run(snapshot=snapshot, algorithm=algorithm, requested_by=actor,
                             seed=71, configuration={"time_limit_seconds": 3, "worker_count": 1},
                             included_in_analysis=False, exclusion_reason="Synthetic container acceptance")
            queue_run(run)
            deadline = monotonic() + 90
            while monotonic() < deadline:
                run.refresh_from_db()
                if run.is_terminal:
                    break
                sleep(0.5)
            if run.status not in {models.RunStatus.FEASIBLE, models.RunStatus.OPTIMAL}:
                raise CommandError(f"{algorithm}: {run.status}: {run.error_message or run.stopping_reason}")
            if run.host_identity == socket.gethostname():
                raise CommandError("Solver did not execute in the separate worker container.")
            validation = validate_schedule_version(run.schedule_version, actor=actor)
            if not validation.is_feasible or run.hard_violation_count:
                raise CommandError(f"{algorithm}: independent schedule validation failed.")
            results.append({"algorithm": algorithm, "run_id": run.pk, "status": run.status,
                            "worker_host": run.host_identity, "worker_process": run.process_identity,
                            "hard_violations": validation.hard_violation_count})
        self.stdout.write(json.dumps({"synthetic": True, "queued_results": results}, sort_keys=True))
