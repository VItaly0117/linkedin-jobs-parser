"""Разбор публичной страницы компании: https://www.linkedin.com/company/<slug>."""

import json
import re
from dataclasses import dataclass
from urllib.parse import quote

from selectolax.lexbor import LexborHTMLParser

from .details import ParseError

COMPANY_PATH = "/company/{slug}"

_FOLLOWERS_RE = re.compile(r"(?<![\d.,])([\d.,]+)\s+followers", re.I)
_EMPLOYEES_RE = re.compile(r"View all ([\d.,]+) employees", re.I)
_COMPANY_ID_RE = re.compile(r"(?:facetCurrentCompany|f_C)(?:%3D|=)(?:%255B|%5B|\[)?(\d+)")


@dataclass
class CompanyData:
    name: str = ""
    linkedin_id: int | None = None
    tagline: str = ""
    description: str = ""
    website: str = ""
    industry: str = ""
    company_size: str = ""
    headquarters: str = ""
    company_type: str = ""
    founded: str = ""
    specialties: str = ""
    logo_url: str = ""
    followers: int | None = None
    employees_on_linkedin: int | None = None
    raw: dict | None = None


def company_path(slug: str) -> str:
    return COMPANY_PATH.format(slug=quote(slug, safe=""))


def _text(node) -> str:
    return " ".join(node.text().split()) if node else ""


def _int(s: str) -> int | None:
    digits = re.sub(r"\D", "", s or "")
    return int(digits) if digits else None


def _about(tree, key: str) -> str:
    """Значение из блока About: <div data-test-id="about-us__<key>"><dt/><dd/></div>."""
    node = tree.css_first(f'[data-test-id="about-us__{key}"]')
    if not node:
        return ""
    dd = node.css_first("dd")
    return _text(dd) if dd else _text(node)


def _organization_ld(tree) -> dict | None:
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text())
        except ValueError:
            continue
        items = data.get("@graph", [data]) if isinstance(data, dict) else data
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "Organization":
                return item
    return None


def parse_company(html: str) -> CompanyData:
    tree = LexborHTMLParser(html)
    c = CompanyData()
    c.name = _text(tree.css_first("h1.top-card-layout__title, .top-card-layout__title"))
    if not c.name:
        raise ParseError("нет названия компании (неожиданная разметка или authwall)")

    c.tagline = _text(tree.css_first(".top-card-layout__second-subline"))
    c.description = _text(tree.css_first('[data-test-id="about-us__description"]'))
    website = tree.css_first('[data-test-id="about-us__website"] a')
    c.website = _text(website) if website else ""
    c.industry = _about(tree, "industry") or _text(tree.css_first(".top-card-layout__headline"))
    c.company_size = _about(tree, "size")
    c.headquarters = _about(tree, "headquarters")
    c.company_type = _about(tree, "organizationType")
    c.founded = _about(tree, "foundedOn")
    c.specialties = _about(tree, "specialties")

    logo = tree.css_first(".top-card-layout__entity-image")
    if logo:
        c.logo_url = logo.attributes.get("data-delayed-url") or logo.attributes.get("src") or ""

    m = _FOLLOWERS_RE.search(_text(tree.css_first(".top-card-layout__first-subline")))
    c.followers = _int(m.group(1)) if m else None
    m = _EMPLOYEES_RE.search(html)
    c.employees_on_linkedin = _int(m.group(1)) if m else None
    m = _COMPANY_ID_RE.search(html)
    c.linkedin_id = int(m.group(1)) if m else None

    c.raw = _organization_ld(tree)
    if c.raw and c.employees_on_linkedin is None:
        employees = c.raw.get("numberOfEmployees")
        value = employees.get("value") if isinstance(employees, dict) else employees
        c.employees_on_linkedin = _int(str(value)) if value else None
    return c
