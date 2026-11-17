from django.core.management.base import BaseCommand

from jobs.services import details_runner


class Command(BaseCommand):
    help = "Часть 2 (Details): собрать полные данные по вакансиям и компаниям из БД."

    def add_arguments(self, parser):
        parser.add_argument("--workers", type=int, default=4, help="потоков на загрузку")
        parser.add_argument("--batch", type=int, default=20, help="размер пачки из очереди")
        parser.add_argument("--limit", type=int, help="обработать не больше N записей каждого типа")
        parser.add_argument("--retry-failed", action="store_true", help="повторить записи со статусом failed")
        parser.add_argument("--jobs-only", action="store_true")
        parser.add_argument("--companies-only", action="store_true")

    def handle(self, *args, **opts):
        try:
            result = details_runner.run(
                workers=opts["workers"],
                batch=opts["batch"],
                limit=opts["limit"],
                retry_failed=opts["retry_failed"],
                jobs=not opts["companies_only"],
                companies=not opts["jobs_only"],
            )
        except KeyboardInterrupt:
            self.stderr.write("Остановлено. Незавершённые записи вернутся в очередь при следующем запуске.")
            return
        self.stdout.write(f"Обработано: вакансий {result['jobs']}, компаний {result['companies']}")
