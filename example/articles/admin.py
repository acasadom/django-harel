from django.contrib import admin

from articles.models import Article


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "author", "status", "publishing_status"]
    readonly_fields = ["status", "reviewer_note", "published_at", "publishing_link"]

    @admin.display(description="publishing")
    def publishing_status(self, obj):
        return obj.publishing.status.value if obj.publishing.exists else "—"

    @admin.display(description="publishing")
    def publishing_link(self, obj):
        from django.contrib.admin.utils import quote
        from django.urls import reverse
        from django.utils.html import format_html

        if not obj.pk or not obj.publishing.exists:
            return "—"
        url = reverse("admin:django_harel_harelexecution_change", args=[quote(obj.publishing.id)])
        return format_html('<a href="{}">{}</a>', url, obj.publishing.id)
