"""Часть 2 — Details: добирает полные данные по вакансиям и компаниям из БД.

Очередь — сами таблицы: записи берутся пачками через SELECT … FOR UPDATE
SKIP LOCKED, поэтому можно запускать несколько процессов одновременно.
Сеть и разбор идут в потоках, запись в БД — в основном потоке.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.utils import timezone

from jobs.linkedin.client import LinkedInClient, NotFound
from jobs.linkedin.company import company_path, parse_company
from jobs.linkedin.details import JOB_PATH, parse_job
from jobs.models import Company, Job

log = logging.getLogger(__name__)

J = Job.DetailsStatus
C = Company.Status


def _clip(value: str, limit: int) -> str:
    return (value or "")[:limit]


def release_stale() -> None:
    """Вернуть в очередь записи, оставшиеся in_progress после упавшего процесса."""
    cutoff = timezone.now() - timedelta(minutes=settings.LINKEDIN_STALE_LOCK_MINUTES)
    n = Job.objects.filter(details_status=J.IN_PROGRESS, locked_at__lt=cutoff).update(
        details_status=J.PENDING, locked_at=None
    )
    m = Company.objects.filter(status=C.IN_PROGRESS, locked_at__lt=cutoff).update(
        status=C.PENDING, locked_at=None
    )
    if n or m:
        log.info("возвращено в очередь: вакансий %d, компаний %d", n, m)


def _claim(model, status_field: str, in_progress: str, pending: str, failed: str,
           size: int, retry_failed: bool) -> list:
    cond = Q(**{status_field: pending})
    if retry_failed:
        cond |= Q(**{status_field: failed}, attempts__lt=settings.LINKEDIN_MAX_ATTEMPTS)
    with transaction.atomic():
        rows = list(
            model.objects.select_for_update(skip_locked=True).filter(cond).order_by("attempts", "id")[:size]
        )
        model.objects.filter(pk__in=[r.pk for r in rows]).update(
            **{status_field: in_progress}, locked_at=timezone.now()
        )
    return rows


def _fetch(client: LinkedInClient, path: str, parser, stateless: bool = False):
    """Выполняется в потоке. Возвращает ('ok', data) | ('gone', None) | ('error', текст)."""
    try:
        return "ok", parser(client.get(path, stateless=stateless))
    except NotFound:
        return "gone", None
    except Exception as e:  # сеть, блокировка, неожиданная разметка — процесс не роняем
        return "error", f"{type(e).__name__}: {e}"


def _safe_save(save, obj, outcome: str, data) -> str:
    """Сохраняет результат; если БД отвергла данные — помечает запись ошибкой, а не роняет процесс."""
    try:
        with transaction.atomic():
            save(obj, outcome, data)
        return outcome
    except DatabaseError as e:
        log.error("%s %s: не удалось сохранить: %s", type(obj).__name__, obj.pk, e)
        obj.refresh_from_db()
        save(obj, "error", f"{type(e).__name__}: {e}")
        return "error"


# ---------- вакансии ----------

def _save_job(job: Job, outcome: str, data) -> None:
    job.locked_at = None
    if outcome == "gone":
        job.details_status = J.GONE
        job.is_closed = True
        job.last_error = ""
    elif outcome == "error":
        job.details_status = J.FAILED
        job.attempts += 1
        job.last_error = data
    else:
        job.title = _clip(data.title, 500) or job.title
        job.company_name_raw = _clip(data.company_name, 500) or job.company_name_raw
        job.location = _clip(data.location, 500) or job.location
        job.posted_text = _clip(data.posted_text, 128) or job.posted_text
        job.applicants_text = _clip(data.applicants_text, 128)
        job.description_html = data.description_html
        job.description_text = data.description_text
        job.seniority_level = _clip(data.criteria.get("seniority_level", ""), 128)
        job.employment_type = _clip(data.criteria.get("employment_type", ""), 128)
        job.job_function = _clip(data.criteria.get("job_function", ""), 500)
        job.industries = _clip(data.criteria.get("industries", ""), 500)
        job.salary_text = _clip(data.salary_text, 255) or job.salary_text
        job.apply_url = data.apply_url
        job.is_easy_apply = data.is_easy_apply
        job.is_closed = data.is_closed
        if data.company_slug:
            company, _ = Company.objects.get_or_create(
                linkedin_slug=_clip(data.company_slug, 255),
                defaults={"name": _clip(data.company_name, 500), "url": _clip(data.company_url, 500)},
            )
            if data.company_linkedin_id and not company.linkedin_id:
                Company.objects.filter(pk=company.pk).update(linkedin_id=data.company_linkedin_id)
            job.company = company
        job.details_status = J.DONE
        job.details_fetched_at = timezone.now()
        job.last_error = ""
    job.save()


def process_jobs(client, pool, batch: int, retry_failed: bool, limit: int | None) -> int:
    done = 0
    while limit is None or done < limit:
        size = batch if limit is None else min(batch, limit - done)
        jobs = _claim(Job, "details_status", J.IN_PROGRESS, J.PENDING, J.FAILED, size, retry_failed)
        if not jobs:
            break
        paths = [JOB_PATH.format(job_id=j.linkedin_job_id) for j in jobs]
        results = pool.map(lambda p: _fetch(client, p, parse_job), paths)
        stats = {"ok": 0, "gone": 0, "error": 0}
        for job, (outcome, data) in zip(jobs, results):
            outcome = _safe_save(_save_job, job, outcome, data)
            stats[outcome] += 1
        done += len(jobs)
        left = Job.objects.filter(details_status=J.PENDING).count()
        log.info("вакансии: ok=%(ok)d gone=%(gone)d error=%(error)d" % stats + f", в очереди {left}")
        if retry_failed and stats["ok"] == 0 and stats["gone"] == 0:
            log.warning("вся пачка завершилась ошибкой — останавливаюсь, чтобы не усугублять блокировку")
            break
    return done


# ---------- компании ----------

def _save_company(company: Company, outcome: str, data) -> None:
    company.locked_at = None
    if outcome == "gone":
        company.status = C.NOT_FOUND
        company.last_error = ""
    elif outcome == "error":
        company.status = C.FAILED
        company.attempts += 1
        company.last_error = data
    else:
        company.name = _clip(data.name, 500) or company.name
        company.linkedin_id = data.linkedin_id or company.linkedin_id
        company.tagline = data.tagline
        company.description = data.description
        company.website = _clip(data.website, 500)
        company.industry = _clip(data.industry, 500)
        company.company_size = _clip(data.company_size, 255)
        company.headquarters = _clip(data.headquarters, 500)
        company.company_type = _clip(data.company_type, 255)
        company.founded = _clip(data.founded, 64)
        company.specialties = data.specialties
        company.logo_url = data.logo_url or company.logo_url
        company.followers = data.followers
        company.employees_on_linkedin = data.employees_on_linkedin
        company.raw = data.raw
        company.status = C.DONE
        company.fetched_at = timezone.now()
        company.last_error = ""
    company.save()


def process_companies(client, pool, batch: int, retry_failed: bool, limit: int | None) -> int:
    done = 0
    while limit is None or done < limit:
        size = batch if limit is None else min(batch, limit - done)
        companies = _claim(Company, "status", C.IN_PROGRESS, C.PENDING, C.FAILED, size, retry_failed)
        if not companies:
            break
        paths = [company_path(c.linkedin_slug) for c in companies]
        results = pool.map(lambda p: _fetch(client, p, parse_company, stateless=True), paths)
        stats = {"ok": 0, "gone": 0, "error": 0}
        for company, (outcome, data) in zip(companies, results):
            outcome = _safe_save(_save_company, company, outcome, data)
            stats[outcome] += 1
        done += len(companies)
        left = Company.objects.filter(status=C.PENDING).count()
        log.info("компании: ok=%(ok)d gone=%(gone)d error=%(error)d" % stats + f", в очереди {left}")
        if retry_failed and stats["ok"] == 0 and stats["gone"] == 0:
            log.warning("вся пачка компаний завершилась ошибкой — останавливаюсь")
            break
    return done


def run(workers=4, batch=20, limit=None, retry_failed=False, jobs=True, companies=True) -> dict:
    release_stale()
    client = LinkedInClient()
    result = {"jobs": 0, "companies": 0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        if jobs:
            result["jobs"] = process_jobs(client, pool, batch, retry_failed, limit)
        if companies:
            result["companies"] = process_companies(client, pool, batch, retry_failed, limit)
    return result


def requeue_jobs(qs) -> int:
    return qs.update(details_status=J.PENDING, attempts=0, last_error="", locked_at=None)


def requeue_companies(qs) -> int:
    return qs.update(status=C.PENDING, attempts=0, last_error="", locked_at=None)
