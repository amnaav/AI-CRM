from decimal import Decimal
from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from core.mixins import AdminRequiredMixin, StaffRequiredMixin, CustomerRequiredMixin
from accounts.models import CustomUser, CustomerProfile, SalesStaffProfile
from catalog.models import ProductService
from leads.models import Lead
from sales.models import Sale, Invoice, Payment
from reminders.models import Reminder
from activity_logs.models import ActivityLog


class DashboardIndexView(LoginRequiredMixin, View):
    def get(self, request):
        if request.user.is_admin:
            return redirect('dashboard:admin_dashboard')
        elif request.user.is_sales_staff:
            return redirect('dashboard:staff_dashboard')
        elif request.user.is_customer:
            return redirect('dashboard:customer_dashboard')
        else:
            return redirect('accounts:login')


class AdminDashboardView(AdminRequiredMixin, View):
    template_name = 'admin_portal/dashboard.html'

    def get(self, request):
        total_customers = CustomerProfile.objects.filter(is_active=True).count()
        total_staff = SalesStaffProfile.objects.count()
        active_leads = Lead.objects.exclude(status__in=['WON', 'LOST']).count()
        converted_leads = Lead.objects.filter(status='WON').count()
        total_sales_count = Sale.objects.exclude(status='CANCELLED').count()

        # Outstanding balances and overdue invoices
        invoices = Invoice.objects.exclude(invoice_status='CANCELLED')
        total_revenue = sum((inv.paid_amount for inv in invoices), 0)
        total_outstanding = sum((inv.balance_amount for inv in invoices), 0)
        overdue_invoices_count = invoices.filter(invoice_status='OVERDUE').count()
        pending_reminders_count = Reminder.objects.filter(status='PENDING').count()
        low_stock_count = ProductService.objects.filter(item_type='PRODUCT', stock_quantity__lte=5).count()

        recent_activities = ActivityLog.objects.select_related('user')[:8]
        recent_sales = Sale.objects.select_related('customer__user', 'sales_staff__user')[:6]

        context = {
            'page_title': 'Enterprise Admin Dashboard',
            'total_customers': total_customers,
            'total_staff': total_staff,
            'active_leads': active_leads,
            'converted_leads': converted_leads,
            'total_sales_count': total_sales_count,
            'total_revenue': total_revenue,
            'total_outstanding': total_outstanding,
            'overdue_invoices_count': overdue_invoices_count,
            'pending_reminders_count': pending_reminders_count,
            'low_stock_count': low_stock_count,
            'recent_activities': recent_activities,
            'recent_sales': recent_sales,
        }
        return render(request, self.template_name, context)


class StaffDashboardView(StaffRequiredMixin, View):
    template_name = 'staff_portal/dashboard.html'

    def get(self, request):
        staff_profile = getattr(request.user, 'staff_profile', None)
        assigned_customers = CustomerProfile.objects.filter(assigned_staff=staff_profile, is_active=True) if staff_profile else CustomerProfile.objects.none()
        assigned_leads = Lead.objects.filter(assigned_staff=staff_profile) if staff_profile else Lead.objects.none()
        high_priority_leads = assigned_leads.filter(ai_priority='HIGH').exclude(status__in=['WON', 'LOST'])

        pending_reminders = Reminder.objects.filter(assigned_to=request.user, status='PENDING')
        recent_sales = Sale.objects.filter(sales_staff=staff_profile).select_related('customer__user')[:5] if staff_profile else Sale.objects.none()

        context = {
            'page_title': 'Sales Staff Dashboard',
            'staff_profile': staff_profile,
            'assigned_customers_count': assigned_customers.count(),
            'assigned_leads_count': assigned_leads.count(),
            'high_priority_leads_count': high_priority_leads.count(),
            'pending_reminders_count': pending_reminders.count(),
            'recent_sales': recent_sales,
            'pending_reminders': pending_reminders[:5],
            'high_priority_leads': high_priority_leads[:5],
        }
        return render(request, self.template_name, context)


class CustomerDashboardView(CustomerRequiredMixin, View):
    template_name = 'customer_portal/dashboard.html'

    def get(self, request):
        # Strictly load through authenticated customer profile (Mandatory requirement 5)
        customer_profile = getattr(request.user, 'customer_profile', None)
        if not customer_profile:
            messages.error(request, "Customer profile not found.")
            return redirect('accounts:login')

        my_sales = Sale.objects.filter(customer=customer_profile).order_by('-sale_date')[:5]
        my_invoices = Invoice.objects.filter(customer=customer_profile).order_by('-invoice_date')[:5]
        my_payments = Payment.objects.filter(customer=customer_profile).order_by('-payment_date')[:5]
        from interactions.models import Interaction
        my_reminders = Reminder.objects.filter(customer=customer_profile, is_customer_visible=True, status='PENDING').order_by('due_date')[:5]
        my_interactions = Interaction.objects.filter(customer=customer_profile, is_customer_visible=True).order_by('-interaction_date')[:5]

        active_invoices = Invoice.objects.filter(customer=customer_profile).exclude(
            invoice_status=Invoice.InvoiceStatus.CANCELLED
        )
        total_spent = sum((inv.paid_amount for inv in active_invoices), Decimal('0.00'))
        total_balance = sum((inv.balance_amount for inv in active_invoices), Decimal('0.00'))

        context = {
            'page_title': 'My Customer Dashboard',
            'customer_profile': customer_profile,
            'total_spent': total_spent,
            'total_balance': total_balance,
            'my_sales': my_sales,
            'my_invoices': my_invoices,
            'my_payments': my_payments,
            'my_reminders': my_reminders,
            'my_interactions': my_interactions,
        }
        return render(request, self.template_name, context)
