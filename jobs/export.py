"""Выгрузка вакансий с данными компаний в CSV / XLSX."""

import csv
import re

from openpyxl import Workbook

JOB_FIELDS = [
    "linkedin_job_id", "title", "company_name_raw", "location", "url", "listed_at", "posted_text",
    "seniority_level", "employment_type", "job_function", "industries",
    "salary_text", "applicants_text", "benefits_text", "apply_url", "is_easy_apply", "is_closed",
    "details_status", "details_fetched_at", "description_text",
]
COMPANY_FIELDS = [
    "linkedin_slug", "linkedin_id", "name", "url", "website", "industry", "company_size",
    "employees_on_linkedin", "headquarters", "company_type", "founded", "specialties",
    "followers", "tagline", "description", "logo_url", "status",
]
HEADER = JOB_FIELDS + ["search_queries"] + [f"company_{f}" for f in COMPANY_FIELDS]

_ILLEGAL_XLSX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
XLSX_CELL_LIMIT = 32767


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return value if isinstance(value, (int, float)) else str(value)


def iter_rows(queryset):
    qs = queryset.select_related("company").prefetch_related("queries").order_by("id")
    for job in qs.iterator(chunk_size=500):
        row = [_cell(getattr(job, f)) for f in JOB_FIELDS]
        row.append("; ".join(str(q) for q in job.queries.all()))
        company = job.company
        row += [_cell(getattr(company, f)) if company else "" for f in COMPANY_FIELDS]
        yield row


def write_csv(queryset, fileobj) -> int:
    writer = csv.writer(fileobj)
    writer.writerow(HEADER)
    n = 0
    for row in iter_rows(queryset):
        writer.writerow(row)
        n += 1
    return n


def write_xlsx(queryset, path) -> int:
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("jobs")
    ws.append(HEADER)
    n = 0
    for row in iter_rows(queryset):
        ws.append(
            [_ILLEGAL_XLSX.sub("", v)[:XLSX_CELL_LIMIT] if isinstance(v, str) else v for v in row]
        )
        n += 1
    wb.save(path)
    return n
