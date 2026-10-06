from django.contrib import admin
from reminders.models import Reminder


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ('title', 'reminder_type', 'priority', 'status', 'due_date', 'assigned_to', 'customer', 'is_customer_visible')
    list_filter = ('status', 'priority', 'reminder_type', 'is_customer_visible', 'due_date')
    search_fields = ('title', 'description', 'assigned_to__username', 'customer__user__full_name')
    date_hierarchy = 'due_date'
