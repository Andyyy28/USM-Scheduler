from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from scheduler.services.imports import SCHEMA_VERSION, build_import_template


class Command(BaseCommand):
    help = "Create the versioned USM Scheduler semester-import XLSX template."

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument(
            "output",
            nargs="?",
            default=f"usm_scheduler_import_template_v{SCHEMA_VERSION}.xlsx",
            help="Output XLSX path (defaults to the current directory).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite the output file when it already exists.",
        )

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        output = Path(options["output"]).expanduser().resolve()
        if output.exists() and not options["force"]:
            raise CommandError(f"Refusing to overwrite existing file: {output}. Use --force to replace it.")
        if not output.parent.exists():
            raise CommandError(f"Output directory does not exist: {output.parent}")
        output.write_bytes(build_import_template())
        self.stdout.write(self.style.SUCCESS(f"Created schema {SCHEMA_VERSION} template: {output}"))
