from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, F, Count
from django.http import HttpResponseNotAllowed, Http404
from django.utils.translation import gettext_lazy as _

from core.mixins import AdminRequiredMixin, StaffRequiredMixin, CustomerRequiredMixin
from activity_logs.models import ActivityLog
from activity_logs.services import log_activity
from catalog.models import Category, ProductService, StockAdjustment
from catalog.forms import CategoryForm, ProductServiceForm, StockAdjustmentForm
from catalog.services import adjust_stock


# ==============================================================================
# CATEGORY MANAGEMENT VIEWS (ADMIN ONLY)
# ==============================================================================

class CategoryListView(AdminRequiredMixin, View):
    """
    Category directory with search, status filtering, product/service count annotations,
    and pagination.
    """
    template_name = 'catalog/category_list.html'

    def get(self, request):
        qs = Category.objects.annotate(
            product_count=Count('products', filter=Q(products__item_type=ProductService.ItemType.PRODUCT)),
            service_count=Count('products', filter=Q(products__item_type=ProductService.ItemType.SERVICE)),
            total_items=Count('products')
        ).order_by('category_name')

        search_query = request.GET.get('q', '').strip()
        if search_query:
            qs = qs.filter(
                Q(category_name__icontains=search_query) |
                Q(description__icontains=search_query)
            )

        status_filter = request.GET.get('status', '').strip()
        if status_filter in dict(Category.Status.choices):
            qs = qs.filter(status=status_filter)

        paginator = Paginator(qs, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        total_categories = Category.objects.count()
        active_categories = Category.objects.filter(status=Category.Status.ACTIVE).count()

        context = {
            'categories': page_obj,
            'page_obj': page_obj,
            'search_query': search_query,
            'status_filter': status_filter,
            'status_choices': Category.Status.choices,
            'total_categories': total_categories,
            'active_categories': active_categories,
            'page_title': _('Category Directory'),
            'breadcrumbs': [
                {'title': _('Catalog & Stock'), 'url': '/catalog/'},
                {'title': _('Categories'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class CategoryCreateView(AdminRequiredMixin, View):
    """Create a new category with case-insensitive unique validation."""
    template_name = 'catalog/category_form.html'

    def get(self, request):
        form = CategoryForm()
        context = {
            'form': form,
            'action': 'Create',
            'page_title': _('Create Category'),
            'breadcrumbs': [
                {'title': _('Catalog & Stock'), 'url': '/catalog/'},
                {'title': _('Categories'), 'url': '/catalog/categories/'},
                {'title': _('New Category'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)

    def post(self, request):
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save()
            log_activity(
                request=request,
                action=ActivityLog.Action.CREATE,
                module_name="Catalog - Category",
                record_id=str(category.pk),
                description=f"Created category: {category.category_name} (Status: {category.get_status_display()})"
            )
            messages.success(request, _('Category "%(name)s" created successfully.') % {'name': category.category_name})
            return redirect('catalog:category_list')

        context = {
            'form': form,
            'action': 'Create',
            'page_title': _('Create Category')
        }
        return render(request, self.template_name, context)


class CategoryDetailView(AdminRequiredMixin, View):
    """View category details and linked products/services."""
    template_name = 'catalog/category_detail.html'

    def get(self, request, pk):
        category = get_object_or_404(Category, pk=pk)
        items = category.products.all().order_by('product_name')

        product_count = items.filter(item_type=ProductService.ItemType.PRODUCT).count()
        service_count = items.filter(item_type=ProductService.ItemType.SERVICE).count()

        context = {
            'category': category,
            'items': items,
            'product_count': product_count,
            'service_count': service_count,
            'page_title': category.category_name,
            'breadcrumbs': [
                {'title': _('Catalog & Stock'), 'url': '/catalog/'},
                {'title': _('Categories'), 'url': '/catalog/categories/'},
                {'title': category.category_name, 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class CategoryUpdateView(AdminRequiredMixin, View):
    """Edit category name and description."""
    template_name = 'catalog/category_form.html'

    def get(self, request, pk):
        category = get_object_or_404(Category, pk=pk)
        form = CategoryForm(instance=category)
        context = {
            'form': form,
            'category': category,
            'action': 'Update',
            'page_title': _('Edit Category: %(name)s') % {'name': category.category_name},
            'breadcrumbs': [
                {'title': _('Catalog & Stock'), 'url': '/catalog/'},
                {'title': _('Categories'), 'url': '/catalog/categories/'},
                {'title': category.category_name, 'url': f'/catalog/categories/{category.pk}/'},
                {'title': _('Edit'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)

    def post(self, request, pk):
        category = get_object_or_404(Category, pk=pk)
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            category = form.save()
            log_activity(
                request=request,
                action=ActivityLog.Action.UPDATE,
                module_name="Catalog - Category",
                record_id=str(category.pk),
                description=f"Updated category: {category.category_name} (Status: {category.get_status_display()})"
            )
            messages.success(request, _('Category "%(name)s" updated successfully.') % {'name': category.category_name})
            return redirect('catalog:category_detail', pk=category.pk)

        context = {
            'form': form,
            'category': category,
            'action': 'Update',
            'page_title': _('Edit Category: %(name)s') % {'name': category.category_name}
        }
        return render(request, self.template_name, context)


class CategoryToggleStatusView(AdminRequiredMixin, View):
    """
    POST-only action to toggle category status between ACTIVE and INACTIVE.
    Does NOT modify or deactivate linked catalog items.
    """
    def post(self, request, pk):
        category = get_object_or_404(Category, pk=pk)
        if category.status == Category.Status.ACTIVE:
            category.status = Category.Status.INACTIVE
            status_label = _('deactivated')
        else:
            category.status = Category.Status.ACTIVE
            status_label = _('activated')

        category.save(update_fields=['status', 'updated_at'])

        log_activity(
            request=request,
            action=ActivityLog.Action.UPDATE,
            module_name="Catalog - Category",
            record_id=str(category.pk),
            description=f"Category '{category.category_name}' {status_label}. Linked items were untouched."
        )

        messages.success(request, _('Category "%(name)s" was %(status)s.') % {'name': category.category_name, 'status': status_label})
        next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/catalog/categories/'
        return redirect(next_url)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'], _('State-changing actions must use POST.'))


class CategoryDeleteView(AdminRequiredMixin, View):
    """
    POST-only action to delete a category.
    Permanently blocked if linked catalog items exist.
    """
    def post(self, request, pk):
        category = get_object_or_404(Category, pk=pk)
        if category.products.exists():
            messages.error(
                request,
                _('Cannot delete category "%(name)s" because it has linked products or services. Please deactivate it instead.') % {'name': category.category_name}
            )
            return redirect('catalog:category_detail', pk=category.pk)

        name = category.category_name
        category.delete()

        log_activity(
            request=request,
            action=ActivityLog.Action.DELETE,
            module_name="Catalog - Category",
            record_id=str(pk),
            description=f"Permanently deleted empty category '{name}'"
        )
        messages.success(request, _('Category "%(name)s" deleted successfully.') % {'name': name})
        return redirect('catalog:category_list')

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'], _('Deletion must use POST.'))


# ==============================================================================
# PRODUCT & SERVICE MANAGEMENT VIEWS (ADMIN & SALES STAFF)
# ==============================================================================

class ProductServiceListView(StaffRequiredMixin, View):
    """
    Catalog directory supporting search, type/category/status/stock filtering,
    sorting, pagination, and KPI summary stats.
    - Admin: sees active and inactive items, stock adjustment links, management buttons.
    - Sales Staff: sees ONLY active items with read-only prices and stock.
    """
    template_name = 'catalog/product_list.html'

    def get(self, request):
        user = request.user
        is_admin = user.is_admin

        # Base scoping
        if is_admin:
            qs = ProductService.objects.select_related('category').all()
        else:
            qs = ProductService.objects.select_related('category').filter(status=ProductService.Status.ACTIVE)

        # KPI summary calculation across active catalog
        active_items_qs = ProductService.objects.filter(status=ProductService.Status.ACTIVE)
        kpi_summary = {
            'total_active': active_items_qs.count(),
            'total_products': active_items_qs.filter(item_type=ProductService.ItemType.PRODUCT).count(),
            'total_services': active_items_qs.filter(item_type=ProductService.ItemType.SERVICE).count(),
            'low_stock': active_items_qs.filter(
                item_type=ProductService.ItemType.PRODUCT,
                stock_quantity__gt=0,
                stock_quantity__lte=F('reorder_level')
            ).count(),
            'out_of_stock': active_items_qs.filter(
                item_type=ProductService.ItemType.PRODUCT,
                stock_quantity=0
            ).count(),
        }

        # Search query
        search_query = request.GET.get('q', '').strip()
        if search_query:
            qs = qs.filter(
                Q(product_name__icontains=search_query) |
                Q(product_code__icontains=search_query) |
                Q(category__category_name__icontains=search_query) |
                Q(description__icontains=search_query)
            )

        # Filter by Item Type
        type_filter = request.GET.get('type', '').strip()
        if type_filter in dict(ProductService.ItemType.choices):
            qs = qs.filter(item_type=type_filter)

        # Filter by Category
        category_filter = request.GET.get('category', '').strip()
        if category_filter.isdigit():
            qs = qs.filter(category_id=int(category_filter))

        # Filter by Status (Admin only)
        status_filter = request.GET.get('status', '').strip()
        if is_admin and status_filter in dict(ProductService.Status.choices):
            qs = qs.filter(status=status_filter)

        # Filter by Stock Condition
        stock_filter = request.GET.get('stock', '').strip()
        if stock_filter == 'out_of_stock':
            qs = qs.filter(item_type=ProductService.ItemType.PRODUCT, stock_quantity=0)
        elif stock_filter == 'low_stock':
            qs = qs.filter(
                item_type=ProductService.ItemType.PRODUCT,
                stock_quantity__gt=0,
                stock_quantity__lte=F('reorder_level')
            )
        elif stock_filter == 'in_stock':
            qs = qs.filter(
                item_type=ProductService.ItemType.PRODUCT,
                stock_quantity__gt=F('reorder_level')
            )

        # Sorting
        sort_by = request.GET.get('sort', 'name_asc').strip()
        sort_map = {
            'name_asc': 'product_name',
            'name_desc': '-product_name',
            'code': 'product_code',
            'price_asc': 'unit_price',
            'price_desc': '-unit_price',
            'stock_asc': 'stock_quantity',
            'stock_desc': '-stock_quantity',
            'newest': '-created_at',
        }
        order_field = sort_map.get(sort_by, 'product_name')
        qs = qs.order_by(order_field)

        # Pagination (10 per page)
        paginator = Paginator(qs, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        categories = Category.objects.filter(status=Category.Status.ACTIVE).order_by('category_name')

        context = {
            'items': page_obj,
            'page_obj': page_obj,
            'kpi_summary': kpi_summary,
            'categories': categories,
            'search_query': search_query,
            'type_filter': type_filter,
            'category_filter': category_filter,
            'status_filter': status_filter,
            'stock_filter': stock_filter,
            'sort_by': sort_by,
            'is_admin': is_admin,
            'page_title': _('Catalog & Inventory') if is_admin else _('Products & Services'),
            'breadcrumbs': [
                {'title': _('Catalog & Stock'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class ProductServiceCreateView(AdminRequiredMixin, View):
    """Create a new Product or Service."""
    template_name = 'catalog/product_form.html'

    def get(self, request):
        form = ProductServiceForm()
        context = {
            'form': form,
            'action': 'Create',
            'page_title': _('Add Catalog Item'),
            'breadcrumbs': [
                {'title': _('Catalog & Stock'), 'url': '/catalog/'},
                {'title': _('New Item'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)

    def post(self, request):
        form = ProductServiceForm(request.POST, request.FILES)
        if form.is_valid():
            item = form.save()
            log_activity(
                request=request,
                action=ActivityLog.Action.CREATE,
                module_name="Catalog",
                record_id=item.product_code,
                description=f"Created {item.get_item_type_display()}: '{item.product_name}' ({item.product_code}) at ₹{item.unit_price}."
            )
            messages.success(request, _('%(type)s "%(name)s" created successfully.') % {
                'type': item.get_item_type_display(),
                'name': item.product_name
            })
            return redirect('catalog:product_detail', pk=item.pk)

        context = {
            'form': form,
            'action': 'Create',
            'page_title': _('Add Catalog Item')
        }
        return render(request, self.template_name, context)


class ProductServiceDetailView(StaffRequiredMixin, View):
    """
    Detailed item view.
    - Admin: sees full management actions, stock adjustments table, image preview.
    - Sales Staff: read-only active items; cannot see stock adjustments.
    """
    template_name = 'catalog/product_detail.html'

    def get(self, request, pk):
        item = get_object_or_404(ProductService.objects.select_related('category'), pk=pk)

        # Sales Staff restriction: cannot view inactive items
        if not request.user.is_admin and item.status != ProductService.Status.ACTIVE:
            raise Http404(_('Item not found or unavailable.'))

        is_admin = request.user.is_admin
        adjustments = []
        stock_form = None

        if is_admin:
            adjustments = item.stock_adjustments.select_related('user').all()[:25]
            if item.item_type == ProductService.ItemType.PRODUCT:
                stock_form = StockAdjustmentForm(product=item)

        context = {
            'item': item,
            'is_admin': is_admin,
            'adjustments': adjustments,
            'stock_form': stock_form,
            'page_title': f"{item.product_name} ({item.product_code})",
            'breadcrumbs': [
                {'title': _('Catalog & Stock'), 'url': '/catalog/'},
                {'title': item.product_name, 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class ProductServiceUpdateView(AdminRequiredMixin, View):
    """Edit product/service information."""
    template_name = 'catalog/product_form.html'

    def get(self, request, pk):
        item = get_object_or_404(ProductService, pk=pk)
        form = ProductServiceForm(instance=item)
        context = {
            'form': form,
            'item': item,
            'action': 'Update',
            'page_title': _('Edit Item: %(name)s') % {'name': item.product_name},
            'breadcrumbs': [
                {'title': _('Catalog & Stock'), 'url': '/catalog/'},
                {'title': item.product_name, 'url': f'/catalog/items/{item.pk}/'},
                {'title': _('Edit'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)

    def post(self, request, pk):
        item = get_object_or_404(ProductService, pk=pk)
        form = ProductServiceForm(request.POST, request.FILES, instance=item)
        if form.is_valid():
            item = form.save()
            log_activity(
                request=request,
                action=ActivityLog.Action.UPDATE,
                module_name="Catalog",
                record_id=item.product_code,
                description=f"Updated item: {item.product_name} ({item.product_code}). Unit price: ₹{item.unit_price}."
            )
            messages.success(request, _('Item "%(name)s" updated successfully.') % {'name': item.product_name})
            return redirect('catalog:product_detail', pk=item.pk)

        context = {
            'form': form,
            'item': item,
            'action': 'Update',
            'page_title': _('Edit Item: %(name)s') % {'name': item.product_name}
        }
        return render(request, self.template_name, context)


class ProductServiceToggleStatusView(AdminRequiredMixin, View):
    """
    POST-only action to toggle item status between ACTIVE and INACTIVE.
    """
    def post(self, request, pk):
        item = get_object_or_404(ProductService, pk=pk)
        if item.status == ProductService.Status.ACTIVE:
            item.status = ProductService.Status.INACTIVE
            label = _('deactivated')
        else:
            item.status = ProductService.Status.ACTIVE
            label = _('activated')

        item.save(update_fields=['status', 'updated_at'])

        log_activity(
            request=request,
            action=ActivityLog.Action.UPDATE,
            module_name="Catalog",
            record_id=item.product_code,
            description=f"Item {item.product_code} was {label}."
        )

        messages.success(request, _('Item "%(name)s" was %(status)s.') % {'name': item.product_name, 'status': label})
        next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or f'/catalog/items/{item.pk}/'
        return redirect(next_url)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'], _('State changes must use POST.'))


class ProductServiceStockAdjustView(AdminRequiredMixin, View):
    """
    POST-only controlled stock adjustment handler.
    Runs inside an atomic transaction with row locking.
    """
    def post(self, request, pk):
        product = get_object_or_404(ProductService, pk=pk)
        if product.item_type != ProductService.ItemType.PRODUCT:
            messages.error(request, _('Services do not carry inventory.'))
            return redirect('catalog:product_detail', pk=product.pk)

        form = StockAdjustmentForm(product=product, data=request.POST)
        if form.is_valid():
            qty_change = form.cleaned_data['quantity_changed']
            adj_type = form.cleaned_data['adjustment_type']
            reason = form.cleaned_data['reason']

            try:
                adjustment = adjust_stock(
                    product=product,
                    quantity_changed=qty_change,
                    adjustment_type=adj_type,
                    reason=reason,
                    user=request.user,
                    request=request,
                    is_manual=True
                )
                messages.success(
                    request,
                    _('Stock adjusted by %(qty)+d units. Current inventory: %(new_stock)d.') % {
                        'qty': qty_change,
                        'new_stock': adjustment.quantity_after
                    }
                )
            except ValidationError as e:
                messages.error(request, str(e.message if hasattr(e, 'message') else e))
            except Exception as e:
                messages.error(request, _('Failed to adjust stock: %(err)s') % {'err': str(e)})

            return redirect('catalog:product_detail', pk=product.pk)

        # Form has errors
        for field, errors in form.errors.items():
            for err in errors:
                messages.error(request, err)

        return redirect('catalog:product_detail', pk=product.pk)

    def get(self, request, pk):
        return redirect('catalog:product_detail', pk=pk)


class ProductServiceDeleteView(AdminRequiredMixin, View):
    """
    POST-only action to delete a catalog item.
    Permanently blocked if item has sales history or stock adjustment records.
    """
    def post(self, request, pk):
        item = get_object_or_404(ProductService, pk=pk)
        if item.has_sales_history:
            messages.error(
                request,
                _('Cannot delete "%(name)s" because it has recorded sales history. Please deactivate it instead.') % {'name': item.product_name}
            )
            return redirect('catalog:product_detail', pk=item.pk)

        if item.stock_adjustments.exists():
            messages.error(
                request,
                _('Cannot delete "%(name)s" because it has stock adjustment audit records. Please deactivate it instead.') % {'name': item.product_name}
            )
            return redirect('catalog:product_detail', pk=item.pk)

        name = item.product_name
        code = item.product_code
        item.delete()

        log_activity(
            request=request,
            action=ActivityLog.Action.DELETE,
            module_name="Catalog",
            record_id=code,
            description=f"Permanently deleted unused catalog item '{name}' ({code})"
        )
        messages.success(request, _('Catalog item "%(name)s" deleted successfully.') % {'name': name})
        return redirect('catalog:product_list')

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'], _('Deletion must use POST.'))


# ==============================================================================
# CUSTOMER PORTAL CATALOG VIEWS
# ==============================================================================

class CustomerCatalogListView(CustomerRequiredMixin, View):
    """
    Customer portal catalog.
    Displays only active items from active categories.
    No internal stock adjustment logs, reorder levels, or management links.
    """
    template_name = 'catalog/customer_catalog.html'

    def get(self, request):
        qs = ProductService.objects.select_related('category').filter(
            status=ProductService.Status.ACTIVE,
            category__status=Category.Status.ACTIVE
        )

        search_query = request.GET.get('q', '').strip()
        if search_query:
            qs = qs.filter(
                Q(product_name__icontains=search_query) |
                Q(description__icontains=search_query) |
                Q(category__category_name__icontains=search_query)
            )

        category_filter = request.GET.get('category', '').strip()
        if category_filter.isdigit():
            qs = qs.filter(category_id=int(category_filter))

        type_filter = request.GET.get('type', '').strip()
        if type_filter in dict(ProductService.ItemType.choices):
            qs = qs.filter(item_type=type_filter)

        qs = qs.order_by('product_name')
        paginator = Paginator(qs, 12)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        categories = Category.objects.filter(status=Category.Status.ACTIVE).order_by('category_name')

        context = {
            'items': page_obj,
            'page_obj': page_obj,
            'categories': categories,
            'search_query': search_query,
            'category_filter': category_filter,
            'type_filter': type_filter,
            'page_title': _('Product & Service Catalog'),
            'breadcrumbs': [
                {'title': _('Product Catalog'), 'url': ''}
            ]
        }
        return render(request, self.template_name, context)


class CustomerProductDetailView(CustomerRequiredMixin, View):
    """Customer view of a single active catalog item."""
    template_name = 'catalog/customer_product_detail.html'

    def get(self, request, pk):
        item = get_object_or_404(
            ProductService.objects.select_related('category'),
            pk=pk,
            status=ProductService.Status.ACTIVE,
            category__status=Category.Status.ACTIVE
        )

        context = {
            'item': item,
            'page_title': item.product_name,
            'breadcrumbs': [
                {'title': _('Catalog'), 'url': '/catalog/portal/'},
                {'title': item.product_name, 'url': ''}
            ]
        }
        return render(request, self.template_name, context)
