from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from core.mixins import AdminRequiredMixin
from accounts.models import CustomUser, SalesStaffProfile
from accounts.management_forms import AdminStaffCreateForm, AdminStaffEditForm
from activity_logs.services import log_activity


class StaffListView(AdminRequiredMixin, View):
    template_name = 'accounts/staff_list.html'

    def get(self, request):
        queryset = SalesStaffProfile.objects.select_related('user').all()

        # Search filter
        search_query = request.GET.get('q', '').strip()
        if search_query:
            queryset = queryset.filter(
                Q(user__full_name__icontains=search_query) |
                Q(user__email__icontains=search_query) |
                Q(user__phone__icontains=search_query) |
                Q(staff_code__icontains=search_query) |
                Q(designation__icontains=search_query) |
                Q(department__icontains=search_query)
            )

        # Status filter
        status_filter = request.GET.get('status', '').strip()
        if status_filter in dict(CustomUser.Status.choices):
            queryset = queryset.filter(user__status=status_filter)

        # Sorting
        sort_by = request.GET.get('sort', '-created_at')
        allowed_sorts = ['-created_at', 'created_at', 'user__full_name', '-user__full_name', 'designation', 'staff_code']
        if sort_by in allowed_sorts:
            queryset = queryset.order_by(sort_by)
        else:
            queryset = queryset.order_by('-created_at')

        # Pagination
        paginator = Paginator(queryset, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'page_title': 'Sales Staff Directory',
            'page_obj': page_obj,
            'search_query': search_query,
            'status_filter': status_filter,
            'sort_by': sort_by,
            'total_count': queryset.count(),
            'status_choices': CustomUser.Status.choices,
        }
        return render(request, self.template_name, context)


class StaffDetailView(LoginRequiredMixin, View):
    template_name = 'accounts/staff_detail.html'

    def get(self, request, pk):
        staff = get_object_or_404(SalesStaffProfile.objects.select_related('user'), pk=pk)

        # Permission check: Admin or Staff viewing their own profile
        if request.user.is_sales_staff:
            if not hasattr(request.user, 'staff_profile') or request.user.staff_profile != staff:
                raise PermissionDenied("You can only view your own staff profile.")
        elif not request.user.is_admin:
            raise PermissionDenied("Access denied.")

        assigned_customers = staff.assigned_customers.select_related('user').all()[:10]
        assigned_leads = staff.leads.all()[:10]
        recent_sales = staff.sales_handled.select_related('customer__user').all()[:10]

        # Target & Performance metrics
        total_sales_revenue = sum((s.net_amount for s in staff.sales_handled.exclude(status='CANCELLED')), Decimal('0.00'))
        target = staff.assigned_target if staff.assigned_target > 0 else Decimal('1.00')
        target_progress_pct = min(round((total_sales_revenue / target) * 100, 1), 100.0) if staff.assigned_target > 0 else 0.0

        context = {
            'page_title': f"Staff Profile: {staff.user.full_name or staff.staff_code}",
            'staff': staff,
            'assigned_customers': assigned_customers,
            'assigned_customers_count': staff.assigned_customers.count(),
            'assigned_leads': assigned_leads,
            'assigned_leads_count': staff.leads.count(),
            'recent_sales': recent_sales,
            'total_sales_count': staff.sales_handled.count(),
            'total_sales_revenue': total_sales_revenue,
            'target_progress_pct': target_progress_pct,
        }
        return render(request, self.template_name, context)


class StaffCreateView(AdminRequiredMixin, View):
    template_name = 'accounts/staff_form.html'

    def get(self, request):
        form = AdminStaffCreateForm()
        return render(request, self.template_name, {
            'page_title': 'Add New Sales Staff',
            'form': form,
            'is_create': True,
        })

    def post(self, request):
        form = AdminStaffCreateForm(request.POST)
        if form.is_valid():
            staff = form.save()
            log_activity(
                request,
                action='CREATE',
                module_name='StaffManagement',
                record_id=str(staff.id),
                description=f"Admin created sales staff account {staff.staff_code} for {staff.user.full_name}"
            )
            messages.success(request, f"Sales staff {staff.user.full_name} ({staff.staff_code}) registered successfully!")
            return redirect('accounts:staff_detail', pk=staff.pk)

        return render(request, self.template_name, {
            'page_title': 'Add New Sales Staff',
            'form': form,
            'is_create': True,
        })


class StaffUpdateView(AdminRequiredMixin, View):
    template_name = 'accounts/staff_form.html'

    def get(self, request, pk):
        staff = get_object_or_404(SalesStaffProfile.objects.select_related('user'), pk=pk)
        form = AdminStaffEditForm(instance=staff)
        return render(request, self.template_name, {
            'page_title': f"Edit Staff: {staff.user.full_name}",
            'form': form,
            'staff': staff,
            'is_create': False,
        })

    def post(self, request, pk):
        staff = get_object_or_404(SalesStaffProfile.objects.select_related('user'), pk=pk)
        form = AdminStaffEditForm(request.POST, instance=staff)
        if form.is_valid():
            form.save()
            log_activity(
                request,
                action='UPDATE',
                module_name='StaffManagement',
                record_id=str(staff.id),
                description=f"Admin updated staff profile {staff.staff_code} ({staff.user.full_name})"
            )
            messages.success(request, f"Staff details for {staff.user.full_name} updated successfully.")
            return redirect('accounts:staff_detail', pk=staff.pk)

        return render(request, self.template_name, {
            'page_title': f"Edit Staff: {staff.user.full_name}",
            'form': form,
            'staff': staff,
            'is_create': False,
        })


class StaffStatusToggleView(AdminRequiredMixin, View):
    """Admin endpoint to activate, deactivate, or lock a sales staff account. POST only."""
    http_method_names = ['post']

    def post(self, request, pk):
        staff = get_object_or_404(SalesStaffProfile.objects.select_related('user'), pk=pk)

        # Self-deactivation prevention
        if staff.user == request.user:
            messages.error(request, "You cannot deactivate or modify the status of your own account.")
            return redirect('accounts:staff_list')

        new_status = request.POST.get('status', '').strip().upper()
        if new_status in dict(CustomUser.Status.choices):
            old_status = staff.user.status

            # Synchronize custom status and Django is_active
            if new_status == CustomUser.Status.ACTIVE:
                staff.user.status = CustomUser.Status.ACTIVE
                staff.user.is_active = True
            else:
                staff.user.status = new_status
                staff.user.is_active = False

            staff.user.save(update_fields=['status', 'is_active'])

            # Record status changes in ActivityLog with acting Admin, affected account and old/new status
            log_activity(
                request,
                action='UPDATE',
                module_name='StaffManagement',
                record_id=str(staff.id),
                description=(
                    f"Admin '{request.user.username}' changed status of sales staff account "
                    f"'{staff.staff_code}' ({staff.user.full_name}) from '{old_status}' to '{new_status}'."
                )
            )
            messages.success(request, f"Staff account status changed to {staff.user.get_status_display()}.")
        else:
            messages.error(request, "Invalid status choice.")

        # Validate next redirect using url_has_allowed_host_and_scheme
        next_url = request.POST.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect('accounts:staff_list')


class StaffDeleteView(AdminRequiredMixin, View):
    """
    Admin-only sales staff soft-deactivation endpoint.
    All staff accounts are soft-deactivated to protect CRM referential history.
    """
    template_name = 'accounts/staff_confirm_delete.html'

    def get(self, request, pk):
        staff = get_object_or_404(SalesStaffProfile.objects.select_related('user'), pk=pk)
        return render(request, self.template_name, {
            'page_title': f"Deactivate Staff: {staff.user.full_name}",
            'staff': staff,
            'has_history': True,
        })

    def post(self, request, pk):
        staff = get_object_or_404(SalesStaffProfile.objects.select_related('user'), pk=pk)

        # Self-deactivation prevention
        if staff.user == request.user:
            messages.error(request, "You cannot deactivate your own account.")
            return redirect('accounts:staff_list')

        old_status = staff.user.status

        # Synchronize status and is_active on soft-deactivation
        staff.user.status = CustomUser.Status.INACTIVE
        staff.user.is_active = False
        staff.user.save(update_fields=['status', 'is_active'])

        log_activity(
            request,
            action='UPDATE',
            module_name='StaffManagement',
            record_id=str(staff.id),
            description=(
                f"Admin '{request.user.username}' deactivated sales staff '{staff.staff_code}' "
                f"({staff.user.full_name}) from '{old_status}' to 'INACTIVE'."
            )
        )
        messages.warning(
            request,
            f"Staff account {staff.user.full_name} ({staff.staff_code}) has been deactivated. "
            "All assigned accounts, leads, and sales history are preserved."
        )
        return redirect('accounts:staff_list')
