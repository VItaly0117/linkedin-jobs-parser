from django.core.management.base import BaseCommand

from jobs.linkedin.client import LinkedInClient
from jobs.models import SearchQuery
from jobs.services import search_runner


class Command(BaseCommand):
    help = "Часть 1 (Search): собрать вакансии по активным поисковым запросам."

    def add_arguments(self, parser):
        parser.add_argument("--query-id", type=int, action="append", help="ID запроса (можно несколько раз)")
        parser.add_argument("--refresh", action="store_true", help="сбросить чекпоинты и пройти выдачу заново")
        parser.add_argument("--max-pages", type=int, help="не больше N страниц на срез (для проверки)")

    def handle(self, *args, **opts):
        queries = SearchQuery.objects.filter(is_active=True).order_by("id")
        if opts["query_id"]:
            queries = queries.filter(id__in=opts["query_id"])
        if not queries:
            self.stderr.write("Нет активных поисковых запросов — добавьте их в админке.")
            return
        client = LinkedInClient()
        try:
            for query in queries:
                if opts["refresh"]:
                    search_runner.reset_query(query)
                elif query.status == SearchQuery.Status.DONE:
                    self.stdout.write(f"{query}: уже собран (для повторного прохода: --refresh)")
                    continue
                search_runner.run_query(client, query, opts["max_pages"])
        except KeyboardInterrupt:
            self.stderr.write("Остановлено. Повторный запуск продолжит с сохранённых чекпоинтов.")
