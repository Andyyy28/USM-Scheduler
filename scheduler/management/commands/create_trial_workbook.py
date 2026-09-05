"""Write the safe synthetic trial workbook to an explicit path."""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from scheduler import models
from scheduler.services.trial_data import (
    TRIAL_WORKBOOK_FILENAME,
    TrialPolicyConfigurationError,
    approved_trial_policy_hashes,
    build_trial_workbook_bytes,
)


class Command(BaseCommand):
    help = "Create the fictional USM Scheduler trial workbook for a guided local test."

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("--output", default=TRIAL_WORKBOOK_FILENAME)
        parser.add_argument("--term-id", type=int, required=True)
        parser.add_argument("--campus", default=None)

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        output = Path(options["output"]).expanduser().resolve()
        if output.suffix.lower() != ".xlsx":
            raise CommandError("The output path must end in .xlsx.")
        try:
            term = models.AcademicTerm.objects.get(pk=options["term_id"])
        except models.AcademicTerm.DoesNotExist as exc:
            raise CommandError(f"Academic term {options['term_id']} does not exist.") from exc
        try:
            policy_hashes = approved_trial_policy_hashes(term)
        except TrialPolicyConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        output.parent.mkdir(parents=True, exist_ok=True)
        content = build_trial_workbook_bytes(
            campus=options["campus"] or term.campus,
            **policy_hashes,
        )
        output.write_bytes(content)
        self.stdout.write(
            self.style.SUCCESS(
                f"Created synthetic trial workbook: {output} ({len(content):,} bytes)"
            )
        )
