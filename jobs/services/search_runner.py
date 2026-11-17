"""Часть 1 — Search: листает выдачу по срезам и складывает карточки в БД.

Ничего не знает о Details: только создаёт Job (details_status=pending),
заготовки Company и связи JobSearchHit.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from jobs.linkedin import search as li
from jobs.linkedin.client import BadRequest, LinkedInClient, LinkedInError
from jobs.models import Company, Job, JobSearchHit, SearchQuery, SearchSlice

log = logging.getLogger(__name__)

EMPTY_PAGE_RETRIES = 2  # пустая страница бывает случайной — перепроверяем


def _clip(value: str, limit: int) -> str:
    return (value or "")[:limit]


def save_cards(query: SearchQuery, cards: list) -> int:
    """Сохраняет страницу карточек. Возвращает число новых вакансий."""
    if not cards:
        return 0
    by_id = {c.job_id: c for c in cards}  # дубли внутри одной страницы
    with transaction.atomic():
        slugs = {c.company_slug for c in by_id.values() if c.company_slug}
        Company.objects.bulk_create(
            [
                Company(
                    linkedin_slug=_clip(c.company_slug, 255),
                    name=_clip(c.company_name, 500),
                    url=_clip(c.company_url, 500),
                    logo_url=c.logo_url,
                )
                for c in {c.company_slug: c for c in by_id.values() if c.company_slug}.values()
            ],
            ignore_conflicts=True,
        )
        companies = dict(
            Company.objects.filter(linkedin_slug__in=slugs).values_list("linkedin_slug", "id")
        )
        existing = set(
            Job.objects.filter(linkedin_job_id__in=by_id).values_list("linkedin_job_id", flat=True)
        )
        Job.objects.bulk_create(
            [
                Job(
                    linkedin_job_id=c.job_id,
                    title=_clip(c.title, 500),
                    company_id=companies.get(c.company_slug),
                    company_name_raw=_clip(c.company_name, 500),
                    location=_clip(c.location, 500),
                    url=_clip(c.url, 1000),
                    listed_at=c.listed_at,
                    posted_text=_clip(c.posted_text, 128),
                    salary_text=_clip(c.salary_text, 255),
                    benefits_text=_clip(c.benefits_text, 255),
                )
                for c in by_id.values()
                if c.job_id not in existing
            ],
            ignore_conflicts=True,  # на случай параллельного процесса Search
        )
        jobs = Job.objects.filter(linkedin_job_id__in=by_id)
        JobSearchHit.objects.bulk_create(
            [JobSearchHit(job_id=pk, query=query) for pk in jobs.values_list("id", flat=True)],
            ignore_conflicts=True,
        )
    return len(by_id) - len(existing)


def run_slice(client: LinkedInClient, query: SearchQuery, sl: SearchSlice, max_pages=None) -> None:
    """Листает один срез от sl.next_start до конца выдачи."""
    sl.status = SearchSlice.Status.RUNNING
    sl.save(update_fields=["status", "updated_at"])
    empty_streak = 0
    pages = 0
    while sl.next_start < li.RESULT_CAP and (max_pages is None or pages < max_pages):
        params = li.build_params(
            query.keywords,
            sl.location or query.country,
            sl.geo_id if sl.location else query.geo_id,
            sl.f_tpr,
            sl.next_start,
        )
        try:
            cards = li.parse_search(client.get(li.SEARCH_PATH, params=params))
        except BadRequest:
            break  # LinkedIn отвечает 400 за пределом пагинации
        if not cards:
            empty_streak += 1
            if empty_streak > EMPTY_PAGE_RETRIES:
                break
            continue
        empty_streak = 0
        pages += 1
        new = save_cards(query, cards)
        sl.next_start += len(cards)
        sl.jobs_seen += len(cards)
        sl.jobs_new += new
        sl.save(update_fields=["next_start", "jobs_seen", "jobs_new", "updated_at"])  # чекпоинт
        log.info("[%s] %s start=%d: +%d карточек, %d новых", query, sl, sl.next_start, len(cards), new)
    else:
        if max_pages is not None and pages >= max_pages:
            return  # остановлены лимитом страниц — срез остаётся в работе
    sl.capped = sl.next_start >= li.CAP_THRESHOLD
    sl.status = SearchSlice.Status.DONE
    sl.error = ""
    sl.save(update_fields=["capped", "status", "error", "updated_at"])


def spawn_children(client: LinkedInClient, sl: SearchSlice) -> int:
    """Дочерние срезы для среза, упёршегося в лимит выдачи.

    1. Тот же регион, но более узкое окно времени.
    2. Для корневого среза (вся страна, без окна) — ещё и срезы по регионам страны.
    Пересечения между срезами гасит уникальный ключ linkedin_job_id.
    """
    children = []
    window = li.next_time_window(sl.f_tpr)
    if window:
        children.append({"location": sl.location, "geo_id": sl.geo_id, "f_tpr": window})
    if not sl.location and not sl.f_tpr:
        try:
            regions = li.fetch_regions(client, sl.query.country)
        except (LinkedInError, ValueError) as e:
            log.error("[%s] не удалось получить регионы: %s", sl.query, e)
            regions = []
        children += [{"location": name[:255], "geo_id": geo_id, "f_tpr": ""} for name, geo_id in regions]
    created = 0
    for child in children:
        _, is_new = SearchSlice.objects.get_or_create(
            query=sl.query, location=child["location"], f_tpr=child["f_tpr"],
            defaults={"geo_id": child["geo_id"], "depth": sl.depth + 1},
        )
        created += is_new
    return created


def run_query(client: LinkedInClient, query: SearchQuery, max_pages=None) -> None:
    query.status = SearchQuery.Status.RUNNING
    query.last_run_at = timezone.now()
    query.last_error = ""
    query.save(update_fields=["status", "last_run_at", "last_error"])
    SearchSlice.objects.get_or_create(query=query, location="", f_tpr="")

    pending = [SearchSlice.Status.PENDING, SearchSlice.Status.RUNNING, SearchSlice.Status.FAILED]
    tried = set()
    while True:
        sl = (
            query.slices.filter(status__in=pending, attempts__lt=settings.LINKEDIN_MAX_ATTEMPTS)
            .exclude(id__in=tried)
            .order_by("depth", "id")
            .first()
        )
        if sl is None:
            break
        tried.add(sl.id)
        try:
            run_slice(client, query, sl, max_pages)
        except LinkedInError as e:
            sl.status = SearchSlice.Status.FAILED
            sl.attempts += 1
            sl.error = str(e)
            sl.save(update_fields=["status", "attempts", "error", "updated_at"])
            log.error("[%s] срез %s: %s", query, sl, e)
            continue
        if sl.status == SearchSlice.Status.DONE and sl.capped:
            n = spawn_children(client, sl)
            log.info("[%s] срез %s упёрся в лимит — создано %d дочерних", query, sl, n)

    query.jobs_found = query.hits.count()
    failed = query.slices.filter(status=SearchSlice.Status.FAILED).count()
    unfinished = query.slices.exclude(status=SearchSlice.Status.DONE).count()
    if failed:
        query.status = SearchQuery.Status.FAILED
        query.last_error = f"срезов с ошибкой: {failed}"
    elif unfinished:
        query.status = SearchQuery.Status.RUNNING
    else:
        query.status = SearchQuery.Status.DONE
    query.save(update_fields=["jobs_found", "status", "last_error"])
    log.info("[%s] итог: %d вакансий, статус %s", query, query.jobs_found, query.status)


def reset_query(query: SearchQuery) -> None:
    """Сбросить чекпоинты — следующий запуск пройдёт выдачу заново (вакансии не удаляются)."""
    query.slices.all().delete()
    query.status = SearchQuery.Status.NEW
    query.last_error = ""
    query.save(update_fields=["status", "last_error"])
