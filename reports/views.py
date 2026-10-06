import calendar
import datetime
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from accounts.models import CustomerProfile, CustomUser, SalesStaffProfile
from activity_logs.models import ActivityLog
from catalog.models import Category, ProductService
from core.mixins import AdminRequiredMixin, StaffRequiredMixin
from interactions.models import Interaction
from leads.models import Lead
from reminders.models import Reminder
from reports.services.analytics_service import (
    calculate_admin_kpis,
    calculate_staff_kpis,
    get_crm_timezone,
    get_eligible_sales,
    parse_date_range,
    to_timezone_datetimes,
)
from reports.services.export_service import (
    generate_csv_response,
    generate_excel_response,
    generate_pdf_response,
)
from reports.services.forecast_service import generate_sales_forecast
from sales.models import Invoice, Payment, Sale, SaleItem


# -------------------------------------------------------------------------
# Hub & Navigation Views
# -------------------------------------------------------------------------

class ReportsHomeView(StaffRequiredMixin, View):
    """
    Central catalog/hub showing cards for all permitted reports and analytics dashboards.
    """
    template_name = 'reports/index.html'

    def get(self, request):
        is_admin = request.user.is_admin
        context = {
            'page_title': 'Reports & Business Intelligence',
            'is_admin': is_admin,
            'user_staff_profile': getattr(request.user, 'staff_profile', None),
        }
        return render(request, self.template_name, context)


class AdminAnalyticsView(AdminRequiredMixin, View):
    """
    Enterprise Executive Analytics Dashboard.
    Provides company-wide KPIs, interactive Chart.js charts, and date filters.
    """
    template_name = 'reports/analytics_admin.html'

    def get(self, request):
        preset = request.GET.get('preset', 'all')
        start_str = request.GET.get('start_date')
        end_str = request.GET.get('end_date')

        start_date, end_date, active_preset, err_msg = parse_date_range(preset, start_str, end_str)
        if err_msg:
            messages.error(request, err_msg)

        kpis = calculate_admin_kpis(start_date=start_date, end_date=end_date)

        # Prepare Chart.js JSON payloads
        revenue_trend_chart = {
            'labels': kpis['revenue_trend_labels'],
            'data': kpis['revenue_trend_data'],
        }

        lead_pipeline_chart = {
            'labels': [str(k) for k in kpis['lead_pipeline_distribution'].keys()],
            'data': list(kpis['lead_pipeline_distribution'].values()),
        }

        sales_status_chart = {
            'labels': [str(k) for k in kpis['sales_by_status'].keys()],
            'data': list(kpis['sales_by_status'].values()),
        }

        payment_states_chart = {
            'labels': [str(k) for k in kpis['invoice_payment_states'].keys()],
            'data': list(kpis['invoice_payment_states'].values()),
        }

        staff_revenue_chart = {
            'labels': [row['staff_name'] for row in kpis['staff_revenue_list']],
            'data': [float(row['total_revenue']) for row in kpis['staff_revenue_list']],
        }

        context = {
            'page_title': 'Executive Analytics Dashboard',
            'kpis': kpis,
            'active_preset': active_preset,
            'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
            'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
            'revenue_trend_chart': revenue_trend_chart,
            'lead_pipeline_chart': lead_pipeline_chart,
            'sales_status_chart': sales_status_chart,
            'payment_states_chart': payment_states_chart,
            'staff_revenue_chart': staff_revenue_chart,
        }
        return render(request, self.template_name, context)


class StaffAnalyticsView(StaffRequiredMixin, View):
    """
    Sales Staff Personal Analytics Dashboard.
    Strictly isolated: Sales Staff can ONLY view their own personal performance.
    """
    template_name = 'reports/analytics_staff.html'

    def get(self, request):
        is_admin = request.user.is_admin

        if is_admin:
            staff_id = request.GET.get('staff_id')
            if staff_id:
                target_staff = get_object_or_404(SalesStaffProfile, id=staff_id)
            else:
                target_staff = SalesStaffProfile.objects.first()
                if not target_staff:
                    messages.warning(request, "No sales staff profiles exist in the system yet.")
                    return redirect('reports:index')
        else:
            # Sales staff ALWAYS strictly sees their own profile, ignoring any staff_id param
            target_staff = getattr(request.user, 'staff_profile', None)
            if not target_staff:
                messages.error(request, "Sales Staff profile not found.")
                return redirect('dashboard:index')

        preset = request.GET.get('preset', 'all')
        start_str = request.GET.get('start_date')
        end_str = request.GET.get('end_date')

        start_date, end_date, active_preset, err_msg = parse_date_range(preset, start_str, end_str)
        if err_msg:
            messages.error(request, err_msg)

        kpis = calculate_staff_kpis(target_staff, start_date=start_date, end_date=end_date)

        performance_trend_chart = {
            'labels': kpis['performance_trend_labels'],
            'data': kpis['performance_trend_data'],
        }

        lead_status_chart = {
            'labels': [str(k) for k in kpis['assigned_leads_by_status'].keys()],
            'data': list(kpis['assigned_leads_by_status'].values()),
        }

        all_staff = SalesStaffProfile.objects.select_related('user').all() if is_admin else None

        context = {
            'page_title': f"Personal Analytics: {target_staff.user.get_full_name() or target_staff.user.username}",
            'target_staff': target_staff,
            'all_staff': all_staff,
            'kpis': kpis,
            'active_preset': active_preset,
            'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
            'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
            'performance_trend_chart': performance_trend_chart,
            'lead_status_chart': lead_status_chart,
        }
        return render(request, self.template_name, context)


class SalesForecastView(StaffRequiredMixin, View):
    """
    Explainable AI Sales Forecasting Dashboard.
    Uses Scikit-Learn Linear Regression with 6-month threshold,
    deterministic moving average fallback, zero-clamping, and plain-language explainability.
    """
    template_name = 'reports/sales_forecast.html'

    def get(self, request):
        is_admin = request.user.is_admin
        target_staff = None

        if is_admin:
            staff_id = request.GET.get('staff_id')
            if staff_id:
                target_staff = get_object_or_404(SalesStaffProfile, id=staff_id)
        else:
            target_staff = getattr(request.user, 'staff_profile', None)

        forecast_data = generate_sales_forecast(staff_profile=target_staff)

        # Prepare Chart.js dataset combining historical points and future forecast
        history_labels = [p['label'] for p in forecast_data['historical_points']]
        history_values = [p['revenue'] for p in forecast_data['historical_points']]

        future_labels = [p['label'] for p in forecast_data['predictions']]
        future_values = [float(p['forecast_value']) for p in forecast_data['predictions']]

        # For seamless line connection, start forecast line from last historical point
        if history_values:
            connected_forecast_data = [None] * (len(history_values) - 1) + [history_values[-1]] + future_values
            combined_labels = history_labels + future_labels
            combined_history_series = history_values + [None] * len(future_values)
        else:
            connected_forecast_data = future_values
            combined_labels = future_labels
            combined_history_series = [None] * len(future_values)

        forecast_chart = {
            'labels': combined_labels,
            'history_data': combined_history_series,
            'forecast_data': connected_forecast_data,
        }

        all_staff = SalesStaffProfile.objects.select_related('user').all() if is_admin else None

        context = {
            'page_title': 'Explainable AI Sales Forecasting',
            'forecast': forecast_data,
            'forecast_chart': forecast_chart,
            'target_staff': target_staff,
            'all_staff': all_staff,
            'is_admin': is_admin,
        }
        return render(request, self.template_name, context)


# -------------------------------------------------------------------------
# 1. Customer Directory Report
# -------------------------------------------------------------------------

class CustomerReportView(StaffRequiredMixin, View):
    template_name = 'reports/customer_report.html'

    def get(self, request):
        is_admin = request.user.is_admin
        qs = CustomerProfile.objects.select_related('user', 'assigned_staff__user')

        # Role scoping
        if not is_admin:
            staff_prof = getattr(request.user, 'staff_profile', None)
            qs = qs.filter(assigned_staff=staff_prof)
        else:
            staff_filter = request.GET.get('staff_id')
            if staff_filter:
                qs = qs.filter(assigned_staff_id=staff_filter)

        # Search filter
        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(customer_code__icontains=q) |
                Q(user__full_name__icontains=q) |
                Q(company__icontains=q) |
                Q(user__email__icontains=q) |
                Q(user__phone__icontains=q)
            )

        # Status filter
        status_filter = request.GET.get('status')
        if status_filter == 'active':
            qs = qs.filter(is_active=True)
        elif status_filter == 'inactive':
            qs = qs.filter(is_active=False)

        # Date filter
        start_date, end_date, active_preset, err_msg = parse_date_range(
            request.GET.get('preset'), request.GET.get('start_date'), request.GET.get('end_date')
        )
        if err_msg:
            messages.error(request, err_msg)
        start_dt, end_dt = to_timezone_datetimes(start_date, end_date)
        if start_dt:
            qs = qs.filter(created_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(created_at__lte=end_dt)

        # Stable deterministic ordering
        qs = qs.order_by('-created_at', '-id')

        export_fmt = request.GET.get('export')
        if export_fmt in ('csv', 'xlsx', 'pdf'):
            headers = [
                'Customer Code', 'Full Name', 'Company', 'Customer Type', 'Email',
                'Phone', 'City', 'State', 'Assigned Staff', 'Status', 'Joined Date'
            ]
            rows = []
            for c in qs:
                staff_name = c.assigned_staff.user.get_full_name() if c.assigned_staff else "Unassigned"
                rows.append([
                    c.customer_code,
                    c.user.get_full_name() or c.user.username,
                    c.company or "-",
                    c.get_customer_type_display(),
                    c.user.email,
                    c.user.phone or "-",
                    c.city or "-",
                    c.state or "-",
                    staff_name,
                    "Active" if c.is_active else "Inactive",
                    c.created_at.strftime('%Y-%m-%d')
                ])

            applied = {
                'Preset': active_preset,
                'Start Date': start_date,
                'End Date': end_date,
                'Search': q,
                'Status': status_filter,
                'Staff Scope': "Assigned Only" if not is_admin else (request.GET.get('staff_id') or "All"),
            }
            if export_fmt == 'csv':
                return generate_csv_response("Customer Directory Report", headers, rows, "customer_directory", request, applied)
            elif export_fmt == 'xlsx':
                return generate_excel_response("Customer Directory Report", headers, rows, "customer_directory", request, applied)
            else:
                return generate_pdf_response("Customer Directory Report", headers, rows, "customer_directory", request, applied)

        paginator = Paginator(qs, 25)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'page_title': 'Customer Directory Report',
            'page_obj': page_obj,
            'total_count': qs.count(),
            'active_preset': active_preset,
            'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
            'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
            'q': q,
            'status_filter': status_filter or '',
            'all_staff': SalesStaffProfile.objects.select_related('user').all() if is_admin else None,
            'selected_staff': request.GET.get('staff_id', ''),
        }
        return render(request, self.template_name, context)


# -------------------------------------------------------------------------
# 2. Lead Pipeline Report
# -------------------------------------------------------------------------

class LeadReportView(StaffRequiredMixin, View):
    template_name = 'reports/lead_report.html'

    def get(self, request):
        is_admin = request.user.is_admin
        qs = Lead.objects.select_related('assigned_staff__user')

        # Role scoping
        if not is_admin:
            staff_prof = getattr(request.user, 'staff_profile', None)
            qs = qs.filter(assigned_staff=staff_prof)
        else:
            staff_filter = request.GET.get('staff_id')
            if staff_filter:
                qs = qs.filter(assigned_staff_id=staff_filter)

        # Status filter
        status_filter = request.GET.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        # Search filter
        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(lead_code__icontains=q) |
                Q(full_name__icontains=q) |
                Q(email__icontains=q) |
                Q(company__icontains=q)
            )

        # Date filter
        start_date, end_date, active_preset, err_msg = parse_date_range(
            request.GET.get('preset'), request.GET.get('start_date'), request.GET.get('end_date')
        )
        if err_msg:
            messages.error(request, err_msg)
        start_dt, end_dt = to_timezone_datetimes(start_date, end_date)
        if start_dt:
            qs = qs.filter(created_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(created_at__lte=end_dt)

        qs = qs.order_by('-created_at', '-id')

        export_fmt = request.GET.get('export')
        if export_fmt in ('csv', 'xlsx', 'pdf'):
            headers = [
                'Lead Code', 'Full Name', 'Company', 'Source', 'Status', 'Priority',
                'Estimated Value', 'Assigned Staff', 'AI Priority', 'AI Score', 'Created Date'
            ]
            rows = []
            for ld in qs:
                staff_name = ld.assigned_staff.user.get_full_name() if ld.assigned_staff else "Unassigned"
                rows.append([
                    ld.lead_code,
                    ld.full_name,
                    ld.company or "-",
                    ld.get_source_display(),
                    ld.get_status_display(),
                    ld.get_ai_priority_display(),
                    ld.estimated_value or Decimal('0.00'),
                    staff_name,
                    ld.ai_priority,
                    ld.probability_score or Decimal('0.00'),
                    ld.created_at.strftime('%Y-%m-%d')
                ])

            applied = {
                'Preset': active_preset,
                'Start Date': start_date,
                'End Date': end_date,
                'Status': status_filter,
                'Search': q,
                'Staff Scope': "Assigned Only" if not is_admin else (request.GET.get('staff_id') or "All"),
            }
            if export_fmt == 'csv':
                return generate_csv_response("Lead Pipeline Report", headers, rows, "lead_pipeline", request, applied)
            elif export_fmt == 'xlsx':
                return generate_excel_response("Lead Pipeline Report", headers, rows, "lead_pipeline", request, applied)
            else:
                return generate_pdf_response("Lead Pipeline Report", headers, rows, "lead_pipeline", request, applied)

        paginator = Paginator(qs, 25)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'page_title': 'Lead Pipeline Report',
            'page_obj': page_obj,
            'total_count': qs.count(),
            'status_choices': Lead.Status.choices,
            'selected_status': status_filter or '',
            'active_preset': active_preset,
            'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
            'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
            'q': q,
            'all_staff': SalesStaffProfile.objects.select_related('user').all() if is_admin else None,
            'selected_staff': request.GET.get('staff_id', ''),
        }
        return render(request, self.template_name, context)


# -------------------------------------------------------------------------
# 3. Sales Orders & Line Items Report
# -------------------------------------------------------------------------

class SalesReportView(StaffRequiredMixin, View):
    template_name = 'reports/sales_report.html'

    def get(self, request):
        is_admin = request.user.is_admin
        qs = Sale.objects.select_related('customer__user', 'sales_staff__user')

        if not is_admin:
            staff_prof = getattr(request.user, 'staff_profile', None)
            qs = qs.filter(sales_staff=staff_prof)
        else:
            staff_filter = request.GET.get('staff_id')
            if staff_filter:
                qs = qs.filter(sales_staff_id=staff_filter)

        status_filter = request.GET.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(sale_number__icontains=q) |
                Q(customer__user__full_name__icontains=q) |
                Q(customer__company__icontains=q)
            )

        start_date, end_date, active_preset, err_msg = parse_date_range(
            request.GET.get('preset'), request.GET.get('start_date'), request.GET.get('end_date')
        )
        if err_msg:
            messages.error(request, err_msg)
        if start_date:
            qs = qs.filter(sale_date__gte=start_date)
        if end_date:
            qs = qs.filter(sale_date__lte=end_date)

        qs = qs.order_by('-sale_date', '-id')

        export_fmt = request.GET.get('export')
        if export_fmt in ('csv', 'xlsx', 'pdf'):
            headers = [
                'Sale Number', 'Customer', 'Sales Staff', 'Sale Date', 'Status',
                'Subtotal (₹)', 'Discount (₹)', 'Taxable (₹)', 'Tax Rate (%)', 'Tax (₹)', 'Net Amount (₹)'
            ]
            rows = []
            for s in qs:
                cust_label = s.customer.company or s.customer.user.get_full_name()
                staff_label = s.sales_staff.user.get_full_name() if s.sales_staff else "Unassigned"
                rows.append([
                    s.sale_number,
                    cust_label,
                    staff_label,
                    s.sale_date.strftime('%Y-%m-%d'),
                    s.get_status_display(),
                    s.subtotal,
                    s.discount,
                    s.taxable_amount,
                    s.tax_rate,
                    s.tax_amount,
                    s.net_amount,
                ])

            applied = {
                'Preset': active_preset,
                'Start Date': start_date,
                'End Date': end_date,
                'Status': status_filter,
                'Search': q,
                'Staff Scope': "Personal Handled Only" if not is_admin else (request.GET.get('staff_id') or "All"),
            }
            if export_fmt == 'csv':
                return generate_csv_response("Sales Orders Report", headers, rows, "sales_orders", request, applied)
            elif export_fmt == 'xlsx':
                return generate_excel_response("Sales Orders Report", headers, rows, "sales_orders", request, applied)
            else:
                return generate_pdf_response("Sales Orders Report", headers, rows, "sales_orders", request, applied)

        paginator = Paginator(qs, 25)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'page_title': 'Sales Orders Report',
            'page_obj': page_obj,
            'total_count': qs.count(),
            'status_choices': Sale.Status.choices,
            'selected_status': status_filter or '',
            'active_preset': active_preset,
            'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
            'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
            'q': q,
            'all_staff': SalesStaffProfile.objects.select_related('user').all() if is_admin else None,
            'selected_staff': request.GET.get('staff_id', ''),
        }
        return render(request, self.template_name, context)


# -------------------------------------------------------------------------
# 4. Invoice & Outstanding Balances Report
# -------------------------------------------------------------------------

class InvoiceReportView(StaffRequiredMixin, View):
    template_name = 'reports/invoice_report.html'

    def get(self, request):
        is_admin = request.user.is_admin
        qs = Invoice.objects.select_related('customer__user', 'sale')

        if not is_admin:
            staff_prof = getattr(request.user, 'staff_profile', None)
            qs = qs.filter(customer__assigned_staff=staff_prof)
        else:
            staff_filter = request.GET.get('staff_id')
            if staff_filter:
                qs = qs.filter(customer__assigned_staff_id=staff_filter)

        payment_status_filter = request.GET.get('payment_status')
        if payment_status_filter:
            qs = qs.filter(payment_status=payment_status_filter)

        invoice_status_filter = request.GET.get('invoice_status')
        if invoice_status_filter:
            qs = qs.filter(invoice_status=invoice_status_filter)

        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(invoice_number__icontains=q) |
                Q(customer_name__icontains=q) |
                Q(customer__user__email__icontains=q)
            )

        start_date, end_date, active_preset, err_msg = parse_date_range(
            request.GET.get('preset'), request.GET.get('start_date'), request.GET.get('end_date')
        )
        if err_msg:
            messages.error(request, err_msg)
        if start_date:
            qs = qs.filter(invoice_date__gte=start_date)
        if end_date:
            qs = qs.filter(invoice_date__lte=end_date)

        qs = qs.order_by('-invoice_date', '-id')

        export_fmt = request.GET.get('export')
        if export_fmt in ('csv', 'xlsx', 'pdf'):
            headers = [
                'Invoice #', 'Sale #', 'Customer Name', 'Issue Date', 'Due Date',
                'Total Amount (₹)', 'Paid Amount (₹)', 'Balance Amount (₹)', 'Payment Status', 'Invoice Status'
            ]
            rows = []
            for inv in qs:
                rows.append([
                    inv.invoice_number,
                    inv.sale.sale_number if inv.sale else "-",
                    inv.customer_name or inv.customer.user.get_full_name(),
                    inv.invoice_date.strftime('%Y-%m-%d'),
                    inv.due_date.strftime('%Y-%m-%d'),
                    inv.total_amount,
                    inv.paid_amount,
                    inv.balance_amount,
                    inv.get_payment_status_display(),
                    inv.get_invoice_status_display(),
                ])

            applied = {
                'Preset': active_preset,
                'Start Date': start_date,
                'End Date': end_date,
                'Payment Status': payment_status_filter,
                'Invoice Status': invoice_status_filter,
                'Search': q,
                'Staff Scope': "Assigned Customer Invoices" if not is_admin else (request.GET.get('staff_id') or "All"),
            }
            if export_fmt == 'csv':
                return generate_csv_response("Invoices & Balances Report", headers, rows, "invoice_balances", request, applied)
            elif export_fmt == 'xlsx':
                return generate_excel_response("Invoices & Balances Report", headers, rows, "invoice_balances", request, applied)
            else:
                return generate_pdf_response("Invoices & Balances Report", headers, rows, "invoice_balances", request, applied)

        paginator = Paginator(qs, 25)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'page_title': 'Invoices & Balances Report',
            'page_obj': page_obj,
            'total_count': qs.count(),
            'payment_status_choices': Invoice.PaymentStatus.choices,
            'invoice_status_choices': Invoice.InvoiceStatus.choices,
            'selected_payment_status': payment_status_filter or '',
            'selected_invoice_status': invoice_status_filter or '',
            'active_preset': active_preset,
            'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
            'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
            'q': q,
            'all_staff': SalesStaffProfile.objects.select_related('user').all() if is_admin else None,
            'selected_staff': request.GET.get('staff_id', ''),
        }
        return render(request, self.template_name, context)


# -------------------------------------------------------------------------
# 5. Payments & Reversals Report
# -------------------------------------------------------------------------

class PaymentReportView(StaffRequiredMixin, View):
    template_name = 'reports/payment_report.html'

    def get(self, request):
        is_admin = request.user.is_admin
        qs = Payment.objects.select_related('customer__user', 'invoice')

        if not is_admin:
            staff_prof = getattr(request.user, 'staff_profile', None)
            qs = qs.filter(customer__assigned_staff=staff_prof)
        else:
            staff_filter = request.GET.get('staff_id')
            if staff_filter:
                qs = qs.filter(customer__assigned_staff_id=staff_filter)

        status_filter = request.GET.get('status')
        if status_filter:
            qs = qs.filter(payment_status=status_filter)

        reversal_filter = request.GET.get('is_reversed')
        if reversal_filter == 'yes':
            qs = qs.filter(is_reversed=True)
        elif reversal_filter == 'no':
            qs = qs.filter(is_reversed=False)

        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(payment_number__icontains=q) |
                Q(invoice__invoice_number__icontains=q) |
                Q(customer__user__full_name__icontains=q) |
                Q(transaction_reference__icontains=q)
            )

        start_date, end_date, active_preset, err_msg = parse_date_range(
            request.GET.get('preset'), request.GET.get('start_date'), request.GET.get('end_date')
        )
        if err_msg:
            messages.error(request, err_msg)
        start_dt, end_dt = to_timezone_datetimes(start_date, end_date)
        if start_dt:
            qs = qs.filter(payment_date__gte=start_dt)
        if end_dt:
            qs = qs.filter(payment_date__lte=end_dt)

        qs = qs.order_by('-payment_date', '-id')

        export_fmt = request.GET.get('export')
        if export_fmt in ('csv', 'xlsx', 'pdf'):
            headers = [
                'Payment #', 'Invoice #', 'Customer Name', 'Payment Date', 'Amount (₹)',
                'Method', 'Status', 'Reversed?', 'Reversal Reason', 'Reversal Date'
            ]
            rows = []
            for p in qs:
                rev_date_str = p.reversed_at.strftime('%Y-%m-%d %H:%M') if p.reversed_at else "-"
                rows.append([
                    p.payment_number,
                    p.invoice.invoice_number if p.invoice else "-",
                    p.customer.user.get_full_name(),
                    p.payment_date.strftime('%Y-%m-%d %H:%M'),
                    p.amount,
                    p.get_payment_method_display(),
                    p.get_payment_status_display(),
                    "Yes" if p.is_reversed else "No",
                    p.reversal_reason or "-",
                    rev_date_str
                ])

            applied = {
                'Preset': active_preset,
                'Start Date': start_date,
                'End Date': end_date,
                'Status': status_filter,
                'Reversed': reversal_filter,
                'Search': q,
                'Staff Scope': "Assigned Customer Payments" if not is_admin else (request.GET.get('staff_id') or "All"),
            }
            if export_fmt == 'csv':
                return generate_csv_response("Payments & Reversals Report", headers, rows, "payments_report", request, applied)
            elif export_fmt == 'xlsx':
                return generate_excel_response("Payments & Reversals Report", headers, rows, "payments_report", request, applied)
            else:
                return generate_pdf_response("Payments & Reversals Report", headers, rows, "payments_report", request, applied)

        paginator = Paginator(qs, 25)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'page_title': 'Payments & Reversals Report',
            'page_obj': page_obj,
            'total_count': qs.count(),
            'status_choices': Payment.Status.choices,
            'selected_status': status_filter or '',
            'reversal_filter': reversal_filter or '',
            'active_preset': active_preset,
            'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
            'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
            'q': q,
            'all_staff': SalesStaffProfile.objects.select_related('user').all() if is_admin else None,
            'selected_staff': request.GET.get('staff_id', ''),
        }
        return render(request, self.template_name, context)


# -------------------------------------------------------------------------
# 6. Product / Catalog and Stock Status Report
# -------------------------------------------------------------------------

class CatalogReportView(StaffRequiredMixin, View):
    template_name = 'reports/catalog_report.html'

    def get(self, request):
        qs = ProductService.objects.select_related('category')

        # Type filter
        type_filter = request.GET.get('item_type')
        if type_filter:
            qs = qs.filter(item_type=type_filter)

        # Stock status filter
        stock_filter = request.GET.get('stock_status')
        if stock_filter == 'OUT_OF_STOCK':
            qs = qs.filter(item_type=ProductService.ItemType.PRODUCT, stock_quantity=0)
        elif stock_filter == 'LOW_STOCK':
            qs = qs.filter(item_type=ProductService.ItemType.PRODUCT, stock_quantity__gt=0, stock_quantity__lte=F('reorder_level'))
        elif stock_filter == 'IN_STOCK':
            qs = qs.filter(item_type=ProductService.ItemType.PRODUCT, stock_quantity__gt=F('reorder_level'))

        # Category filter
        cat_filter = request.GET.get('category_id')
        if cat_filter:
            qs = qs.filter(category_id=cat_filter)

        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(product_code__icontains=q) |
                Q(product_name__icontains=q) |
                Q(category__category_name__icontains=q)
            )

        qs = qs.order_by('product_name', 'id')

        export_fmt = request.GET.get('export')
        if export_fmt in ('csv', 'xlsx', 'pdf'):
            headers = [
                'Product Code', 'Product Name', 'Category', 'Item Type', 'Unit Price (₹)',
                'Stock Qty', 'Reorder Level', 'Stock Status', 'Status'
            ]
            rows = []
            for p in qs:
                stock_str = "-" if p.item_type == 'SERVICE' else p.stock_quantity
                reorder_str = "-" if p.item_type == 'SERVICE' else p.reorder_level
                status_str = "-" if p.item_type == 'SERVICE' else (
                    "Out of Stock" if p.stock_quantity == 0 else (
                        "Low Stock" if p.stock_quantity <= p.reorder_level else "In Stock"
                    )
                )
                rows.append([
                    p.product_code,
                    p.product_name,
                    p.category.category_name,
                    p.get_item_type_display(),
                    p.unit_price,
                    stock_str,
                    reorder_str,
                    status_str,
                    p.get_status_display()
                ])

            applied = {
                'Item Type': type_filter,
                'Stock Status': stock_filter,
                'Category': cat_filter,
                'Search': q,
            }
            if export_fmt == 'csv':
                return generate_csv_response("Catalog & Stock Status Report", headers, rows, "catalog_stock", request, applied)
            elif export_fmt == 'xlsx':
                return generate_excel_response("Catalog & Stock Status Report", headers, rows, "catalog_stock", request, applied)
            else:
                return generate_pdf_response("Catalog & Stock Status Report", headers, rows, "catalog_stock", request, applied)

        paginator = Paginator(qs, 25)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'page_title': 'Catalog & Stock Status Report',
            'page_obj': page_obj,
            'total_count': qs.count(),
            'categories': Category.objects.filter(status='ACTIVE'),
            'item_type_choices': ProductService.ItemType.choices,
            'selected_type': type_filter or '',
            'selected_stock': stock_filter or '',
            'selected_cat': cat_filter or '',
            'q': q,
        }
        return render(request, self.template_name, context)


# -------------------------------------------------------------------------
# 7. Sales Staff Performance Report
# -------------------------------------------------------------------------

class StaffPerformanceReportView(StaffRequiredMixin, View):
    """
    Performance report comparing targets, monthly revenue, order counts, and won leads.
    Strict Rule: Sales Staff can ONLY view their own personal performance row.
    """
    template_name = 'reports/staff_performance_report.html'

    def get(self, request):
        is_admin = request.user.is_admin

        if not is_admin:
            staff_prof = getattr(request.user, 'staff_profile', None)
            if not staff_prof:
                messages.error(request, "Staff profile not found.")
                return redirect('reports:index')
            qs = SalesStaffProfile.objects.filter(id=staff_prof.id).select_related('user')
        else:
            qs = SalesStaffProfile.objects.select_related('user').all()

        qs = qs.order_by('-created_at', '-id')

        tz = get_crm_timezone()
        today = timezone.now().astimezone(tz).date()

        # Build performance metrics for each staff row
        report_rows = []
        for staff in qs:
            # Confirmed/completed sales for current month
            curr_month_sales = Sale.objects.filter(
                sales_staff=staff,
                status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED],
                sale_date__year=today.year,
                sale_date__month=today.month
            )
            curr_rev = curr_month_sales.aggregate(total=Sum('net_amount'))['total'] or Decimal('0.00')

            # Lifetime confirmed/completed sales
            lifetime_sales = Sale.objects.filter(
                sales_staff=staff,
                status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED]
            )
            lifetime_rev = lifetime_sales.aggregate(total=Sum('net_amount'))['total'] or Decimal('0.00')
            lifetime_orders = lifetime_sales.count()

            # Assigned leads won
            won_leads_cnt = Lead.objects.filter(assigned_staff=staff, status=Lead.Status.WON).count()
            target = staff.assigned_target or Decimal('0.00')

            if target > Decimal('0.00'):
                achievement_pct = round(float(curr_rev / target) * 100.0, 2)
            else:
                achievement_pct = 0.0

            report_rows.append({
                'staff': staff,
                'staff_code': staff.staff_code,
                'name': staff.user.get_full_name() or staff.user.username,
                'designation': staff.designation,
                'department': staff.department,
                'assigned_target': target,
                'current_month_revenue': curr_rev,
                'target_achievement_pct': achievement_pct,
                'lifetime_revenue': lifetime_rev,
                'lifetime_orders': lifetime_orders,
                'won_leads_count': won_leads_cnt,
            })

        export_fmt = request.GET.get('export')
        if export_fmt in ('csv', 'xlsx', 'pdf'):
            headers = [
                'Staff Code', 'Staff Name', 'Designation', 'Department', 'Monthly Target (₹)',
                'Month Revenue (₹)', 'Achievement (%)', 'Lifetime Revenue (₹)', 'Orders Count', 'Leads Won'
            ]
            rows = []
            for r in report_rows:
                rows.append([
                    r['staff_code'],
                    r['name'],
                    r['designation'],
                    r['department'],
                    r['assigned_target'],
                    r['current_month_revenue'],
                    r['target_achievement_pct'],
                    r['lifetime_revenue'],
                    r['lifetime_orders'],
                    r['won_leads_count'],
                ])

            applied = {
                'Current Month': f"{calendar.month_name[today.month]} {today.year}",
                'Scope': "Personal Row Only" if not is_admin else "All Sales Representatives",
            }
            if export_fmt == 'csv':
                return generate_csv_response("Sales Staff Performance Report", headers, rows, "staff_performance", request, applied)
            elif export_fmt == 'xlsx':
                return generate_excel_response("Sales Staff Performance Report", headers, rows, "staff_performance", request, applied)
            else:
                return generate_pdf_response("Sales Staff Performance Report", headers, rows, "staff_performance", request, applied)

        context = {
            'page_title': 'Sales Staff Performance Report',
            'report_rows': report_rows,
            'is_admin': is_admin,
            'current_month_label': f"{calendar.month_name[today.month]} {today.year}",
        }
        return render(request, self.template_name, context)


# -------------------------------------------------------------------------
# 8. Interaction and Reminder Activity Report
# -------------------------------------------------------------------------

class ActivityReportView(StaffRequiredMixin, View):
    template_name = 'reports/activity_report.html'

    def get(self, request):
        is_admin = request.user.is_admin

        # Query Reminders
        rem_qs = Reminder.objects.select_related('customer__user', 'assigned_to')
        if not is_admin:
            rem_qs = rem_qs.filter(assigned_to=request.user)

        # Query Interactions
        int_qs = Interaction.objects.select_related('customer__user', 'sales_staff__user')
        if not is_admin:
            staff_prof = getattr(request.user, 'staff_profile', None)
            int_qs = int_qs.filter(
                Q(sales_staff=staff_prof) | Q(created_by=request.user) | Q(customer__assigned_staff=staff_prof)
            )

        start_date, end_date, active_preset, err_msg = parse_date_range(
            request.GET.get('preset'), request.GET.get('start_date'), request.GET.get('end_date')
        )
        if err_msg:
            messages.error(request, err_msg)
        start_dt, end_dt = to_timezone_datetimes(start_date, end_date)

        if start_date:
            rem_qs = rem_qs.filter(due_date__gte=start_date)
        if end_date:
            rem_qs = rem_qs.filter(due_date__lte=end_date)

        if start_dt:
            int_qs = int_qs.filter(interaction_date__gte=start_dt)
        if end_dt:
            int_qs = int_qs.filter(interaction_date__lte=end_dt)

        rem_qs = rem_qs.order_by('-due_date', '-id')
        int_qs = int_qs.order_by('-interaction_date', '-id')

        activity_type = request.GET.get('activity_type', 'all')

        export_fmt = request.GET.get('export')
        if export_fmt in ('csv', 'xlsx', 'pdf'):
            headers = [
                'Entity Type', 'Subject / Title', 'Target Customer', 'Staff / Assigned',
                'Activity Date / Due', 'Type / Channel', 'Status'
            ]
            rows = []

            if activity_type in ('all', 'reminders'):
                for rem in rem_qs:
                    cust_str = rem.customer.user.get_full_name() if rem.customer else (rem.lead.full_name if rem.lead else "-")
                    rows.append([
                        "Reminder / Task",
                        rem.title,
                        cust_str,
                        rem.assigned_to.get_full_name() or rem.assigned_to.username,
                        rem.due_date.strftime('%Y-%m-%d'),
                        rem.get_reminder_type_display(),
                        rem.get_status_display(),
                    ])

            if activity_type in ('all', 'interactions'):
                for it in int_qs:
                    cust_str = it.customer.user.get_full_name() if it.customer else (it.lead.full_name if it.lead else "-")
                    staff_str = it.sales_staff.user.get_full_name() if it.sales_staff else (it.created_by.get_full_name() if it.created_by else "-")
                    rows.append([
                        "Customer Touchpoint",
                        it.subject,
                        cust_str,
                        staff_str,
                        it.interaction_date.strftime('%Y-%m-%d %H:%M'),
                        it.get_interaction_type_display(),
                        "Completed",
                    ])

            applied = {
                'Activity Type': activity_type,
                'Preset': active_preset,
                'Start Date': start_date,
                'End Date': end_date,
                'Scope': "Assigned Activities Only" if not is_admin else "All Company Activities",
            }
            if export_fmt == 'csv':
                return generate_csv_response("Activities & Touchpoints Report", headers, rows, "activities_report", request, applied)
            elif export_fmt == 'xlsx':
                return generate_excel_response("Activities & Touchpoints Report", headers, rows, "activities_report", request, applied)
            else:
                return generate_pdf_response("Activities & Touchpoints Report", headers, rows, "activities_report", request, applied)

        context = {
            'page_title': 'Interaction & Reminder Activity Report',
            'reminders_count': rem_qs.count(),
            'interactions_count': int_qs.count(),
            'reminders_list': rem_qs[:20],
            'interactions_list': int_qs[:20],
            'activity_type': activity_type,
            'active_preset': active_preset,
            'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
            'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
        }
        return render(request, self.template_name, context)
