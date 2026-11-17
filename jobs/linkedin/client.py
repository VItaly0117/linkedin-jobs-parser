"""HTTP-клиент для гостевых эндпоинтов LinkedIn.

Отвечает за три вещи: общий лимит частоты запросов, повторы с backoff
и распознавание блокировок (429 / 999 / authwall).
"""

import logging
import random
import threading
import time

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException
from django.conf import settings

log = logging.getLogger(__name__)

BASE = "https://www.linkedin.com"
RETRY_STATUSES = {401, 403, 408, 425, 429, 999, 500, 502, 503, 504}


class LinkedInError(Exception):
    """Базовая ошибка клиента."""


class NotFound(LinkedInError):
    """404/410 — сущности больше нет."""


class BadRequest(LinkedInError):
    """400 — для поиска означает выход за предел пагинации."""


class Blocked(LinkedInError):
    """Не удалось получить ответ за отведённое число попыток."""


def backoff_delay(attempt: int, retry_after: float | None = None) -> float:
    """Пауза перед повтором №`attempt` (с нуля).

    Экспонента 2, 4, 8 … с потолком 120 с и «полным» джиттером ±50 %, чтобы
    параллельные воркеры не били в LinkedIn синхронно. Если сервер прислал
    Retry-After, ждём не меньше него.
    """
    delay = min(120.0, 2.0 * (2**attempt)) * random.uniform(0.5, 1.5)
    if retry_after:
        delay = max(delay, retry_after)
    return delay


class RateLimiter:
    """Потокобезопасный лимитер: не чаще `rps` запросов в секунду на процесс."""

    def __init__(self, rps: float):
        self.interval = 1.0 / rps if rps > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self.interval * random.uniform(0.7, 1.3)
        if slot > now:
            time.sleep(slot - now)


class LinkedInClient:
    def __init__(self, rps=None, max_retries=None, proxies=None, timeout=None):
        self.limiter = RateLimiter(rps if rps is not None else settings.LINKEDIN_RPS)
        self.max_retries = max_retries if max_retries is not None else settings.LINKEDIN_MAX_RETRIES
        self.proxies = list(proxies if proxies is not None else settings.LINKEDIN_PROXIES)
        self.timeout = timeout or settings.LINKEDIN_TIMEOUT
        self._local = threading.local()
        self._proxy_idx = 0
        self._lock = threading.Lock()

    # --- сессии (по одной на поток: curl-сессию нельзя делить между потоками) ---
    def _next_proxy(self):
        if not self.proxies:
            return None
        with self._lock:
            proxy = self.proxies[self._proxy_idx % len(self.proxies)]
            self._proxy_idx += 1
        return proxy

    def _session(self, fresh=False):
        s = getattr(self._local, "session", None)
        if s is None or fresh:
            if s is not None:
                s.close()
            s = requests.Session(impersonate="chrome")
            proxy = self._next_proxy()
            if proxy:
                s.proxies = {"http": proxy, "https": proxy}
            s.headers.update({"Accept-Language": "en-US,en;q=0.9"})
            self._local.session = s
        return s

    def get(self, path: str, params: dict | None = None, referer: str | None = None,
            stateless: bool = False) -> str:
        """GET с лимитом и повторами. Возвращает тело ответа (HTML).

        stateless=True — запрос без накопленных cookie. Страницы компаний
        отвечают 999, если прийти на них с cookie от предыдущих запросов.
        """
        url = path if path.startswith("http") else BASE + path
        headers = {"Referer": referer} if referer else {}
        last = ""
        for attempt in range(self.max_retries):
            self.limiter.wait()
            retry_after = None
            try:
                if stateless:
                    self._session().cookies.clear()
                r = self._session().get(
                    url, params=params, headers=headers, timeout=self.timeout, allow_redirects=True
                )
            except RequestException as e:
                last = f"network: {e}"
            else:
                if r.status_code == 200:
                    if "/authwall" in r.url or "/login" in r.url or "/checkpoint" in r.url:
                        last = "authwall redirect"
                    else:
                        return r.text
                elif r.status_code in (404, 410):
                    raise NotFound(f"{r.status_code} {url}")
                elif r.status_code == 400:
                    raise BadRequest(f"400 {url}")
                elif r.status_code in RETRY_STATUSES:
                    last = f"HTTP {r.status_code}"
                    ra = r.headers.get("Retry-After", "")
                    retry_after = float(ra) if ra.isdigit() else None
                else:
                    raise LinkedInError(f"HTTP {r.status_code} {url}")
            if attempt + 1 < self.max_retries:
                delay = backoff_delay(attempt, retry_after)
                log.warning("%s — %s, повтор через %.0f с (%d/%d)", url, last, delay, attempt + 1, self.max_retries)
                time.sleep(delay)
                self._session(fresh=True)  # новые cookie и, если есть, следующий прокси
        raise Blocked(f"{last} после {self.max_retries} попыток: {url}")
