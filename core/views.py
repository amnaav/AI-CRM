from django.shortcuts import render, redirect
from django.views import View
from catalog.models import ProductService, Category


class LandingPageView(View):
    """
    Public Home/Landing Page accessible to both anonymous and authenticated users.
    Displays:
    - Peacock Blue & Copper brand identity and hero section
    - Three role-benefit cards (Administrator, Sales Staff, Customer)
    - Core CRM module capabilities & factual AI explanations
    - Safe public catalog preview (capped at 6 items, active only, zero stock exposure)
    - About & configurable contact information
    """
    template_name = 'landing.html'

    def get(self, request):
        # Query only active items from active categories with deterministic ordering
        preview_items = list(
            ProductService.objects.select_related('category').filter(
                status=ProductService.Status.ACTIVE,
                category__status=Category.Status.ACTIVE
            ).order_by('product_name')[:6]
        )

        # Attach safe availability labels without exposing numeric stock amounts
        for item in preview_items:
            if item.item_type == ProductService.ItemType.SERVICE:
                item.public_availability = "Available"
                item.public_availability_class = "badge-available"
            else:
                if item.stock_quantity > item.reorder_level:
                    item.public_availability = "Available"
                    item.public_availability_class = "badge-available"
                elif 0 < item.stock_quantity <= item.reorder_level:
                    item.public_availability = "Limited Availability"
                    item.public_availability_class = "badge-limited"
                else:
                    item.public_availability = "Out of Stock"
                    item.public_availability_class = "badge-out-of-stock"

        # Resolve role-based dashboard destination for authenticated visitors
        dashboard_url = None
        role_label = None
        if request.user.is_authenticated:
            if request.user.is_admin:
                dashboard_url = 'dashboard:admin_dashboard'
                role_label = 'Administrator'
            elif request.user.is_sales_staff:
                dashboard_url = 'dashboard:staff_dashboard'
                role_label = 'Sales Staff'
            elif request.user.is_customer:
                dashboard_url = 'dashboard:customer_dashboard'
                role_label = 'Customer'
            else:
                dashboard_url = 'dashboard:index'
                role_label = 'User'

        context = {
            'page_title': 'Intelligent Customer Relationships. Smarter Business Growth.',
            'preview_items': preview_items,
            'dashboard_url': dashboard_url,
            'role_label': role_label,
            'is_landing': True,
        }
        return render(request, self.template_name, context)


def home_redirect(request):
    """Legacy redirector preserved for backwards compatibility."""
    if request.user.is_authenticated:
        return redirect('dashboard:index')
    return redirect('accounts:login')


def error_400(request, exception=None):
    return render(request, 'errors/400.html', status=400)


def error_403(request, exception=None):
    return render(request, 'errors/403.html', status=403)


def error_404(request, exception=None):
    return render(request, 'errors/404.html', status=404)


def error_500(request):
    return render(request, 'errors/500.html', status=500)
