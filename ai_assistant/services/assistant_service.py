from datetime import timedelta
from decimal import Decimal
from typing import Dict, Any, Optional, List
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum, F, Q, Avg
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, CustomerProfile, SalesStaffProfile
from catalog.models import ProductService, Category
from leads.models import Lead
from reminders.models import Reminder
from reports.services.forecast_service import generate_sales_forecast
from sales.models import Sale, Invoice, Payment, SaleItem
from ai_assistant.services.intent_service import (
    INTENT_CRM_SUMMARY,
    INTENT_CUSTOMERS_SUMMARY,
    INTENT_LEAD_PIPELINE_SUMMARY,
    INTENT_HIGH_PRIORITY_LEADS,
    INTENT_LEADS_NEEDING_FOLLOWUP,
    INTENT_REVENUE_SUMMARY,
    INTENT_COLLECTED_OUTSTANDING,
    INTENT_STOCK_ALERTS,
    INTENT_REMINDERS_SUMMARY,
    INTENT_STAFF_PERFORMANCE_ALL,
    INTENT_FORECAST_EXPLANATION,
    INTENT_STAFF_MY_CUSTOMERS,
    INTENT_STAFF_MY_LEADS,
    INTENT_STAFF_MY_HIGH_PRIORITY_LEADS,
    INTENT_STAFF_MY_FOLLOWUPS,
    INTENT_STAFF_MY_REMINDERS,
    INTENT_STAFF_MY_PERFORMANCE,
    INTENT_STAFF_MY_INVOICES,
    INTENT_CUSTOMER_MY_PROFILE,
    INTENT_CUSTOMER_MY_SALES,
    INTENT_CUSTOMER_MY_INVOICES,
    INTENT_CUSTOMER_MY_BALANCE,
    INTENT_CUSTOMER_MY_PAYMENTS,
    INTENT_CUSTOMER_MY_REMINDERS,
    INTENT_PRODUCT_RECOMMENDATIONS,
    INTENT_CATALOG_BROWSE,
    INTENT_HELP_INTENTS,
    INTENT_UNSUPPORTED,
)
from ai_assistant.services.recommendation_service import get_recommendations_for_customer


def get_current_timestamp_str() -> str:
    tz = timezone.get_current_timezone_name()
    return timezone.now().strftime(f"%Y-%m-%d %H:%M:%S ({tz})")


def execute_assistant_intent(
    intent: str,
    user: CustomUser,
    params: Optional[Dict[str, Any]] = None,
    request: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Role-aware intent dispatcher.
    Executes safe, predefined Django ORM queries with hard-coded role filtering.
    Guarantees no raw SQL, no dynamic eval, no secrets exposure.
    """
    params = params or {}
    now_str = get_current_timestamp_str()

    if not user.is_authenticated or not user.is_account_active:
        raise PermissionDenied("User is not authenticated or account is inactive.")

    # -------------------------------------------------------------------------
    # 1. ADMIN INTENTS
    # -------------------------------------------------------------------------
    if intent == INTENT_CRM_SUMMARY and user.is_admin:
        return _handle_crm_summary(now_str)

    elif intent == INTENT_CUSTOMERS_SUMMARY and user.is_admin:
        return _handle_customers_summary(now_str)

    elif intent == INTENT_LEAD_PIPELINE_SUMMARY and user.is_admin:
        return _handle_lead_pipeline_summary(now_str)

    elif intent == INTENT_HIGH_PRIORITY_LEADS and user.is_admin:
        return _handle_high_priority_leads(now_str)

    elif intent == INTENT_LEADS_NEEDING_FOLLOWUP and user.is_admin:
        return _handle_leads_needing_followup(now_str)

    elif intent == INTENT_REVENUE_SUMMARY and user.is_admin:
        return _handle_revenue_summary(now_str)

    elif intent == INTENT_COLLECTED_OUTSTANDING and user.is_admin:
        return _handle_collected_outstanding(now_str)

    elif intent == INTENT_STOCK_ALERTS and user.is_admin:
        return _handle_stock_alerts(now_str)

    elif intent == INTENT_REMINDERS_SUMMARY and user.is_admin:
        return _handle_reminders_summary(now_str)

    elif intent == INTENT_STAFF_PERFORMANCE_ALL and user.is_admin:
        return _handle_staff_performance_all(now_str)

    # -------------------------------------------------------------------------
    # 2. SALES STAFF INTENTS
    # -------------------------------------------------------------------------
    elif intent == INTENT_STAFF_MY_CUSTOMERS and user.is_sales_staff:
        return _handle_staff_my_customers(user, now_str)

    elif intent == INTENT_STAFF_MY_LEADS and user.is_sales_staff:
        return _handle_staff_my_leads(user, now_str)

    elif intent == INTENT_STAFF_MY_HIGH_PRIORITY_LEADS and user.is_sales_staff:
        return _handle_staff_my_high_priority_leads(user, now_str)

    elif intent == INTENT_STAFF_MY_FOLLOWUPS and user.is_sales_staff:
        return _handle_staff_my_followups(user, now_str)

    elif intent == INTENT_STAFF_MY_REMINDERS and user.is_sales_staff:
        return _handle_staff_my_reminders(user, now_str)

    elif intent == INTENT_STAFF_MY_PERFORMANCE and user.is_sales_staff:
        return _handle_staff_my_performance(user, now_str)

    elif intent == INTENT_STAFF_MY_INVOICES and user.is_sales_staff:
        return _handle_staff_my_invoices(user, now_str)

    # -------------------------------------------------------------------------
    # 3. CUSTOMER INTENTS
    # -------------------------------------------------------------------------
    elif intent == INTENT_CUSTOMER_MY_PROFILE and user.is_customer:
        return _handle_customer_my_profile(user, now_str)

    elif intent == INTENT_CUSTOMER_MY_SALES and user.is_customer:
        return _handle_customer_my_sales(user, now_str)

    elif intent == INTENT_CUSTOMER_MY_INVOICES and user.is_customer:
        return _handle_customer_my_invoices(user, now_str)

    elif intent == INTENT_CUSTOMER_MY_BALANCE and user.is_customer:
        return _handle_customer_my_balance(user, now_str)

    elif intent == INTENT_CUSTOMER_MY_PAYMENTS and user.is_customer:
        return _handle_customer_my_payments(user, now_str)

    elif intent == INTENT_CUSTOMER_MY_REMINDERS and user.is_customer:
        return _handle_customer_my_reminders(user, now_str)

    # -------------------------------------------------------------------------
    # 4. SHARED / MULTI-ROLE INTENTS
    # -------------------------------------------------------------------------
    elif intent == INTENT_FORECAST_EXPLANATION and (user.is_admin or user.is_sales_staff):
        return _handle_forecast_explanation(user, now_str)

    elif intent == INTENT_PRODUCT_RECOMMENDATIONS:
        return _handle_product_recommendations(user, params, now_str)

    elif intent == INTENT_CATALOG_BROWSE:
        return _handle_catalog_browse(user, now_str)

    elif intent == INTENT_HELP_INTENTS:
        return _handle_help(user, now_str)

    # -------------------------------------------------------------------------
    # 5. UNSUPPORTED / UNAUTHORIZED FALLBACK
    # -------------------------------------------------------------------------
    return _handle_unsupported(user, now_str)


# =============================================================================
# ADMIN HANDLERS
# =============================================================================

def _handle_crm_summary(now_str: str) -> Dict[str, Any]:
    active_cust_cnt = CustomerProfile.objects.filter(is_active=True).count()
    active_leads_cnt = Lead.objects.filter(is_active=True).exclude(status__in=[Lead.Status.WON, Lead.Status.LOST]).count()
    
    confirmed_sales_qs = Sale.objects.filter(status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED])
    rev = confirmed_sales_qs.aggregate(total=Sum('net_amount'))['total'] or Decimal('0.00')
    orders_cnt = confirmed_sales_qs.count()

    payments_qs = Payment.objects.filter(payment_status=Payment.Status.COMPLETED, is_reversed=False)
    collected = payments_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    invoices_qs = Invoice.objects.exclude(invoice_status=Invoice.InvoiceStatus.CANCELLED)
    balance = invoices_qs.aggregate(total=Sum('balance_amount'))['total'] or Decimal('0.00')

    physical_products = ProductService.objects.filter(item_type=ProductService.ItemType.PRODUCT, status=ProductService.Status.ACTIVE)
    out_of_stock_cnt = physical_products.filter(stock_quantity=0).count()
    low_stock_cnt = physical_products.filter(stock_quantity__gt=0, stock_quantity__lte=F('reorder_level')).count()

    pending_reminders_cnt = Reminder.objects.filter(status=Reminder.Status.PENDING).count()

    return {
        'intent': INTENT_CRM_SUMMARY,
        'topic': 'CRM Executive Summary',
        'calculation_timestamp': now_str,
        'data_source_summary': 'System-wide aggregated CRM tables (Customers, Leads, Confirmed Sales, Payments, Invoices, Catalog).',
        'answer_text': (
            f"Currently, the CRM maintains {active_cust_cnt} active customer(s) and {active_leads_cnt} active pipeline lead(s). "
            f"Total confirmed sales revenue stands at ₹{rev:,.2f} across {orders_cnt} order(s). "
            f"Total collected revenue is ₹{collected:,.2f}, with ₹{balance:,.2f} remaining in outstanding invoice balances. "
            f"There are {pending_reminders_cnt} pending follow-up task(s), and {low_stock_cnt + out_of_stock_cnt} inventory alert(s)."
        ),
        'metrics': [
            {'label': 'Active Customers', 'value': str(active_cust_cnt), 'badge': 'primary'},
            {'label': 'Active Leads', 'value': str(active_leads_cnt), 'badge': 'info'},
            {'label': 'Confirmed Revenue', 'value': f"₹{rev:,.2f}", 'badge': 'success'},
            {'label': 'Collected Payments', 'value': f"₹{collected:,.2f}", 'badge': 'success'},
            {'label': 'Outstanding Balance', 'value': f"₹{balance:,.2f}", 'badge': 'warning'},
            {'label': 'Inventory Alerts', 'value': str(low_stock_cnt + out_of_stock_cnt), 'badge': 'danger' if (low_stock_cnt + out_of_stock_cnt) > 0 else 'secondary'},
        ],
        'suggested_actions': [
            {'label': 'Admin Dashboard', 'url': reverse('dashboard:admin_dashboard')},
            {'label': 'Lead Pipeline', 'url': reverse('leads:lead_list')},
            {'label': 'Executive Analytics', 'url': reverse('reports:admin_analytics')},
        ]
    }


def _handle_customers_summary(now_str: str) -> Dict[str, Any]:
    active_qs = CustomerProfile.objects.filter(is_active=True)
    total_cnt = active_qs.count()
    ind_cnt = active_qs.filter(customer_type=CustomerProfile.CustomerType.INDIVIDUAL).count()
    biz_cnt = active_qs.filter(customer_type=CustomerProfile.CustomerType.BUSINESS).count()

    recent_customers = active_qs.select_related('user', 'assigned_staff__user').order_by('-created_at')[:5]
    table_rows = []
    for c in recent_customers:
        staff_name = c.assigned_staff.user.full_name if c.assigned_staff and c.assigned_staff.user else "Unassigned"
        table_rows.append([
            c.customer_code,
            c.user.full_name or c.user.username,
            c.company or "—",
            c.get_customer_type_display(),
            staff_name
        ])

    return {
        'intent': INTENT_CUSTOMERS_SUMMARY,
        'topic': 'Total & Active Customers',
        'calculation_timestamp': now_str,
        'data_source_summary': 'CustomerProfile records where is_active=True.',
        'answer_text': f"There are {total_cnt} active customer(s) registered in the system: {biz_cnt} corporate business account(s) and {ind_cnt} individual client(s).",
        'metrics': [
            {'label': 'Total Active', 'value': str(total_cnt), 'badge': 'primary'},
            {'label': 'Corporate Accounts', 'value': str(biz_cnt), 'badge': 'info'},
            {'label': 'Individual Clients', 'value': str(ind_cnt), 'badge': 'secondary'},
        ],
        'table_data': {
            'headers': ['Customer Code', 'Contact Name', 'Company', 'Type', 'Assigned Rep'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'View Customers', 'url': reverse('accounts:customer_list')},
            {'label': 'Add Customer', 'url': reverse('accounts:customer_create')},
        ]
    }


def _handle_lead_pipeline_summary(now_str: str) -> Dict[str, Any]:
    active_leads = Lead.objects.filter(is_active=True)
    total_leads = active_leads.count()

    status_breakdown = list(active_leads.values('status').annotate(
        cnt=Count('id'),
        total_val=Sum('estimated_value')
    ).order_by('status'))

    status_labels = {choice[0]: choice[1] for choice in Lead.Status.choices}
    table_rows = []
    for row in status_breakdown:
        s_code = row['status']
        s_name = status_labels.get(s_code, s_code)
        val = row['total_val'] or Decimal('0.00')
        table_rows.append([s_name, str(row['cnt']), f"₹{val:,.2f}"])

    won_cnt = active_leads.filter(status=Lead.Status.WON).count()
    lost_cnt = active_leads.filter(status=Lead.Status.LOST).count()
    closed_cnt = won_cnt + lost_cnt
    win_rate = round((won_cnt / closed_cnt) * 100, 2) if closed_cnt > 0 else 0.0

    return {
        'intent': INTENT_LEAD_PIPELINE_SUMMARY,
        'topic': 'Lead Pipeline Breakdown',
        'calculation_timestamp': now_str,
        'data_source_summary': 'Lead records where is_active=True grouped by pipeline status.',
        'answer_text': (
            f"The sales pipeline contains {total_leads} active lead(s). "
            f"Historical conversion win rate on closed deals is {win_rate}% ({won_cnt} won, {lost_cnt} lost)."
        ),
        'metrics': [
            {'label': 'Total Pipeline Leads', 'value': str(total_leads), 'badge': 'primary'},
            {'label': 'Won Deals', 'value': str(won_cnt), 'badge': 'success'},
            {'label': 'Lost Deals', 'value': str(lost_cnt), 'badge': 'danger'},
            {'label': 'Closed Win Rate', 'value': f"{win_rate}%", 'badge': 'info'},
        ],
        'table_data': {
            'headers': ['Pipeline Stage', 'Lead Count', 'Estimated Pipeline Value'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'Leads Pipeline', 'url': reverse('leads:lead_list')},
            {'label': 'New Lead', 'url': reverse('leads:lead_create')},
        ]
    }


def _handle_high_priority_leads(now_str: str) -> Dict[str, Any]:
    high_leads = Lead.objects.filter(
        is_active=True,
        ai_priority=Lead.Priority.HIGH
    ).select_related('assigned_staff__user').order_by('-probability_score', '-created_at')

    cnt = high_leads.count()
    table_rows = []
    for l in high_leads[:5]:
        staff_str = l.assigned_staff.user.full_name if l.assigned_staff and l.assigned_staff.user else "Unassigned"
        method_str = l.get_scoring_method_display()
        table_rows.append([
            l.lead_code,
            l.full_name,
            l.company or "—",
            f"{l.probability_score}%",
            method_str,
            f"₹{l.estimated_value:,.2f}",
            staff_str
        ])

    return {
        'intent': INTENT_HIGH_PRIORITY_LEADS,
        'topic': 'High-Priority Opportunities',
        'calculation_timestamp': now_str,
        'data_source_summary': 'Lead records where is_active=True and ai_priority=HIGH, scored by LeadScoringService.',
        'answer_text': (
            f"There are currently {cnt} high-priority opportunity lead(s) requiring proactive sales engagement. "
            f"Scores are derived transparently via existing LeadScoringService methods."
        ),
        'metrics': [
            {'label': 'High Priority Leads', 'value': str(cnt), 'badge': 'danger'},
        ],
        'table_data': {
            'headers': ['Code', 'Contact', 'Company', 'AI Score', 'Scoring Method', 'Deal Value', 'Assigned Rep'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'View All Leads', 'url': reverse('leads:lead_list')},
        ]
    }


def _handle_leads_needing_followup(now_str: str) -> Dict[str, Any]:
    cutoff = timezone.now() - timedelta(days=3)
    stale_leads = Lead.objects.filter(
        is_active=True,
        status__in=[Lead.Status.NEW, Lead.Status.CONTACTED]
    ).exclude(
        interactions__interaction_date__gte=cutoff
    ).filter(
        created_at__lte=cutoff
    ).select_related('assigned_staff__user')

    cnt = stale_leads.count()
    table_rows = []
    for l in stale_leads[:5]:
        staff_str = l.assigned_staff.user.full_name if l.assigned_staff and l.assigned_staff.user else "Unassigned"
        table_rows.append([
            l.lead_code,
            l.full_name,
            l.get_status_display(),
            l.created_at.strftime('%Y-%m-%d'),
            staff_str
        ])

    return {
        'intent': INTENT_LEADS_NEEDING_FOLLOWUP,
        'topic': 'Leads Requiring Follow-Up',
        'calculation_timestamp': now_str,
        'data_source_summary': 'Evaluated via Smart Follow-Up Engine Rule 1 (active leads with 0 touchpoints in >=3 days).',
        'answer_text': (
            f"Found {cnt} lead(s) with no logged communication for 3 or more days. "
            f"These follow-up suggestions are generated by deterministic CRM business rules."
        ),
        'metrics': [
            {'label': 'Stale Leads', 'value': str(cnt), 'badge': 'warning'},
        ],
        'table_data': {
            'headers': ['Lead Code', 'Contact Name', 'Status', 'Created Date', 'Assigned Rep'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'Manage Reminders', 'url': reverse('reminders:reminder_list')},
            {'label': 'Lead Pipeline', 'url': reverse('leads:lead_list')},
        ]
    }


def _handle_revenue_summary(now_str: str) -> Dict[str, Any]:
    confirmed_sales = Sale.objects.filter(status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED])
    total_rev = confirmed_sales.aggregate(total=Sum('net_amount'))['total'] or Decimal('0.00')
    subtotal = confirmed_sales.aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')
    tax = confirmed_sales.aggregate(total=Sum('tax_amount'))['total'] or Decimal('0.00')
    count = confirmed_sales.count()

    aov = (total_rev / Decimal(count)).quantize(Decimal('0.01')) if count > 0 else Decimal('0.00')

    return {
        'intent': INTENT_REVENUE_SUMMARY,
        'topic': 'Revenue & Commercial Summary',
        'calculation_timestamp': now_str,
        'data_source_summary': 'Sale orders with status CONFIRMED or COMPLETED strictly excluding DRAFT and CANCELLED.',
        'answer_text': (
            f"Total confirmed sales revenue is ₹{total_rev:,.2f} across {count} order(s). "
            f"Average Order Value (AOV) is ₹{aov:,.2f}. All cancelled and draft orders are strictly excluded."
        ),
        'metrics': [
            {'label': 'Net Confirmed Revenue', 'value': f"₹{total_rev:,.2f}", 'badge': 'success'},
            {'label': 'Order Count', 'value': str(count), 'badge': 'primary'},
            {'label': 'Average Order Value', 'value': f"₹{aov:,.2f}", 'badge': 'info'},
            {'label': 'Tax Collected', 'value': f"₹{tax:,.2f}", 'badge': 'secondary'},
        ],
        'suggested_actions': [
            {'label': 'Sales Orders', 'url': reverse('sales:sale_list')},
            {'label': 'Executive Analytics', 'url': reverse('reports:admin_analytics')},
        ]
    }


def _handle_collected_outstanding(now_str: str) -> Dict[str, Any]:
    payments = Payment.objects.filter(payment_status=Payment.Status.COMPLETED, is_reversed=False)
    collected = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    pay_count = payments.count()

    invoices = Invoice.objects.exclude(invoice_status=Invoice.InvoiceStatus.CANCELLED)
    invoiced_total = invoices.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    outstanding = invoices.aggregate(total=Sum('balance_amount'))['total'] or Decimal('0.00')
    overdue = invoices.filter(invoice_status=Invoice.InvoiceStatus.OVERDUE).aggregate(total=Sum('balance_amount'))['total'] or Decimal('0.00')

    return {
        'intent': INTENT_COLLECTED_OUTSTANDING,
        'topic': 'Collected Revenue vs Outstanding Balances',
        'calculation_timestamp': now_str,
        'data_source_summary': 'Non-reversed completed payments and non-cancelled invoice balances.',
        'answer_text': (
            f"Collected non-reversed revenue is ₹{collected:,.2f} across {pay_count} transaction(s). "
            f"Total unpaid invoice balance is ₹{outstanding:,.2f}, of which ₹{overdue:,.2f} is currently overdue."
        ),
        'metrics': [
            {'label': 'Collected Payments', 'value': f"₹{collected:,.2f}", 'badge': 'success'},
            {'label': 'Outstanding Balance', 'value': f"₹{outstanding:,.2f}", 'badge': 'warning'},
            {'label': 'Overdue Balance', 'value': f"₹{overdue:,.2f}", 'badge': 'danger'},
            {'label': 'Total Invoiced', 'value': f"₹{invoiced_total:,.2f}", 'badge': 'info'},
        ],
        'suggested_actions': [
            {'label': 'Invoices List', 'url': reverse('sales:invoice_list')},
            {'label': 'Payment Receipts', 'url': reverse('sales:payment_list')},
        ]
    }


def _handle_stock_alerts(now_str: str) -> Dict[str, Any]:
    active_physical = ProductService.objects.filter(
        item_type=ProductService.ItemType.PRODUCT,
        status=ProductService.Status.ACTIVE
    ).select_related('category')

    out_of_stock = active_physical.filter(stock_quantity=0)
    low_stock = active_physical.filter(stock_quantity__gt=0, stock_quantity__lte=F('reorder_level'))

    table_rows = []
    for p in out_of_stock:
        table_rows.append([p.product_code, p.product_name, p.category.category_name, "0", str(p.reorder_level), "OUT OF STOCK"])
    for p in low_stock:
        table_rows.append([p.product_code, p.product_name, p.category.category_name, str(p.stock_quantity), str(p.reorder_level), "LOW STOCK"])

    return {
        'intent': INTENT_STOCK_ALERTS,
        'topic': 'Inventory & Stock Alerts',
        'calculation_timestamp': now_str,
        'data_source_summary': 'Active physical products evaluated against stock_quantity and reorder_level.',
        'answer_text': f"There are {out_of_stock.count()} out-of-stock product(s) and {low_stock.count()} low-stock item(s) requiring restock attention.",
        'metrics': [
            {'label': 'Out of Stock', 'value': str(out_of_stock.count()), 'badge': 'danger'},
            {'label': 'Low Stock', 'value': str(low_stock.count()), 'badge': 'warning'},
        ],
        'table_data': {
            'headers': ['Product Code', 'Product Name', 'Category', 'Current Stock', 'Reorder Level', 'Alert Status'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'Product Catalog', 'url': reverse('catalog:product_list')},
        ]
    }


def _handle_reminders_summary(now_str: str) -> Dict[str, Any]:
    today = timezone.localdate()
    all_reminders = Reminder.objects.all()
    pending = all_reminders.filter(status=Reminder.Status.PENDING)
    overdue = pending.filter(due_date__lt=today)
    completed = all_reminders.filter(status=Reminder.Status.COMPLETED)

    table_rows = []
    for r in overdue[:5]:
        table_rows.append([
            r.title,
            r.get_reminder_type_display(),
            r.due_date.strftime('%Y-%m-%d'),
            r.get_priority_display(),
            r.assigned_to.full_name or r.assigned_to.username
        ])

    return {
        'intent': INTENT_REMINDERS_SUMMARY,
        'topic': 'System-Wide Tasks & Reminders',
        'calculation_timestamp': now_str,
        'data_source_summary': 'Reminders table status and due_date comparisons.',
        'answer_text': f"Total pending reminders: {pending.count()}, with {overdue.count()} overdue. {completed.count()} task(s) have been completed.",
        'metrics': [
            {'label': 'Pending Tasks', 'value': str(pending.count()), 'badge': 'primary'},
            {'label': 'Overdue Tasks', 'value': str(overdue.count()), 'badge': 'danger'},
            {'label': 'Completed Tasks', 'value': str(completed.count()), 'badge': 'success'},
        ],
        'table_data': {
            'headers': ['Task Title', 'Type', 'Due Date', 'Priority', 'Assigned To'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'Task Calendar & List', 'url': reverse('reminders:reminder_list')},
            {'label': 'Create Task', 'url': reverse('reminders:reminder_create')},
        ]
    }


def _handle_staff_performance_all(now_str: str) -> Dict[str, Any]:
    staff_profiles = SalesStaffProfile.objects.select_related('user').all()
    table_rows = []

    for sp in staff_profiles:
        sales = Sale.objects.filter(sales_staff=sp, status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED])
        rev = sales.aggregate(total=Sum('net_amount'))['total'] or Decimal('0.00')
        target = sp.assigned_target or Decimal('0.00')
        achieve = round(float(rev / target) * 100, 2) if target > 0 else 0.0
        leads_cnt = Lead.objects.filter(assigned_staff=sp, is_active=True).count()
        table_rows.append([
            sp.staff_code,
            sp.user.full_name or sp.user.username,
            sp.designation,
            f"₹{rev:,.2f}",
            f"₹{target:,.2f}",
            f"{achieve}%",
            str(leads_cnt)
        ])

    return {
        'intent': INTENT_STAFF_PERFORMANCE_ALL,
        'topic': 'Sales Staff Performance Ranking',
        'calculation_timestamp': now_str,
        'data_source_summary': 'SalesStaffProfile, confirmed Sales orders, and assigned Leads.',
        'answer_text': f"Evaluated performance metrics across {len(staff_profiles)} sales staff member(s).",
        'table_data': {
            'headers': ['Staff Code', 'Representative', 'Designation', 'Revenue Achieved', 'Target', 'Achievement', 'Assigned Leads'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'Sales Staff Directory', 'url': reverse('accounts:staff_list')},
            {'label': 'Analytics Report', 'url': reverse('reports:admin_analytics')},
        ]
    }


# =============================================================================
# SALES STAFF HANDLERS (Strictly Scoped)
# =============================================================================

def _handle_staff_my_customers(user: CustomUser, now_str: str) -> Dict[str, Any]:
    sp = getattr(user, 'staff_profile', None)
    if not sp:
        return _unassigned_staff_response(now_str)

    customers = CustomerProfile.objects.filter(assigned_staff=sp, is_active=True).select_related('user').order_by('customer_code')
    cnt = customers.count()
    table_rows = []
    for c in customers[:10]:
        table_rows.append([
            c.customer_code,
            c.user.full_name or c.user.username,
            c.company or "—",
            c.user.phone or "—",
            c.get_customer_type_display()
        ])

    return {
        'intent': INTENT_STAFF_MY_CUSTOMERS,
        'topic': 'My Assigned Customers',
        'calculation_timestamp': now_str,
        'data_source_summary': f"CustomerProfile records assigned to staff {sp.staff_code}.",
        'answer_text': f"You are currently assigned to manage {cnt} active customer account(s).",
        'metrics': [
            {'label': 'My Assigned Customers', 'value': str(cnt), 'badge': 'primary'},
        ],
        'table_data': {
            'headers': ['Customer Code', 'Contact Name', 'Company', 'Phone', 'Type'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'View Customers', 'url': reverse('accounts:customer_list')},
        ]
    }


def _handle_staff_my_leads(user: CustomUser, now_str: str) -> Dict[str, Any]:
    sp = getattr(user, 'staff_profile', None)
    if not sp:
        return _unassigned_staff_response(now_str)

    leads = Lead.objects.filter(assigned_staff=sp, is_active=True).order_by('-created_at')
    cnt = leads.count()
    active_cnt = leads.exclude(status__in=[Lead.Status.WON, Lead.Status.LOST]).count()

    table_rows = []
    for l in leads[:10]:
        table_rows.append([
            l.lead_code,
            l.full_name,
            l.company or "—",
            l.get_status_display(),
            l.get_ai_priority_display(),
            f"₹{l.estimated_value:,.2f}"
        ])

    return {
        'intent': INTENT_STAFF_MY_LEADS,
        'topic': 'My Assigned Leads',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Lead records assigned to staff {sp.staff_code}.",
        'answer_text': f"You have {cnt} assigned lead(s) in your portfolio ({active_cnt} active).",
        'metrics': [
            {'label': 'Total Assigned Leads', 'value': str(cnt), 'badge': 'primary'},
            {'label': 'Active Pipeline Leads', 'value': str(active_cnt), 'badge': 'info'},
        ],
        'table_data': {
            'headers': ['Lead Code', 'Contact Name', 'Company', 'Status', 'Priority', 'Deal Value'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'My Leads Pipeline', 'url': reverse('leads:lead_list')},
        ]
    }


def _handle_staff_my_high_priority_leads(user: CustomUser, now_str: str) -> Dict[str, Any]:
    sp = getattr(user, 'staff_profile', None)
    if not sp:
        return _unassigned_staff_response(now_str)

    high_leads = Lead.objects.filter(
        assigned_staff=sp,
        is_active=True,
        ai_priority=Lead.Priority.HIGH
    ).order_by('-probability_score')

    cnt = high_leads.count()
    table_rows = []
    for l in high_leads[:5]:
        table_rows.append([
            l.lead_code,
            l.full_name,
            l.company or "—",
            f"{l.probability_score}%",
            l.get_scoring_method_display(),
            f"₹{l.estimated_value:,.2f}"
        ])

    return {
        'intent': INTENT_STAFF_MY_HIGH_PRIORITY_LEADS,
        'topic': 'My High-Priority Leads',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Leads assigned to {sp.staff_code} scored with ai_priority=HIGH.",
        'answer_text': f"You have {cnt} high-priority opportunity lead(s) requiring prompt attention.",
        'metrics': [
            {'label': 'High Priority Opportunities', 'value': str(cnt), 'badge': 'danger'},
        ],
        'table_data': {
            'headers': ['Lead Code', 'Contact', 'Company', 'Score', 'Method', 'Estimated Value'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'View Pipeline', 'url': reverse('leads:lead_list')},
        ]
    }


def _handle_staff_my_followups(user: CustomUser, now_str: str) -> Dict[str, Any]:
    sp = getattr(user, 'staff_profile', None)
    if not sp:
        return _unassigned_staff_response(now_str)

    cutoff = timezone.now() - timedelta(days=3)
    stale_leads = Lead.objects.filter(
        assigned_staff=sp,
        is_active=True,
        status__in=[Lead.Status.NEW, Lead.Status.CONTACTED]
    ).exclude(
        interactions__interaction_date__gte=cutoff
    ).filter(
        created_at__lte=cutoff
    )

    cnt = stale_leads.count()
    table_rows = []
    for l in stale_leads[:5]:
        table_rows.append([
            l.lead_code,
            l.full_name,
            l.get_status_display(),
            l.created_at.strftime('%Y-%m-%d')
        ])

    return {
        'intent': INTENT_STAFF_MY_FOLLOWUPS,
        'topic': 'My Leads Requiring Follow-Up',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Leads assigned to {sp.staff_code} with 0 touchpoints in >=3 days (Smart Follow-Up Rule 1).",
        'answer_text': f"You have {cnt} lead(s) requiring follow-up communication.",
        'metrics': [
            {'label': 'Pending Follow-Ups', 'value': str(cnt), 'badge': 'warning'},
        ],
        'table_data': {
            'headers': ['Lead Code', 'Contact Name', 'Status', 'Created Date'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'My Reminders', 'url': reverse('reminders:reminder_list')},
        ]
    }


def _handle_staff_my_reminders(user: CustomUser, now_str: str) -> Dict[str, Any]:
    today = timezone.localdate()
    reminders = Reminder.objects.filter(assigned_to=user, status=Reminder.Status.PENDING)
    overdue = reminders.filter(due_date__lt=today)
    due_today = reminders.filter(due_date=today)

    table_rows = []
    for r in reminders.order_by('due_date')[:10]:
        status_label = "OVERDUE" if r.due_date < today else ("DUE TODAY" if r.due_date == today else "UPCOMING")
        table_rows.append([
            r.title,
            r.get_reminder_type_display(),
            r.due_date.strftime('%Y-%m-%d'),
            r.get_priority_display(),
            status_label
        ])

    return {
        'intent': INTENT_STAFF_MY_REMINDERS,
        'topic': 'My Assigned Reminders & Tasks',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Reminder records assigned to user {user.username} with status PENDING.",
        'answer_text': f"You have {reminders.count()} pending task(s): {overdue.count()} overdue and {due_today.count()} due today.",
        'metrics': [
            {'label': 'Pending Tasks', 'value': str(reminders.count()), 'badge': 'primary'},
            {'label': 'Overdue Tasks', 'value': str(overdue.count()), 'badge': 'danger'},
            {'label': 'Due Today', 'value': str(due_today.count()), 'badge': 'warning'},
        ],
        'table_data': {
            'headers': ['Task Title', 'Type', 'Due Date', 'Priority', 'Status'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'Manage Reminders', 'url': reverse('reminders:reminder_list')},
        ]
    }


def _handle_staff_my_performance(user: CustomUser, now_str: str) -> Dict[str, Any]:
    sp = getattr(user, 'staff_profile', None)
    if not sp:
        return _unassigned_staff_response(now_str)

    today = timezone.localdate()
    sales = Sale.objects.filter(sales_staff=sp, status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED])
    total_rev = sales.aggregate(total=Sum('net_amount'))['total'] or Decimal('0.00')
    count = sales.count()

    # Current month revenue
    cur_month_sales = sales.filter(sale_date__year=today.year, sale_date__month=today.month)
    month_rev = cur_month_sales.aggregate(total=Sum('net_amount'))['total'] or Decimal('0.00')

    target = sp.assigned_target or Decimal('0.00')
    achieve_pct = round(float(month_rev / target) * 100, 2) if target > 0 else 0.0

    return {
        'intent': INTENT_STAFF_MY_PERFORMANCE,
        'topic': 'My Sales Performance',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Personal confirmed sales for staff {sp.staff_code} against assigned target.",
        'answer_text': (
            f"You have confirmed {count} order(s) for a career total of ₹{total_rev:,.2f}. "
            f"This month you achieved ₹{month_rev:,.2f} of your ₹{target:,.2f} monthly target ({achieve_pct}%)."
        ),
        'metrics': [
            {'label': 'Month Revenue', 'value': f"₹{month_rev:,.2f}", 'badge': 'success'},
            {'label': 'Monthly Target', 'value': f"₹{target:,.2f}", 'badge': 'primary'},
            {'label': 'Target Achievement', 'value': f"{achieve_pct}%", 'badge': 'info'},
            {'label': 'Career Confirmed Sales', 'value': f"₹{total_rev:,.2f}", 'badge': 'secondary'},
        ],
        'suggested_actions': [
            {'label': 'Personal Analytics', 'url': reverse('reports:staff_analytics')},
            {'label': 'My Sales Orders', 'url': reverse('sales:sale_list')},
        ]
    }


def _handle_staff_my_invoices(user: CustomUser, now_str: str) -> Dict[str, Any]:
    sp = getattr(user, 'staff_profile', None)
    if not sp:
        return _unassigned_staff_response(now_str)

    invoices = Invoice.objects.filter(
        customer__assigned_staff=sp
    ).exclude(invoice_status=Invoice.InvoiceStatus.CANCELLED)

    tot_invoiced = invoices.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    tot_balance = invoices.aggregate(total=Sum('balance_amount'))['total'] or Decimal('0.00')
    overdue_balance = invoices.filter(invoice_status=Invoice.InvoiceStatus.OVERDUE).aggregate(total=Sum('balance_amount'))['total'] or Decimal('0.00')

    return {
        'intent': INTENT_STAFF_MY_INVOICES,
        'topic': 'Invoices for My Assigned Customers',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Invoices for customers assigned to staff {sp.staff_code}.",
        'answer_text': (
            f"Your assigned customers have {invoices.count()} valid invoice(s) totaling ₹{tot_invoiced:,.2f}. "
            f"Total outstanding balance is ₹{tot_balance:,.2f} (overdue: ₹{overdue_balance:,.2f})."
        ),
        'metrics': [
            {'label': 'Total Invoiced', 'value': f"₹{tot_invoiced:,.2f}", 'badge': 'primary'},
            {'label': 'Outstanding Balance', 'value': f"₹{tot_balance:,.2f}", 'badge': 'warning'},
            {'label': 'Overdue Amount', 'value': f"₹{overdue_balance:,.2f}", 'badge': 'danger'},
        ],
        'suggested_actions': [
            {'label': 'Invoices List', 'url': reverse('sales:invoice_list')},
        ]
    }


def _unassigned_staff_response(now_str: str) -> Dict[str, Any]:
    return {
        'intent': 'STAFF_UNASSIGNED',
        'topic': 'Sales Staff Profile Missing',
        'calculation_timestamp': now_str,
        'data_source_summary': 'SalesStaffProfile lookup.',
        'answer_text': "Your account is marked as Sales Staff, but no linked SalesStaffProfile was found.",
        'metrics': [],
        'suggested_actions': []
    }


# =============================================================================
# CUSTOMER HANDLERS (Strict Self-Service Scope)
# =============================================================================

def _handle_customer_my_profile(user: CustomUser, now_str: str) -> Dict[str, Any]:
    cp = getattr(user, 'customer_profile', None)
    if not cp:
        return _missing_customer_profile_response(now_str)

    rep_name = cp.assigned_staff.user.full_name if cp.assigned_staff and cp.assigned_staff.user else "Dedicated Support Team"

    return {
        'intent': INTENT_CUSTOMER_MY_PROFILE,
        'topic': 'My Customer Profile',
        'calculation_timestamp': now_str,
        'data_source_summary': 'Your personal CustomerProfile and account registration details.',
        'answer_text': (
            f"Hello {user.full_name or user.username}! You are registered under customer code {cp.customer_code} "
            f"({cp.get_customer_type_display()}). Your dedicated representative is {rep_name}."
        ),
        'metrics': [
            {'label': 'Customer Code', 'value': cp.customer_code, 'badge': 'primary'},
            {'label': 'Account Type', 'value': cp.get_customer_type_display(), 'badge': 'secondary'},
            {'label': 'Company', 'value': cp.company or 'Individual Account', 'badge': 'info'},
            {'label': 'Assigned Representative', 'value': rep_name, 'badge': 'success'},
        ],
        'suggested_actions': [
            {'label': 'My Account Overview', 'url': reverse('dashboard:customer_dashboard')},
            {'label': 'Update Profile', 'url': reverse('accounts:profile')},
        ]
    }


def _handle_customer_my_sales(user: CustomUser, now_str: str) -> Dict[str, Any]:
    cp = getattr(user, 'customer_profile', None)
    if not cp:
        return _missing_customer_profile_response(now_str)

    sales = Sale.objects.filter(customer=cp).order_by('-sale_date')
    cnt = sales.count()
    completed_sales = sales.filter(status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED])
    total_spent = completed_sales.aggregate(total=Sum('net_amount'))['total'] or Decimal('0.00')

    table_rows = []
    for s in sales[:10]:
        table_rows.append([
            s.sale_number,
            s.sale_date.strftime('%Y-%m-%d'),
            s.get_status_display(),
            f"₹{s.net_amount:,.2f}"
        ])

    return {
        'intent': INTENT_CUSTOMER_MY_SALES,
        'topic': 'My Purchases & Sales Orders',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Sales orders associated with customer code {cp.customer_code}.",
        'answer_text': f"You have placed {cnt} order(s) with our company, for a completed purchase total of ₹{total_spent:,.2f}.",
        'metrics': [
            {'label': 'Total Orders Placed', 'value': str(cnt), 'badge': 'primary'},
            {'label': 'Total Purchased', 'value': f"₹{total_spent:,.2f}", 'badge': 'success'},
        ],
        'table_data': {
            'headers': ['Order Number', 'Date', 'Status', 'Net Amount'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'View Order History', 'url': reverse('sales:customer_sale_list')},
            {'label': 'Browse Catalog', 'url': reverse('catalog:customer_catalog')},
        ]
    }


def _handle_customer_my_invoices(user: CustomUser, now_str: str) -> Dict[str, Any]:
    cp = getattr(user, 'customer_profile', None)
    if not cp:
        return _missing_customer_profile_response(now_str)

    invoices = Invoice.objects.filter(customer=cp).exclude(invoice_status=Invoice.InvoiceStatus.CANCELLED).order_by('-invoice_date')
    cnt = invoices.count()
    unpaid = invoices.filter(balance_amount__gt=Decimal('0.00'))

    table_rows = []
    for inv in invoices[:10]:
        table_rows.append([
            inv.invoice_number,
            inv.invoice_date.strftime('%Y-%m-%d'),
            inv.due_date.strftime('%Y-%m-%d'),
            f"₹{inv.total_amount:,.2f}",
            f"₹{inv.balance_amount:,.2f}",
            inv.get_payment_status_display()
        ])

    return {
        'intent': INTENT_CUSTOMER_MY_INVOICES,
        'topic': 'My Invoices & Billing History',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Invoices issued to customer code {cp.customer_code}.",
        'answer_text': f"You have {cnt} invoice(s) on file ({unpaid.count()} currently have an outstanding balance).",
        'metrics': [
            {'label': 'Total Invoices', 'value': str(cnt), 'badge': 'primary'},
            {'label': 'Unpaid / Open Invoices', 'value': str(unpaid.count()), 'badge': 'warning' if unpaid.count() > 0 else 'success'},
        ],
        'table_data': {
            'headers': ['Invoice No', 'Issue Date', 'Due Date', 'Total Amount', 'Balance Due', 'Status'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'View Invoices', 'url': reverse('sales:customer_invoice_list')},
        ]
    }


def _handle_customer_my_balance(user: CustomUser, now_str: str) -> Dict[str, Any]:
    cp = getattr(user, 'customer_profile', None)
    if not cp:
        return _missing_customer_profile_response(now_str)

    invoices = Invoice.objects.filter(
        customer=cp,
        balance_amount__gt=Decimal('0.00')
    ).exclude(invoice_status=Invoice.InvoiceStatus.CANCELLED)

    tot_balance = invoices.aggregate(total=Sum('balance_amount'))['total'] or Decimal('0.00')
    overdue_invoices = invoices.filter(invoice_status=Invoice.InvoiceStatus.OVERDUE)
    overdue_amt = overdue_invoices.aggregate(total=Sum('balance_amount'))['total'] or Decimal('0.00')

    if tot_balance == Decimal('0.00'):
        status_msg = "Great news! Your account is completely settled with ₹0.00 outstanding balance."
        badge = "success"
    else:
        status_msg = f"Your current outstanding balance across all open invoices is ₹{tot_balance:,.2f}."
        badge = "warning"

    return {
        'intent': INTENT_CUSTOMER_MY_BALANCE,
        'topic': 'My Outstanding Account Balance',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Calculated strictly from non-cancelled invoices for {cp.customer_code}.",
        'answer_text': status_msg,
        'metrics': [
            {'label': 'Current Outstanding Balance', 'value': f"₹{tot_balance:,.2f}", 'badge': badge},
            {'label': 'Overdue Amount', 'value': f"₹{overdue_amt:,.2f}", 'badge': 'danger' if overdue_amt > 0 else 'secondary'},
            {'label': 'Open Invoices', 'value': str(invoices.count()), 'badge': 'info'},
        ],
        'suggested_actions': [
            {'label': 'View Invoices & Pay', 'url': reverse('sales:customer_invoice_list')},
            {'label': 'My Receipts', 'url': reverse('sales:customer_payment_list')},
        ]
    }


def _handle_customer_my_payments(user: CustomUser, now_str: str) -> Dict[str, Any]:
    cp = getattr(user, 'customer_profile', None)
    if not cp:
        return _missing_customer_profile_response(now_str)

    payments = Payment.objects.filter(
        customer=cp,
        payment_status=Payment.Status.COMPLETED,
        is_reversed=False
    ).order_by('-payment_date')

    total_paid = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    table_rows = []
    for p in payments[:10]:
        table_rows.append([
            p.payment_number,
            p.payment_date.strftime('%Y-%m-%d'),
            f"₹{p.amount:,.2f}",
            p.get_payment_method_display(),
            p.invoice.invoice_number
        ])

    return {
        'intent': INTENT_CUSTOMER_MY_PAYMENTS,
        'topic': 'My Payment Receipts',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Completed, non-reversed payments recorded for {cp.customer_code}.",
        'answer_text': f"We have recorded {payments.count()} successful payment(s) from your account totaling ₹{total_paid:,.2f}.",
        'metrics': [
            {'label': 'Total Payments Completed', 'value': str(payments.count()), 'badge': 'primary'},
            {'label': 'Total Amount Paid', 'value': f"₹{total_paid:,.2f}", 'badge': 'success'},
        ],
        'table_data': {
            'headers': ['Receipt No', 'Payment Date', 'Amount Paid', 'Payment Method', 'Invoice No'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'Payment Receipts', 'url': reverse('sales:customer_payment_list')},
        ]
    }


def _handle_customer_my_reminders(user: CustomUser, now_str: str) -> Dict[str, Any]:
    cp = getattr(user, 'customer_profile', None)
    if not cp:
        return _missing_customer_profile_response(now_str)

    # STRICT SECURITY: Only is_customer_visible=True reminders are ever shown to customers
    reminders = Reminder.objects.filter(
        customer=cp,
        is_customer_visible=True,
        status=Reminder.Status.PENDING
    ).order_by('due_date')

    table_rows = []
    for r in reminders[:10]:
        table_rows.append([
            r.title,
            r.get_reminder_type_display(),
            r.due_date.strftime('%Y-%m-%d'),
            r.get_priority_display()
        ])

    return {
        'intent': INTENT_CUSTOMER_MY_REMINDERS,
        'topic': 'My Scheduled Reminders & Appointments',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Pending reminders marked is_customer_visible=True for {cp.customer_code}. Internal staff notes are strictly excluded.",
        'answer_text': f"You have {reminders.count()} upcoming scheduled notification(s) or appointment(s).",
        'metrics': [
            {'label': 'Upcoming Reminders', 'value': str(reminders.count()), 'badge': 'primary'},
        ],
        'table_data': {
            'headers': ['Subject', 'Type', 'Scheduled Date', 'Priority'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'My Portal Reminders', 'url': reverse('reminders:customer_reminder_list')},
        ]
    }


def _missing_customer_profile_response(now_str: str) -> Dict[str, Any]:
    return {
        'intent': 'CUSTOMER_PROFILE_MISSING',
        'topic': 'Customer Profile Missing',
        'calculation_timestamp': now_str,
        'data_source_summary': 'CustomerProfile lookup.',
        'answer_text': "Your account is marked as Customer, but no linked CustomerProfile was found.",
        'metrics': [],
        'suggested_actions': []
    }


# =============================================================================
# MULTI-ROLE HANDLERS (Forecasting, Recommendations, Catalog, Help)
# =============================================================================

def _handle_forecast_explanation(user: CustomUser, now_str: str) -> Dict[str, Any]:
    staff_profile = getattr(user, 'staff_profile', None) if user.is_sales_staff else None
    forecast_result = generate_sales_forecast(staff_profile=staff_profile, horizon_months=3)

    method = forecast_result.get('method', 'UNKNOWN')
    is_ml = forecast_result.get('is_ml', False)
    num_months = forecast_result.get('num_months_used', 0)
    predictions = forecast_result.get('predictions', [])

    if method == 'LINEAR_REGRESSION':
        method_desc = "Machine Learning (Scikit-learn Linear Regression fitted on continuous monthly CRM sales)"
    elif method == 'MOVING_AVERAGE_FALLBACK':
        method_desc = "Deterministic Rule-Based Moving Average (Historical data is under the 6-month ML threshold)"
    else:
        method_desc = "Insufficient Data (Under 2 historical months available in CRM records)"

    table_rows = []
    for pred in predictions:
        val = pred.get('forecast_value', Decimal('0.00'))
        table_rows.append([pred.get('label', ''), f"₹{val:,.2f}"])

    return {
        'intent': INTENT_FORECAST_EXPLANATION,
        'topic': 'AI Sales Forecast Explanation',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Forecast generated on demand using {num_months} contiguous historical month(s).",
        'answer_text': (
            f"The forecasting engine is currently operating under method: '{method}'. "
            f"Method classification: {method_desc}. Trend direction: {forecast_result.get('trend_direction')}."
        ),
        'metrics': [
            {'label': 'Forecasting Method', 'value': method, 'badge': 'primary'},
            {'label': 'ML Enabled?', 'value': 'Yes' if is_ml else 'No (Rule Fallback)', 'badge': 'success' if is_ml else 'secondary'},
            {'label': 'Months Evaluated', 'value': str(num_months), 'badge': 'info'},
            {'label': 'Trend Direction', 'value': forecast_result.get('trend_direction', 'N/A'), 'badge': 'warning'},
        ],
        'table_data': {
            'headers': ['Forecast Period', 'Projected Revenue'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'Full Forecasting Page', 'url': reverse('reports:sales_forecast')},
        ]
    }


def _handle_product_recommendations(user: CustomUser, params: Dict[str, Any], now_str: str) -> Dict[str, Any]:
    target_customer = None

    if user.is_customer:
        target_customer = getattr(user, 'customer_profile', None)
    elif user.is_sales_staff:
        sp = getattr(user, 'staff_profile', None)
        cust_id = params.get('customer_id')
        if cust_id:
            # IDOR defense: ensure customer belongs to this staff
            target_customer = CustomerProfile.objects.filter(pk=cust_id, assigned_staff=sp, is_active=True).first()
            if not target_customer:
                raise PermissionDenied("You do not have access to view recommendations for this customer.")
        else:
            # Default to first assigned customer
            target_customer = CustomerProfile.objects.filter(assigned_staff=sp, is_active=True).first()
    elif user.is_admin:
        cust_id = params.get('customer_id')
        if cust_id:
            target_customer = CustomerProfile.objects.filter(pk=cust_id, is_active=True).first()
        else:
            target_customer = CustomerProfile.objects.filter(is_active=True).first()

    if not target_customer:
        return {
            'intent': INTENT_PRODUCT_RECOMMENDATIONS,
            'topic': 'Product Recommendations',
            'calculation_timestamp': now_str,
            'data_source_summary': 'Product recommendation engine.',
            'answer_text': "No eligible customer profile was available to evaluate recommendations.",
            'metrics': [],
            'suggested_actions': []
        }

    rec_result = get_recommendations_for_customer(target_customer, user, limit=5)
    recs = rec_result.get('recommendations', [])

    table_rows = []
    for r in recs:
        stock_display = "Consulting Service" if r['is_service'] else f"{r['stock_quantity']} In Stock"
        table_rows.append([
            f"#{r['rank']}",
            r['product_name'],
            r['category_name'],
            f"₹{r['unit_price']:,.2f}",
            stock_display,
            f"{r['score']}/100"
        ])

    return {
        'intent': INTENT_PRODUCT_RECOMMENDATIONS,
        'topic': f"Product Recommendations ({target_customer.user.full_name or target_customer.customer_code})",
        'calculation_timestamp': now_str,
        'data_source_summary': f"Deterministic recommendation engine evaluated {rec_result.get('total_evaluated', 0)} active products/services. {rec_result.get('explanation')}",
        'answer_text': (
            f"Generated {len(recs)} top recommendation(s) for {target_customer.user.full_name or target_customer.customer_code}. "
            f"These rankings are produced by a deterministic business rules engine and are not machine learning."
        ),
        'metrics': [
            {'label': 'Customer Evaluated', 'value': target_customer.customer_code, 'badge': 'primary'},
            {'label': 'Top Score', 'value': f"{recs[0]['score']}/100" if recs else "N/A", 'badge': 'success'},
            {'label': 'Recommendation Type', 'value': 'Deterministic Rules', 'badge': 'info'},
        ],
        'table_data': {
            'headers': ['Rank', 'Item Name', 'Category', 'Unit Price', 'Stock Availability', 'Match Score'],
            'rows': table_rows
        },
        'recommendations': recs,
        'suggested_actions': [
            {'label': 'Browse Full Catalog', 'url': reverse('catalog:product_list') if not user.is_customer else reverse('catalog:customer_catalog')},
        ]
    }


def _handle_catalog_browse(user: CustomUser, now_str: str) -> Dict[str, Any]:
    products = ProductService.objects.filter(
        status=ProductService.Status.ACTIVE,
        category__status=Category.Status.ACTIVE
    ).select_related('category').order_by('product_name')

    total_cnt = products.count()
    table_rows = []
    for p in products[:10]:
        if user.is_customer:
            stock_str = "Available for Order" if (p.item_type == ProductService.ItemType.SERVICE or p.stock_quantity > 0) else "Currently Unavailable"
        else:
            stock_str = "Service" if p.item_type == ProductService.ItemType.SERVICE else f"{p.stock_quantity} units"

        table_rows.append([
            p.product_code,
            p.product_name,
            p.category.category_name,
            p.get_item_type_display(),
            f"₹{p.unit_price:,.2f}",
            stock_str
        ])

    return {
        'intent': INTENT_CATALOG_BROWSE,
        'topic': 'Available Products & Services',
        'calculation_timestamp': now_str,
        'data_source_summary': 'Active items from active product categories.',
        'answer_text': f"There are {total_cnt} active product(s) and service(s) available in the CRM catalog.",
        'metrics': [
            {'label': 'Active Offerings', 'value': str(total_cnt), 'badge': 'primary'},
        ],
        'table_data': {
            'headers': ['Code', 'Name', 'Category', 'Type', 'Unit Price', 'Stock Status'],
            'rows': table_rows
        },
        'suggested_actions': [
            {'label': 'Full Catalog', 'url': reverse('catalog:product_list') if not user.is_customer else reverse('catalog:customer_catalog')},
        ]
    }


def _handle_help(user: CustomUser, now_str: str) -> Dict[str, Any]:
    if user.is_admin:
        topics = [
            "CRM Summary (Overview of customers, revenue, pipeline, stock)",
            "Total Customers (Active corporate vs individual clients)",
            "Lead Pipeline Summary (Leads by pipeline stage, win rate)",
            "High Priority Leads (AI scored priority opportunities)",
            "Leads Needing Follow-up (Stale leads with no touchpoints in 3+ days)",
            "Revenue Summary (Total confirmed orders and net revenue)",
            "Collected and Outstanding (Payments received vs unpaid balances)",
            "Stock Alerts (Low-stock and out-of-stock physical products)",
            "Reminders Summary (Pending and overdue task calendar)",
            "Sales Staff Performance (Revenue and target rankings across reps)",
            "Sales Forecast Explanation (Inspect ML vs Moving Average engine)",
            "Product Recommendations (Deterministic recommendation ranking)",
            "Catalog (Browse active products and consulting services)"
        ]
    elif user.is_sales_staff:
        topics = [
            "My Customers (Your assigned customer account portfolio)",
            "My Leads (Active leads assigned to you)",
            "My High Priority Leads (High-conversion opportunities in your pipeline)",
            "My Follow-ups (Assigned leads with no recent touchpoints)",
            "My Reminders (Your pending and overdue follow-up tasks)",
            "My Sales Performance (Your month-to-date revenue vs assigned target)",
            "My Invoices (Outstanding balances for your assigned clients)",
            "Sales Forecast (Your personal projected revenue horizon)",
            "Product Recommendations (Recommended products for your assigned clients)",
            "Catalog (Browse available products and services for sale)"
        ]
    else:
        topics = [
            "My Profile (Account details and assigned representative)",
            "My Purchases (Your past sales order history)",
            "My Invoices (Billing history and invoice statuses)",
            "My Balance (Current amount owed and payment status)",
            "My Payments (Receipts for completed payments)",
            "My Reminders (Scheduled appointments and notices)",
            "Product Recommendations (Products and services recommended for your account)",
            "Catalog (Browse available products and services)"
        ]

    table_rows = [[f"• {topic}"] for topic in topics]

    return {
        'intent': INTENT_HELP_INTENTS,
        'topic': 'Supported Questions & Guide',
        'calculation_timestamp': now_str,
        'data_source_summary': f"Predefined question directory customized for role: {user.get_role_display()}.",
        'answer_text': (
            f"Welcome to the AI CRM Assistant! I operate offline using deterministic business rules and Django ORM queries. "
            f"Here are the topics you can ask me about as a {user.get_role_display()}:"
        ),
        'table_data': {
            'headers': [f"Supported Topics for {user.get_role_display()}"],
            'rows': table_rows
        },
        'suggested_actions': []
    }


def _handle_unsupported(user: CustomUser, now_str: str) -> Dict[str, Any]:
    return {
        'intent': INTENT_UNSUPPORTED,
        'topic': 'Question Not Recognized',
        'calculation_timestamp': now_str,
        'data_source_summary': 'Intent validation failure.',
        'answer_text': (
            "I could not match your question to a supported CRM query for your account role. "
            "Please select one of the suggested topics below or ask for 'help' to see the full list of supported questions."
        ),
        'metrics': [],
        'suggested_actions': []
    }
