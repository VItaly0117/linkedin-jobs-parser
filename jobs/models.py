from django.db import models


class SearchQuery(models.Model):
    """Поисковый запрос: ключевые слова + страна. Точка входа для части Search."""

    class Status(models.TextChoices):
        NEW = "new", "Новый"
        RUNNING = "running", "В работе"
        DONE = "done", "Собран"
        FAILED = "failed", "Есть ошибки"

    keywords = models.CharField("Поисковый запрос", max_length=255)
    country = models.CharField(
        "Страна", max_length=255, help_text="Как в поле Location на LinkedIn, например: United States"
    )
    geo_id = models.CharField(
        "geoId", max_length=32, blank=True, help_text="Необязательно: точный geoId LinkedIn"
    )
    is_active = models.BooleanField("Активен", default=True)
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.NEW, db_index=True
    )
    jobs_found = models.PositiveIntegerField("Найдено вакансий", default=0)
    last_run_at = models.DateTimeField("Последний запуск", null=True, blank=True)
    last_error = models.TextField("Последняя ошибка", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Поисковый запрос"
        verbose_name_plural = "Поисковые запросы"
        constraints = [
            models.UniqueConstraint(fields=["keywords", "country"], name="uniq_query_keywords_country")
        ]

    def __str__(self):
        return f"{self.keywords} / {self.country}"


class SearchSlice(models.Model):
    """Срез запроса по фильтрам LinkedIn и чекпоинт пагинации.

    Гостевой поиск отдаёт не больше ~1000 результатов на набор фильтров, поэтому
    запрос дробится на срезы по регионам и окнам времени. `next_start` сохраняется после каждой страницы —
    перезапуск продолжает с того же места.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "В очереди"
        RUNNING = "running", "В работе"
        DONE = "done", "Готов"
        FAILED = "failed", "Ошибка"

    query = models.ForeignKey(SearchQuery, on_delete=models.CASCADE, related_name="slices")
    location = models.CharField(
        "Регион", max_length=255, blank=True, help_text="Пусто — вся страна запроса"
    )
    geo_id = models.CharField("geoId региона", max_length=32, blank=True)
    f_tpr = models.CharField("Период (f_TPR)", max_length=16, blank=True)
    depth = models.PositiveSmallIntegerField("Глубина", default=0)
    next_start = models.PositiveIntegerField("Следующий start", default=0)
    jobs_seen = models.PositiveIntegerField("Карточек получено", default=0)
    jobs_new = models.PositiveIntegerField("Новых вакансий", default=0)
    capped = models.BooleanField("Упёрся в лимит", default=False)
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveSmallIntegerField("Попыток", default=0)
    error = models.TextField("Ошибка", blank=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Срез поиска"
        verbose_name_plural = "Срезы поиска"
        constraints = [
            models.UniqueConstraint(
                fields=["query", "location", "f_tpr"], name="uniq_slice_filters"
            )
        ]

    def __str__(self):
        parts = [self.location or "вся страна"]
        if self.f_tpr:
            parts.append(f"f_TPR={self.f_tpr}")
        return ", ".join(parts)


class Company(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "В очереди"
        IN_PROGRESS = "in_progress", "В работе"
        DONE = "done", "Собрана"
        FAILED = "failed", "Ошибка"
        NOT_FOUND = "not_found", "Не найдена"

    linkedin_slug = models.CharField("Slug", max_length=255, unique=True)
    linkedin_id = models.BigIntegerField("ID LinkedIn", null=True, blank=True)
    name = models.CharField("Название", max_length=500, blank=True)
    url = models.URLField("Страница LinkedIn", max_length=500, blank=True)
    website = models.CharField("Сайт", max_length=500, blank=True)
    industry = models.CharField("Отрасль", max_length=500, blank=True)
    company_size = models.CharField("Размер", max_length=255, blank=True)
    employees_on_linkedin = models.PositiveIntegerField("Сотрудников в LinkedIn", null=True, blank=True)
    headquarters = models.CharField("Штаб-квартира", max_length=500, blank=True)
    company_type = models.CharField("Тип", max_length=255, blank=True)
    founded = models.CharField("Год основания", max_length=64, blank=True)
    specialties = models.TextField("Специализации", blank=True)
    tagline = models.TextField("Слоган", blank=True)
    description = models.TextField("Описание", blank=True)
    logo_url = models.TextField("Логотип", blank=True)
    followers = models.PositiveIntegerField("Подписчиков", null=True, blank=True)
    raw = models.JSONField("JSON-LD", null=True, blank=True)

    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveSmallIntegerField("Попыток", default=0)
    last_error = models.TextField("Последняя ошибка", blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField("Собрана", null=True, blank=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Компания"
        verbose_name_plural = "Компании"

    def __str__(self):
        return self.name or self.linkedin_slug


class Job(models.Model):
    class DetailsStatus(models.TextChoices):
        PENDING = "pending", "В очереди"
        IN_PROGRESS = "in_progress", "В работе"
        DONE = "done", "Собрана"
        FAILED = "failed", "Ошибка"
        GONE = "gone", "Удалена на LinkedIn"

    # --- заполняет Search ---
    linkedin_job_id = models.BigIntegerField("ID вакансии", unique=True)
    title = models.CharField("Название", max_length=500)
    company = models.ForeignKey(
        Company, verbose_name="Компания", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="jobs",
    )
    company_name_raw = models.CharField("Компания (текст)", max_length=500, blank=True)
    location = models.CharField("Локация", max_length=500, blank=True)
    url = models.URLField("Ссылка", max_length=1000, blank=True)
    listed_at = models.DateField("Дата публикации", null=True, blank=True)
    posted_text = models.CharField("Опубликовано (текст)", max_length=128, blank=True)
    salary_text = models.CharField("Зарплата", max_length=255, blank=True)
    benefits_text = models.CharField("Плашка", max_length=255, blank=True)
    queries = models.ManyToManyField(
        SearchQuery, through="JobSearchHit", related_name="jobs", verbose_name="Запросы"
    )

    # --- заполняет Details ---
    description_html = models.TextField("Описание (HTML)", blank=True)
    description_text = models.TextField("Описание", blank=True)
    seniority_level = models.CharField("Уровень", max_length=128, blank=True)
    employment_type = models.CharField("Тип занятости", max_length=128, blank=True)
    job_function = models.CharField("Функция", max_length=500, blank=True)
    industries = models.CharField("Отрасли", max_length=500, blank=True)
    applicants_text = models.CharField("Откликов", max_length=128, blank=True)
    apply_url = models.TextField("Ссылка для отклика", blank=True)
    is_easy_apply = models.BooleanField("Easy Apply", null=True, blank=True)
    is_closed = models.BooleanField("Закрыта", default=False)

    # --- служебные ---
    details_status = models.CharField(
        "Статус обработки", max_length=16, choices=DetailsStatus.choices,
        default=DetailsStatus.PENDING, db_index=True,
    )
    attempts = models.PositiveSmallIntegerField("Попыток", default=0)
    last_error = models.TextField("Последняя ошибка", blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    details_fetched_at = models.DateTimeField("Детали собраны", null=True, blank=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    class Meta:
        verbose_name = "Вакансия"
        verbose_name_plural = "Вакансии"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.title} ({self.linkedin_job_id})"


class JobSearchHit(models.Model):
    """Связь «вакансия найдена по запросу». Одна вакансия — много запросов, без дублей."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="hits")
    query = models.ForeignKey(SearchQuery, on_delete=models.CASCADE, related_name="hits")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Попадание в поиск"
        verbose_name_plural = "Попадания в поиск"
        constraints = [models.UniqueConstraint(fields=["job", "query"], name="uniq_job_query")]
