from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from core.mixins import AdminRequiredMixin
from accounts.models import CustomUser, CustomerProfile, SalesStaffProfile
from accounts.management_forms import (
    AdminCustomerCreateForm, AdminCustomerEditForm,
    StaffCustomerEditForm, CustomerAssignStaffForm
)
from activity_logs.services import log_activity


class CustomerListView(LoginRequiredMixin, View):
    template_name = 'accounts/customer_list.html'

    def get(self, request):
        user = request.user
        if user.is_customer:
            raise PermissionDenied("Customers cannot access customer management.")

        # Admin views all; Sales Staff views only assigned
        if user.is_admin:
            queryset = CustomerProfile.objects.select_related('user', 'assigned_staff__user').all()
        elif user.is_sales_staff and hasattr(user, 'staff_profile'):
            queryset = CustomerProfile.objects.select_related('user', 'assigned_staff__user').filter(
                assigned_staff=user.staff_profile
            )
        else:
            queryset = CustomerProfile.objects.none()

        # Search filter
        search_query = request.GET.get('q', '').strip()
        if search_query:
            queryset = queryset.filter(
                Q(user__full_name__icontains=search_query) |
                Q(user__email__icontains=search_query) |
                Q(user__phone__icontains=search_query) |
                Q(customer_code__icontains=search_query) |
                Q(company__icontains=search_query)
            )

        # Status filter
        status_filter = request.GET.get('status', '').strip()
        if status_filter in dict(CustomUser.Status.choices):
            queryset = queryset.filter(user__status=status_filter)

        # Customer type filter
        type_filter = request.GET.get('type', '').strip()
        if type_filter in dict(CustomerProfile.CustomerType.choices):
            queryset = queryset.filter(customer_type=type_filter)

        # Assigned staff filter (Admin only)
        staff_filter = request.GET.get('staff', '').strip()
        if staff_filter and user.is_admin:
            queryset = queryset.filter(assigned_staff_id=staff_filter)

        # Sorting
        sort_by = request.GET.get('sort', '-created_at')
        allowed_sorts = ['-created_at', 'created_at', 'user__full_name', '-user__full_name', 'company', 'customer_code']
        if sort_by in allowed_sorts:
            queryset = queryset.order_by(sort_by)
        else:
            queryset = queryset.order_by('-created_at')

        # Pagination
        paginator = Paginator(queryset, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        all_staff = SalesStaffProfile.objects.select_related('user').filter(user__status='ACTIVE') if user.is_admin else None

        context = {
            'page_title': 'Customer Directory',
            'page_obj': page_obj,
            'search_query': search_query,
            'status_filter': status_filter,
            'type_filter': type_filter,
            'staff_filter': staff_filter,
            'sort_by': sort_by,
            'all_staff': all_staff,
            'total_count': queryset.count(),
            'status_choices': CustomUser.Status.choices,
            'type_choices': CustomerProfile.CustomerType.choices,
        }
        return render(request, self.template_name, context)


class CustomerDetailView(LoginRequiredMixin, View):
    template_name = 'accounts/customer_detail.html'

    def get(self, request, pk):
        customer = get_object_or_404(CustomerProfile.objects.select_related('user', 'assigned_staff__user'), pk=pk)

        # Permission verification: Admin or strictly assigned staff
        if request.user.is_sales_staff:
            if not hasattr(request.user, 'staff_profile') or customer.assigned_staff != request.user.staff_profile:
                raise PermissionDenied("You are not authorized to view this customer's details.")
        elif not request.user.is_admin:
            raise PermissionDenied("Access denied.")

        # Related records
        sales = customer.sales.select_related('sales_staff__user').all()[:5]
        invoices = customer.invoices.all()[:5]
        interactions = customer.interactions.select_related('sales_staff__user').all()[:5]
        reminders = customer.reminders.all()[:5]

        assign_form = CustomerAssignStaffForm(instance=customer) if request.user.is_admin else None
        all_staff = SalesStaffProfile.objects.select_related('user').filter(user__status='ACTIVE') if request.user.is_admin else None

        context = {
            'page_title': f"Customer: {customer.user.full_name or customer.customer_code}",
            'customer': customer,
            'sales': sales,
            'invoices': invoices,
            'interactions': interactions,
            'reminders': reminders,
            'assign_form': assign_form,
            'all_staff': all_staff,
        }
        return render(request, self.template_name, context)


class CustomerCreateView(AdminRequiredMixin, View):
    template_name = 'accounts/customer_form.html'

    def get(self, request):
        form = AdminCustomerCreateForm()
        return render(request, self.template_name, {
            'page_title': 'Add New Customer',
            'form': form,
            'is_create': True,
        })

    def post(self, request):
        form = AdminCustomerCreateForm(request.POST)
        if form.is_valid():
            customer = form.save()
            log_activity(
                request,
                action='CREATE',
                module_name='CustomerManagement',
                record_id=str(customer.id),
                description=f"Admin created customer account {customer.customer_code} for {customer.user.full_name}"
            )
            messages.success(request, f"Customer {customer.user.full_name} ({customer.customer_code}) created successfully!")
            return redirect('accounts:customer_detail', pk=customer.pk)

        return render(request, self.template_name, {
            'page_title': 'Add New Customer',
            'form': form,
            'is_create': True,
        })


class CustomerUpdateView(LoginRequiredMixin, View):
    template_name = 'accounts/customer_form.html'

    def get_customer(self, request, pk):
        customer = get_object_or_404(CustomerProfile.objects.select_related('user', 'assigned_staff__user'), pk=pk)
        if request.user.is_sales_staff:
            if not hasattr(request.user, 'staff_profile') or customer.assigned_staff != request.user.staff_profile:
                raise PermissionDenied("You can only edit customers assigned to you.")
        elif not request.user.is_admin:
            raise PermissionDenied("Access denied.")
        return customer

    def get(self, request, pk):
        customer = self.get_customer(request, pk)
        if request.user.is_admin:
            form = AdminCustomerEditForm(instance=customer)
        else:
            form = StaffCustomerEditForm(instance=customer)

        return render(request, self.template_name, {
            'page_title': f"Edit Customer: {customer.user.full_name}",
            'form': form,
            'customer': customer,
            'is_create': False,
        })

    def post(self, request, pk):
        customer = self.get_customer(request, pk)
        if request.user.is_admin:
            form = AdminCustomerEditForm(request.POST, instance=customer)
        else:
            form = StaffCustomerEditForm(request.POST, instance=customer)

        if form.is_valid():
            form.save()
            log_activity(
                request,
                action='UPDATE',
                module_name='CustomerManagement',
                record_id=str(customer.id),
                description=f"User {request.user.username} updated customer {customer.customer_code}"
            )
            messages.success(request, f"Customer details for {customer.user.full_name} updated successfully.")
            return redirect('accounts:customer_detail', pk=customer.pk)

        return render(request, self.template_name, {
            'page_title': f"Edit Customer: {customer.user.full_name}",
            'form': form,
            'customer': customer,
            'is_create': False,
        })


class CustomerStatusToggleView(AdminRequiredMixin, View):
    """Admin endpoint to activate, deactivate, or lock a customer account. POST only."""
    http_method_names = ['post']

    def post(self, request, pk):
        customer = get_object_or_404(CustomerProfile.objects.select_related('user'), pk=pk)

        # Self-deactivation prevention
        if customer.user == request.user:
            messages.error(request, "You cannot deactivate or modify the status of your own account.")
            return redirect('accounts:customer_list')

        new_status = request.POST.get('status', '').strip().upper()
        if new_status in dict(CustomUser.Status.choices):
            old_status = customer.user.status

            # Synchronize custom status and Django is_active
            if new_status == CustomUser.Status.ACTIVE:
                customer.user.status = CustomUser.Status.ACTIVE
                customer.user.is_active = True
                customer.is_active = True
            else:
                customer.user.status = new_status
                customer.user.is_active = False
                customer.is_active = False

            customer.user.save(update_fields=['status', 'is_active'])
            customer.save(update_fields=['is_active'])

            # Record status changes in ActivityLog with acting Admin, affected account and old/new status
            log_activity(
                request,
                action='UPDATE',
                module_name='CustomerManagement',
                record_id=str(customer.id),
                description=(
                    f"Admin '{request.user.username}' changed status of customer "
                    f"'{customer.customer_code}' ({customer.user.full_name}) from '{old_status}' to '{new_status}'."
                )
            )
            messages.success(request, f"Customer account status changed to {customer.user.get_status_display()}.")
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
        return redirect('accounts:customer_list')


class CustomerAssignStaffView(AdminRequiredMixin, View):
    """Admin endpoint to assign or reassign sales staff to a customer."""
    def post(self, request, pk):
        customer = get_object_or_404(CustomerProfile, pk=pk)
        staff_id = request.POST.get('assigned_staff', '').strip()

        if staff_id:
            staff = get_object_or_404(SalesStaffProfile, pk=staff_id)
            customer.assigned_staff = staff
            staff_name = staff.user.full_name
        else:
            customer.assigned_staff = None
            staff_name = "Unassigned"

        customer.save(update_fields=['assigned_staff'])
        log_activity(
            request,
            action='UPDATE',
            module_name='CustomerManagement',
            record_id=str(customer.id),
            description=f"Admin assigned customer {customer.customer_code} to staff {staff_name}"
        )
        messages.success(request, f"Customer successfully assigned to {staff_name}.")
        return redirect('accounts:customer_detail', pk=customer.pk)


class CustomerDeleteView(AdminRequiredMixin, View):
    """
    Admin-only customer soft-deactivation endpoint.
    All customers are soft-deactivated to protect CRM referential history.
    """
    template_name = 'accounts/customer_confirm_delete.html'

    def get(self, request, pk):
        customer = get_object_or_404(CustomerProfile.objects.select_related('user'), pk=pk)
        return render(request, self.template_name, {
            'page_title': f"Deactivate Customer: {customer.user.full_name}",
            'customer': customer,
            'has_history': True,
        })

    def post(self, request, pk):
        customer = get_object_or_404(CustomerProfile.objects.select_related('user'), pk=pk)

        # Self-deactivation prevention
        if customer.user == request.user:
            messages.error(request, "You cannot deactivate your own account.")
            return redirect('accounts:customer_list')

        old_status = customer.user.status

        # Synchronize status and is_active on soft-deactivation
        customer.is_active = False
        customer.user.status = CustomUser.Status.INACTIVE
        customer.user.is_active = False
        customer.user.save(update_fields=['status', 'is_active'])
        customer.save(update_fields=['is_active'])

        log_activity(
            request,
            action='UPDATE',
            module_name='CustomerManagement',
            record_id=str(customer.id),
            description=(
                f"Admin '{request.user.username}' deactivated customer '{customer.customer_code}' "
                f"({customer.user.full_name}) from '{old_status}' to 'INACTIVE'."
            )
        )
        messages.warning(
            request,
            f"Customer account {customer.user.full_name} ({customer.customer_code}) has been deactivated. "
            "All CRM records, history, and associations are preserved."
        )
        return redirect('accounts:customer_list')
