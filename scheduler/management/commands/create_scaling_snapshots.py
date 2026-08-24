"""Plan or commit deterministic nested scaling snapshots."""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from scheduler import models
from scheduler.services.scaling import (
    DEFAULT_SCALING_SEED,
    create_scaling_snapshots,
    plan_scaling_snapshots,
)


class Command(BaseCommand):
    help = "Plan nested 25/50/75/100 scaling snapshots; writes require explicit --commit."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("snapshot_id", type=int)
        parser.add_argument("--seed", type=int, default=DEFAULT_SCALING_SEED)
        parser.add_argument("--commit", action="store_true")
        parser.add_argument("--user-id", type=int, help="Required with --commit.")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            snapshot = models.ProblemSnapshot.objects.select_related(
                "revision", "objective_profile"
            ).get(pk=options["snapshot_id"])
        except models.ProblemSnapshot.DoesNotExist as exc:
            raise CommandError(f"Problem snapshot {options['snapshot_id']} does not exist") from exc
        try:
            plan = plan_scaling_snapshots(snapshot, seed=options["seed"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        payload = {"committed": False, "plan": plan.to_dict()}
        if options["commit"]:
            if not options["user_id"]:
                raise CommandError("--user-id is required with --commit")
            try:
                user = models.User.objects.get(pk=options["user_id"])
            except models.User.DoesNotExist as exc:
                raise CommandError(f"User {options['user_id']} does not exist") from exc
            snapshots = create_scaling_snapshots(snapshot, user, seed=options["seed"])
            payload["committed"] = True
            payload["snapshots"] = {
                str(percentage): {
                    "id": scaled.pk,
                    "hash": scaled.snapshot_hash,
                    "event_count": scaled.event_count,
                    "is_source_snapshot": scaled.pk == snapshot.pk,
                }
                for percentage, scaled in sorted(snapshots.items())
            }
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
