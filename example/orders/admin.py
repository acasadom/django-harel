from django.contrib import admin

from orders.models import Order


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["id", "customer", "status", "card_ok", "reminded", "workflow_state"]
    list_editable = ["card_ok"]
    readonly_fields = ["status", "reminded", "workflow"]

    @admin.display(description="workflow")
    def workflow_state(self, obj):
        return obj.fulfilment.status.value if obj.fulfilment.exists else "—"

    @admin.display(description="execution")
    def workflow(self, obj):
        from django.contrib.admin.utils import quote
        from django.urls import reverse
        from django.utils.html import format_html

        if not obj.pk or not obj.fulfilment.exists:
            return "—"
        url = reverse("admin:django_harel_harelexecution_change", args=[quote(obj.fulfilment.id)])
        return format_html('<a href="{}">{}</a>', url, obj.fulfilment.id)
