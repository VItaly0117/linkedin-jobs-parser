from django.contrib import admin, messages
from django.db.models import Count
from django.http import StreamingHttpResponse
from django.utils.html import format_html

from . import export
from .models import Company, Job, SearchQuery, SearchSlice
from .services import details_runner, search_runner

admin.site.site_header = "Парсер вакансий LinkedIn"
admin.site.site_title = "LinkedIn parser"
admin.site.index_title = "Управление"


class SearchSliceInline(admin.TabularInline):
    model = SearchSlice
    extra = 0
    can_delete = False
    fields = ("__str__", "status", "next_start", "jobs_seen", "jobs_new", "capped", "attempts", "error", "updated_at")
    readonly_fields = fields
    ordering = ("depth", "id")
    verbose_name_plural = "Срезы поиска (прогресс пагинации)"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SearchQuery)
class SearchQueryAdmin(admin.ModelAdmin):
    list_display = ("keywords", "country", "is_active", "status", "jobs_found", "slices_progress", "last_run_at")
    list_filter = ("status", "is_active", "country")
    list_editable = ("is_active",)
    search_fields = ("keywords", "country")
    readonly_fields = ("status", "jobs_found", "last_run_at", "last_error", "created_at")
    fields = ("keywords", "country", "geo_id", "is_active") + readonly_fields
    inlines = [SearchSliceInline]
    actions = ["reset_and_recollect", "activate", "deactivate"]

    @admin.display(description="Срезы готово/всего")
    def slices_progress(self, obj):
        total = obj.slices.count()
        done = obj.slices.filter(status=SearchSlice.Status.DONE).count()
        return f"{done}/{total}"

    @admin.action(description="Сбросить чекпоинты (собрать заново при следующем run_search)")
    def reset_and_recollect(self, request, queryset):
        for query in queryset:
            search_runner.reset_query(query)
        self.message_user(request, f"Сброшено запросов: {queryset.count()}", messages.SUCCESS)

    @admin.action(description="Включить")
    def activate(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Выключить")
    def deactivate(self, request, queryset):
        queryset.update(is_active=False)


class _Echo:
    def write(self, value):
        return value


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "company_link", "location", "employment_type", "seniority_level",
                    "listed_at", "details_status", "attempts")
    list_filter = ("details_status", "queries", "employment_type", "seniority_level",
                   "is_closed", "is_easy_apply")
    search_fields = ("title", "company_name_raw", "company__name", "location", "=linkedin_job_id")
    list_select_related = ("company",)
    date_hierarchy = "listed_at"
    list_per_page = 50
    autocomplete_fields = ("company",)
    actions = ["requeue_details", "export_csv"]
    readonly_fields = ("linkedin_job_id", "open_link", "queries_list", "description_rendered",
                       "details_status", "attempts", "last_error", "locked_at",
                       "details_fetched_at", "created_at", "updated_at")
    fieldsets = (
        ("Вакансия", {"fields": ("linkedin_job_id", "title", "open_link", "company", "company_name_raw",
                                 "location", "listed_at", "posted_text", "queries_list")}),
        ("Детали", {"fields": ("seniority_level", "employment_type", "job_function", "industries",
                               "salary_text", "applicants_text", "benefits_text",
                               "apply_url", "is_easy_apply", "is_closed", "description_rendered")}),
        ("Обработка", {"fields": ("details_status", "attempts", "last_error", "locked_at",
                                  "details_fetched_at", "created_at", "updated_at")}),
    )

    @admin.display(description="Компания", ordering="company__name")
    def company_link(self, obj):
        if not obj.company_id:
            return obj.company_name_raw
        return format_html('<a href="/admin/jobs/company/{}/change/">{}</a>', obj.company_id, obj.company)

    @admin.display(description="На LinkedIn")
    def open_link(self, obj):
        return format_html('<a href="{0}" target="_blank" rel="noopener">{0}</a>', obj.url) if obj.url else "—"

    @admin.display(description="Найдена по запросам")
    def queries_list(self, obj):
        return ", ".join(str(q) for q in obj.queries.all()) or "—"

    @admin.display(description="Описание")
    def description_rendered(self, obj):
        return format_html('<div style="max-width:900px;white-space:pre-wrap">{}</div>', obj.description_text)

    @admin.action(description="Повторить сбор деталей (вернуть в очередь)")
    def requeue_details(self, request, queryset):
        n = details_runner.requeue_jobs(queryset)
        self.message_user(request, f"В очередь возвращено вакансий: {n}", messages.SUCCESS)

    @admin.action(description="Выгрузить выбранное в CSV")
    def export_csv(self, request, queryset):
        writer = __import__("csv").writer(_Echo())

        def stream():
            yield "﻿" + writer.writerow(export.HEADER)
            for row in export.iter_rows(queryset):
                yield writer.writerow(row)

        response = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="jobs.csv"'
        return response


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "linkedin_slug", "industry", "company_size", "headquarters",
                    "jobs_count", "status", "attempts")
    list_filter = ("status", "company_type", "company_size")
    search_fields = ("name", "linkedin_slug", "industry", "headquarters")
    actions = ["requeue"]
    readonly_fields = ("linkedin_slug", "linkedin_id", "status", "attempts", "last_error",
                       "locked_at", "fetched_at", "created_at", "raw")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_jobs=Count("jobs"))

    @admin.display(description="Вакансий", ordering="_jobs")
    def jobs_count(self, obj):
        return obj._jobs

    @admin.action(description="Повторить сбор (вернуть в очередь)")
    def requeue(self, request, queryset):
        n = details_runner.requeue_companies(queryset)
        self.message_user(request, f"В очередь возвращено компаний: {n}", messages.SUCCESS)
