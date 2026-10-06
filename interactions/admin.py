from django.contrib import admin
from interactions.models import Interaction


@admin.register(Interaction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = ('subject', 'interaction_type', 'customer', 'lead', 'sales_staff', 'is_customer_visible', 'interaction_date')
    list_filter = ('interaction_type', 'is_customer_visible', 'interaction_date')
    search_fields = ('subject', 'description', 'customer__user__full_name', 'lead__full_name')
    date_hierarchy = 'interaction_date'
