from django.contrib import admin
from activity_logs.models import ActivityLog


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'action', 'module_name', 'record_id', 'ip_address')
    list_filter = ('action', 'module_name', 'created_at')
    search_fields = ('user__username', 'user__email', 'module_name', 'record_id', 'description')
    readonly_fields = ('user', 'action', 'module_name', 'record_id', 'description', 'metadata', 'ip_address', 'created_at')
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
