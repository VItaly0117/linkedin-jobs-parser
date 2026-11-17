"""Разбор страницы вакансии: /jobs-guest/jobs/api/jobPosting/<id>."""

import json
import re
from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser

from .search import company_slug_from_url

JOB_PATH = "/jobs-guest/jobs/api/jobPosting/{job_id}"

_COMPANY_ID_RE = re.compile(r"facetCurrentCompany(?:%3D|=)(\d+)")
CRITERIA = {
    "seniority level": "seniority_level",
    "employment type": "employment_type",
    "job function": "job_function",
    "industries": "industries",
}


class ParseError(Exception):
    pass


@dataclass
class JobDetails:
    title: str = ""
    company_name: str = ""
    company_slug: str = ""
    company_url: str = ""
    company_linkedin_id: int | None = None
    location: str = ""
    posted_text: str = ""
    applicants_text: str = ""
    description_html: str = ""
    description_text: str = ""
    salary_text: str = ""
    apply_url: str = ""
    is_easy_apply: bool | None = None
    is_closed: bool = False
    criteria: dict = field(default_factory=dict)


def _text(node) -> str:
    return " ".join(node.text().split()) if node else ""


def _code_value(tree, code_id: str) -> str:
    """LinkedIn прячет значения в <code id=..><!--"json"--></code>."""
    node = tree.css_first(f"code#{code_id}")
    if not node:
        return ""
    raw = node.html.split("<!--", 1)[-1].rsplit("-->", 1)[0].strip()
    try:
        value = json.loads(raw)
    except ValueError:
        return ""
    return value if isinstance(value, str) else ""


def parse_job(html: str) -> JobDetails:
    tree = LexborHTMLParser(html)
    d = JobDetails()
    d.title = _text(tree.css_first(".top-card-layout__title, .topcard__title"))
    if not d.title:
        raise ParseError("нет заголовка вакансии (неожиданная разметка)")

    org = tree.css_first("a.topcard__org-name-link")
    d.company_name = _text(org) or _text(tree.css_first(".topcard__flavor"))
    if org:
        d.company_url = (org.attributes.get("href") or "").split("?")[0]
        d.company_slug = company_slug_from_url(d.company_url)
    m = _COMPANY_ID_RE.search(html)
    if m:
        d.company_linkedin_id = int(m.group(1))

    d.location = _text(tree.css_first(".topcard__flavor--bullet"))
    d.posted_text = _text(tree.css_first(".posted-time-ago__text"))
    d.applicants_text = _text(tree.css_first(".num-applicants__caption"))

    desc = tree.css_first(".show-more-less-html__markup") or tree.css_first(".description__text")
    if desc:
        d.description_html = desc.html.strip()
        d.description_text = desc.text(separator="\n", strip=True).strip()

    for item in tree.css(".description__job-criteria-item"):
        key = _text(item.css_first(".description__job-criteria-subheader")).lower()
        if key in CRITERIA:
            d.criteria[CRITERIA[key]] = _text(item.css_first(".description__job-criteria-text"))

    d.salary_text = _text(tree.css_first(".compensation__salary, .salary"))
    d.is_closed = tree.css_first(".closed-job") is not None
    d.apply_url = _code_value(tree, "applyUrl")
    if not d.is_closed:
        # Внешняя ссылка есть только у «offsite»-вакансий; без неё отклик идёт через LinkedIn.
        d.is_easy_apply = not d.apply_url and "apply-link-offsite" not in html
    return d
