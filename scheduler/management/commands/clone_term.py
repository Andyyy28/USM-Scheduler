from __future__ import annotations

import json
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from scheduler import models
from scheduler.services.term_cloning import clone_term_revision


def _date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option} must use YYYY-MM-DD format.") from exc


class Command(BaseCommand):
    help = "Clone a committed semester revision into a new editable DRAFT term."

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("source_revision_id", type=int)
        parser.add_argument("--academic-year", required=True)
        parser.add_argument("--semester", required=True, choices=models.Semester.values)
        parser.add_argument("--starts-on", required=True)
        parser.add_argument("--ends-on", required=True)
        parser.add_argument("--actor", required=True, help="Username of the central scheduler performing the clone.")
        parser.add_argument("--label", default=None)

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        try:
            source = models.TermDatasetRevision.objects.get(pk=options["source_revision_id"])
        except models.TermDatasetRevision.DoesNotExist as exc:
            raise CommandError("Source dataset revision does not exist.") from exc
        try:
            actor = models.User.objects.get(username=options["actor"], is_active=True)
        except models.User.DoesNotExist as exc:
            raise CommandError("The active actor username does not exist.") from exc
        try:
            revision = clone_term_revision(
                source,
                academic_year=options["academic_year"],
                semester=options["semester"],
                starts_on=_date(options["starts_on"], "--starts-on"),
                ends_on=_date(options["ends_on"], "--ends-on"),
                actor=actor,
                label=options["label"],
            )
        except (ValidationError, PermissionDenied, IntegrityError) as exc:
            raise CommandError(f"Term clone failed: {exc}") from exc

        payload = {
            "source_revision_id": source.pk,
            "term_id": revision.term_id,
            "revision_id": revision.pk,
            "academic_year": revision.term.academic_year,
            "semester": revision.term.semester,
            "status": revision.status,
            "sections": revision.sections.count(),
            "time_slots": revision.time_slots.count(),
            "offerings": revision.course_offerings.count(),
            "meetings": models.MeetingRequirement.objects.filter(offering__revision=revision).count(),
        }
        self.stdout.write(json.dumps(payload, sort_keys=True))
