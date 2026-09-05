"""Reconcile schedule-run worker leases that have expired."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from scheduler.services.runs import reconcile_stale_runs, stale_run_ids


class Command(BaseCommand):
    help = (
        "Mark expired RUNNING schedule-run leases as FAILED/UNCLASSIFIED so "
        "worker loss cannot leave research trials stranded."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum stale leases to inspect in this bounded pass (default: 500).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the number of stale leases without changing evidence.",
        )

    def handle(self, *args, **options) -> None:
        limit = options["limit"]
        if limit <= 0:
            raise CommandError("--limit must be positive")
        if options["dry_run"]:
            run_ids = stale_run_ids(limit=limit)
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {len(run_ids)} stale schedule-run lease(s) would be reconciled."
                )
            )
            return

        run_ids = reconcile_stale_runs(limit=limit)
        self.stdout.write(self.style.SUCCESS(f"Reconciled {len(run_ids)} stale schedule-run lease(s)."))
