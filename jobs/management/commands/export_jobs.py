from pathlib import Path

from django.core.management.base import BaseCommand

from jobs import export
from jobs.models import Job


class Command(BaseCommand):
    help = "Выгрузить вакансии вместе с данными компаний в CSV или XLSX."

    def add_arguments(self, parser):
        parser.add_argument("--format", choices=["csv", "xlsx"], default="csv")
        parser.add_argument("--out", help="путь к файлу (по умолчанию exports/jobs.<format>)")
        parser.add_argument("--only-done", action="store_true", help="только вакансии с собранными деталями")

    def handle(self, *args, **opts):
        qs = Job.objects.all()
        if opts["only_done"]:
            qs = qs.filter(details_status=Job.DetailsStatus.DONE)
        out = Path(opts["out"] or f"exports/jobs.{opts['format']}")
        out.parent.mkdir(parents=True, exist_ok=True)
        if opts["format"] == "csv":
            with out.open("w", newline="", encoding="utf-8-sig") as f:
                n = export.write_csv(qs, f)
        else:
            n = export.write_xlsx(qs, out)
        self.stdout.write(f"Выгружено {n} вакансий → {out}")
