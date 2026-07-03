from django.contrib import admin

from django_harel.models import HarelExecution, HarelOutbox, HarelQueue, HarelSpawn, HarelTimer


@admin.register(HarelExecution)
class HarelExecutionAdmin(admin.ModelAdmin):
    list_display = ["id", "definition_id", "status", "active_path", "version", "parent_id"]
    list_filter = ["status", "definition_id"]
    search_fields = ["id", "definition_id"]
    readonly_fields = ["id", "definition_id", "data", "version", "status", "active_path", "parent_id"]
    ordering = ["-id"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(HarelOutbox)
class HarelOutboxAdmin(admin.ModelAdmin):
    list_display = ["seq", "target_id", "event"]
    readonly_fields = ["seq", "target_id", "event"]
    ordering = ["seq"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(HarelTimer)
class HarelTimerAdmin(admin.ModelAdmin):
    list_display = ["execution_id", "path", "fire_at"]
    readonly_fields = ["execution_id", "path", "fire_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(HarelSpawn)
class HarelSpawnAdmin(admin.ModelAdmin):
    list_display = ["seq", "parent_id", "child_id", "root_path"]
    readonly_fields = ["seq", "parent_id", "child_id", "root_path", "context"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(HarelQueue)
class HarelQueueAdmin(admin.ModelAdmin):
    list_display = ["seq", "group_id", "available_at", "locked_by", "lock_expiry"]
    list_filter = ["group_id"]
    readonly_fields = ["seq", "group_id", "payload", "available_at", "locked_by", "lock_expiry"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
