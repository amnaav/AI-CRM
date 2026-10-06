from django.contrib import admin
from django.db.models import Count, Q
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from catalog.models import Category, ProductService, StockAdjustment


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('category_name', 'status_badge', 'get_product_count', 'get_service_count', 'created_at')
    list_filter = ('status',)
    search_fields = ('category_name', 'description')
    readonly_fields = ('created_at', 'updated_at')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _product_count=Count('products', filter=Q(products__item_type=ProductService.ItemType.PRODUCT)),
            _service_count=Count('products', filter=Q(products__item_type=ProductService.ItemType.SERVICE)),
        )

    @admin.display(description=_('Products'))
    def get_product_count(self, obj):
        return getattr(obj, '_product_count', 0)

    @admin.display(description=_('Services'))
    def get_service_count(self, obj):
        return getattr(obj, '_service_count', 0)

    @admin.display(description=_('Status'))
    def status_badge(self, obj):
        if obj.status == Category.Status.ACTIVE:
            return format_html('<span style="color: green; font-weight: bold;">{}</span>', obj.get_status_display())
        return format_html('<span style="color: gray;">{}</span>', obj.get_status_display())


@admin.register(ProductService)
class ProductServiceAdmin(admin.ModelAdmin):
    list_display = (
        'product_code', 'product_name', 'item_type', 'category',
        'unit_price', 'stock_quantity', 'reorder_level', 'stock_condition', 'status'
    )
    list_filter = ('item_type', 'status', 'category')
    search_fields = ('product_code', 'product_name', 'description', 'category__category_name')
    readonly_fields = ('product_code', 'created_at', 'updated_at')

    @admin.display(description=_('Stock Condition'))
    def stock_condition(self, obj):
        if obj.item_type == ProductService.ItemType.SERVICE:
            return format_html('<span style="color: #6c757d;">—</span>')
        if obj.is_out_of_stock:
            return format_html('<span style="color: #dc3545; font-weight: bold;">Out of Stock</span>')
        if obj.is_low_stock:
            return format_html('<span style="color: #ffc107; font-weight: bold;">Low Stock</span>')
        return format_html('<span style="color: #198754;">In Stock</span>')


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    """
    Read-only audit trail for stock adjustments.
    Prevents adding, editing, or deleting audit logs directly.
    """
    list_display = (
        'created_at', 'product', 'adjustment_type',
        'quantity_changed', 'quantity_before', 'quantity_after', 'user', 'reason'
    )
    list_filter = ('adjustment_type', 'created_at')
    search_fields = ('product__product_name', 'product__product_code', 'reason', 'user__username')
    readonly_fields = (
        'product', 'adjustment_type', 'quantity_changed',
        'quantity_before', 'quantity_after', 'reason', 'user', 'created_at'
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
