"""Поиск вакансий: гостевой эндпоинт, разбор карточек, дробление на срезы."""

import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote

from selectolax.lexbor import LexborHTMLParser

SEARCH_PATH = "/jobs-guest/jobs/api/seeMoreJobPostings/search"

# LinkedIn отдаёт гостю не больше ~1000 результатов на набор фильтров.
RESULT_CAP = 1000
CAP_THRESHOLD = 950  # срез считается «упёршимся в лимит», если дошли до этого start

# Проверено на живых запросах: гостевой эндпоинт учитывает только location/geoId
# и f_TPR (окно времени в секундах). f_E, f_JT, f_WT и sortBy он игнорирует,
# поэтому выдачу дробим по регионам страны и по сужающимся окнам времени.
TIME_WINDOWS = ["r2592000", "r604800", "r259200", "r86400", "r43200", "r10800", "r3600"]

TYPEAHEAD_PATH = "/jobs-guest/api/typeaheadHits"
REGION_GEO_TYPES = ["ADMIN_DIVISION_1", "MARKET_AREA,POPULATED_PLACE"]

_COMPANY_RE = re.compile(r"linkedin\.com/company/([^/?#]+)")
_JOB_ID_RE = re.compile(r"jobPosting:(\d+)")


@dataclass
class JobCard:
    job_id: int
    title: str
    url: str
    company_name: str
    company_slug: str
    company_url: str
    logo_url: str
    location: str
    listed_at: date | None
    posted_text: str
    salary_text: str
    benefits_text: str


def build_params(keywords: str, location: str, geo_id: str, f_tpr: str, start: int) -> dict:
    params = {"keywords": keywords, "location": location, "start": start}
    if geo_id:
        params["geoId"] = geo_id
    if f_tpr:
        params["f_TPR"] = f_tpr
    return params


def next_time_window(f_tpr: str) -> str | None:
    """Следующее, более узкое окно времени для среза, упёршегося в лимит.

    Окна вложены (сутки входят в неделю), поэтому более узкое окно нужно только
    тогда, когда более широкое не поместилось в 1000 результатов: если поместилось,
    оно уже содержит всё, что есть в узких. Отсюда цепочка, а не веер срезов.
    None — сужать дальше некуда.
    """
    if not f_tpr:
        return TIME_WINDOWS[0]
    i = TIME_WINDOWS.index(f_tpr) if f_tpr in TIME_WINDOWS else len(TIME_WINDOWS) - 1
    return TIME_WINDOWS[i + 1] if i + 1 < len(TIME_WINDOWS) else None


def fetch_regions(client, country: str) -> list[tuple[str, str]]:
    """Регионы и крупные города страны из подсказок локаций: [(название, geoId)]."""
    regions = {}
    for geo_types in REGION_GEO_TYPES:
        body = client.get(
            TYPEAHEAD_PATH, params={"query": country, "typeaheadType": "GEO", "geoTypes": geo_types}
        )
        for hit in json.loads(body):
            name = hit.get("displayName") or ""
            if name and name.lower() != country.lower():
                regions.setdefault(name, str(hit.get("id") or ""))
    return list(regions.items())


def company_slug_from_url(url: str) -> str:
    m = _COMPANY_RE.search(url or "")
    return unquote(m.group(1)).lower() if m else ""


def _text(node) -> str:
    return " ".join(node.text().split()) if node else ""


def parse_search(html: str) -> list[JobCard]:
    cards = []
    for node in LexborHTMLParser(html).css("[data-entity-urn]"):
        m = _JOB_ID_RE.search(node.attributes.get("data-entity-urn") or "")
        if not m:
            continue
        link = node.css_first("a.base-card__full-link") or node
        url = (link.attributes.get("href") or "").split("?")[0]
        company_a = node.css_first(".base-search-card__subtitle a")
        company_url = (company_a.attributes.get("href") or "").split("?")[0] if company_a else ""
        time_node = node.css_first("time")
        listed = None
        if time_node and time_node.attributes.get("datetime"):
            try:
                listed = date.fromisoformat(time_node.attributes["datetime"][:10])
            except ValueError:
                pass
        img = node.css_first("img")
        cards.append(
            JobCard(
                job_id=int(m.group(1)),
                title=_text(node.css_first(".base-search-card__title")),
                url=url,
                company_name=_text(node.css_first(".base-search-card__subtitle")),
                company_slug=company_slug_from_url(company_url),
                company_url=company_url,
                logo_url=(img.attributes.get("data-delayed-url") or "") if img else "",
                location=_text(node.css_first(".job-search-card__location")),
                listed_at=listed,
                posted_text=_text(time_node),
                salary_text=_text(node.css_first(".job-search-card__salary-info")),
                benefits_text=_text(node.css_first(".job-posting-benefits__text")),
            )
        )
    return cards
