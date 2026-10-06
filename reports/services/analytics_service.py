import calendar
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from accounts.models import CustomerProfile, CustomUser, SalesStaffProfile
from catalog.models import ProductService
from leads.models import Lead
from reminders.models import Reminder
from sales.models import Invoice, Payment, Sale, SaleItem


def get_crm_timezone():
    tz_name = getattr(settings, 'TIME_ZONE', 'Asia/Kolkata')
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.get_current_timezone()


def parse_date_range(preset=None, start_str=None, end_str=None):
    """
    Parses and validates date range parameters.
    Returns: (start_date, end_date, active_preset, error_message)
    start_date and end_date are date objects or None.
    Rejects start_date > end_date and invalid formats gracefully.
    """
    tz = get_crm_timezone()
    today = timezone.now().astimezone(tz).date()
    active_preset = preset or 'all'
    error_message = None

    if start_str or end_str:
        active_preset = 'custom'
        start_d = None
        end_d = None

        if start_str:
            try:
                start_d = datetime.strptime(start_str.strip(), '%Y-%m-%d').date()
            except ValueError:
                return None, None, 'all', "Invalid start date format. Use YYYY-MM-DD."

        if end_str:
            try:
                end_d = datetime.strptime(end_str.strip(), '%Y-%m-%d').date()
            except ValueError:
                return None, None, 'all', "Invalid end date format. Use YYYY-MM-DD."

        if start_d and end_d and start_d > end_d:
            return None, None, 'all', "Start date cannot be after end date."

        return start_d, end_d, 'custom', None

    if preset == 'today':
        return today, today, 'today', None
    elif preset == 'this_month':
        start_d = today.replace(day=1)
        _, last_day = calendar.monthrange(today.year, today.month)
        end_d = today.replace(day=last_day)
        return start_d, end_d, 'this_month', None
    elif preset == 'last_month':
        first_of_this = today.replace(day=1)
        if first_of_this.month == 1:
            prev_year = first_of_this.year - 1
            prev_month = 12
        else:
            prev_year = first_of_this.year
            prev_month = first_of_this.month - 1
        _, last_day = calendar.monthrange(prev_year, prev_month)
        return date(prev_year, prev_month, 1), date(prev_year, prev_month, last_day), 'last_month', None
    elif preset == 'this_quarter':
        quarter = (today.month - 1) // 3 + 1
        start_month = (quarter - 1) * 3 + 1
        end_month = start_month + 2
        _, last_day = calendar.monthrange(today.year, end_month)
        return date(today.year, start_month, 1), date(today.year, end_month, last_day), 'this_quarter', None
    elif preset == 'this_year':
        return date(today.year, 1, 1), date(today.year, 12, 31), 'this_year', None

    return None, None, 'all', None


def to_timezone_datetimes(start_date, end_date):
    """
    Converts optional (start_date, end_date) into timezone-aware datetimes for DateTimeField filtering.
    """
    tz = get_crm_timezone()
    start_dt = None
    end_dt = None
    if start_date:
        start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    if end_date:
        end_dt = timezone.make_aware(datetime.combine(end_date, time.max), tz)
    return start_dt, end_dt


# -------------------------------------------------------------------------
# Central Queryset Filtering (Enforcing Financial Rules)
# -------------------------------------------------------------------------

def get_eligible_sales(staff_profile=None, start_date=None, end_date=None):
    """
    Returns confirmed or completed sales, strictly excluding DRAFT and CANCELLED.
    """
    qs = Sale.objects.filter(status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED])
    if staff_profile:
        qs = qs.filter(sales_staff=staff_profile)
    if start_date:
        qs = qs.filter(sale_date__gte=start_date)
    if end_date:
        qs = qs.filter(sale_date__lte=end_date)
    return qs


def get_eligible_payments(staff_profile=None, start_date=None, end_date=None):
    """
    Returns completed, non-reversed payments.
    When staff_profile is provided, filters payments for customers assigned to that staff member.
    """
    qs = Payment.objects.filter(
        payment_status=Payment.Status.COMPLETED,
        is_reversed=False
    )
    if staff_profile:
        qs = qs.filter(customer__assigned_staff=staff_profile)
    start_dt, end_dt = to_timezone_datetimes(start_date, end_date)
    if start_dt:
        qs = qs.filter(payment_date__gte=start_dt)
    if end_dt:
        qs = qs.filter(payment_date__lte=end_dt)
    return qs


def get_eligible_invoices(staff_profile=None, start_date=None, end_date=None):
    """
    Returns valid invoices, strictly excluding CANCELLED invoices.
    When staff_profile is provided, filters invoices for customers assigned to that staff member.
    """
    qs = Invoice.objects.exclude(invoice_status=Invoice.InvoiceStatus.CANCELLED)
    if staff_profile:
        qs = qs.filter(customer__assigned_staff=staff_profile)
    if start_date:
        qs = qs.filter(invoice_date__gte=start_date)
    if end_date:
        qs = qs.filter(invoice_date__lte=end_date)
    return qs


def get_eligible_leads(staff_profile=None, start_date=None, end_date=None):
    qs = Lead.objects.filter(is_active=True)
    if staff_profile:
        qs = qs.filter(assigned_staff=staff_profile)
    start_dt, end_dt = to_timezone_datetimes(start_date, end_date)
    if start_dt:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(created_at__lte=end_dt)
    return qs


def get_eligible_customers(staff_profile=None, start_date=None, end_date=None):
    qs = CustomerProfile.objects.filter(is_active=True)
    if staff_profile:
        qs = qs.filter(assigned_staff=staff_profile)
    start_dt, end_dt = to_timezone_datetimes(start_date, end_date)
    if start_dt:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(created_at__lte=end_dt)
    return qs


# -------------------------------------------------------------------------
# Admin Analytics KPI Calculation
# -------------------------------------------------------------------------

def calculate_admin_kpis(start_date=None, end_date=None):
    """
    Computes all Admin dashboard KPIs and chart dataset structures.
    """
    # Base querysets
    customers_qs = get_eligible_customers(start_date=start_date, end_date=end_date)
    leads_qs = get_eligible_leads(start_date=start_date, end_date=end_date)
    sales_qs = get_eligible_sales(start_date=start_date, end_date=end_date)
    payments_qs = get_eligible_payments(start_date=start_date, end_date=end_date)
    invoices_qs = get_eligible_invoices(start_date=start_date, end_date=end_date)

    # 1. Total active customers
    total_customers = customers_qs.count()

    # 2. Active leads (excluding WON and LOST)
    active_leads_count = leads_qs.exclude(status__in=[Lead.Status.WON, Lead.Status.LOST]).count()

    # 3. Revenue from confirmed/completed sales
    sales_agg = sales_qs.aggregate(
        total_revenue=Sum('net_amount'),
        order_count=Count('id')
    )
    confirmed_revenue = sales_agg['total_revenue'] or Decimal('0.00')
    sales_order_count = sales_agg['order_count'] or 0

    # 4. Collected payments
    payments_agg = payments_qs.aggregate(total_collected=Sum('amount'))
    collected_payments = payments_agg['total_collected'] or Decimal('0.00')

    # 5. Outstanding invoice balance
    invoices_agg = invoices_qs.aggregate(total_balance=Sum('balance_amount'))
    outstanding_balance = invoices_agg['total_balance'] or Decimal('0.00')

    # 6. Average Order Value (AOV)
    if sales_order_count > 0:
        average_order_value = (confirmed_revenue / Decimal(sales_order_count)).quantize(Decimal('0.01'))
    else:
        average_order_value = Decimal('0.00')

    # 7. Conversion Rate: WON / (WON + LOST) * 100
    won_leads = leads_qs.filter(status=Lead.Status.WON).count()
    lost_leads = leads_qs.filter(status=Lead.Status.LOST).count()
    closed_leads = won_leads + lost_leads
    if closed_leads > 0:
        conversion_rate = round((won_leads / closed_leads) * 100.0, 2)
    else:
        conversion_rate = 0.0

    total_leads_count = leads_qs.count()
    pipeline_win_rate = round((won_leads / total_leads_count) * 100.0, 2) if total_leads_count > 0 else 0.0

    # 8. Low-stock and out-of-stock (physical active products)
    physical_products = ProductService.objects.filter(
        item_type=ProductService.ItemType.PRODUCT,
        status=ProductService.Status.ACTIVE
    )
    out_of_stock_count = physical_products.filter(stock_quantity=0).count()
    low_stock_count = physical_products.filter(stock_quantity__gt=0, stock_quantity__lte=F('reorder_level')).count()

    # 9. Revenue by Sales Staff
    staff_breakdown = list(sales_qs.values(
        'sales_staff__id',
        'sales_staff__staff_code',
        'sales_staff__user__full_name',
        'sales_staff__user__username'
    ).annotate(
        total_rev=Sum('net_amount'),
        order_cnt=Count('id')
    ).order_by('-total_rev'))

    staff_revenue_list = []
    for row in staff_breakdown:
        name = row['sales_staff__user__full_name'] or row['sales_staff__user__username'] or row['sales_staff__staff_code'] or 'Unassigned'
        staff_revenue_list.append({
            'staff_id': row['sales_staff__id'],
            'staff_code': row['sales_staff__staff_code'],
            'staff_name': name,
            'total_revenue': row['total_rev'] or Decimal('0.00'),
            'order_count': row['order_cnt']
        })

    # 10. Top 5 Products by Revenue
    top_items_qs = SaleItem.objects.filter(
        sale__in=sales_qs
    ).values(
        'product__id',
        'product__product_code',
        'product__product_name',
        'product__item_type'
    ).annotate(
        qty_sold=Sum('quantity'),
        rev_generated=Sum('line_total')
    ).order_by('-rev_generated')[:5]

    top_products_list = []
    for item in top_items_qs:
        top_products_list.append({
            'product_id': item['product__id'],
            'product_code': item['product__product_code'],
            'product_name': item['product__product_name'],
            'item_type': item['product__item_type'],
            'units_sold': item['qty_sold'] or 0,
            'total_revenue': item['rev_generated'] or Decimal('0.00'),
        })

    # 11. Sales Status Distribution (All sales within date range)
    raw_sales_in_range = Sale.objects.all()
    if start_date:
        raw_sales_in_range = raw_sales_in_range.filter(sale_date__gte=start_date)
    if end_date:
        raw_sales_in_range = raw_sales_in_range.filter(sale_date__lte=end_date)
    status_counts_raw = dict(raw_sales_in_range.values('status').annotate(cnt=Count('id')).values_list('status', 'cnt'))
    sales_by_status = {
        st.value: status_counts_raw.get(st.value, 0)
        for st in Sale.Status
    }

    # 12. Lead Pipeline Distribution
    lead_stages_raw = dict(leads_qs.values('status').annotate(cnt=Count('id')).values_list('status', 'cnt'))
    lead_pipeline_distribution = {
        st.value: lead_stages_raw.get(st.value, 0)
        for st in Lead.Status
    }

    # 13. Invoice Payment-State Summary
    inv_pay_status_raw = dict(invoices_qs.values('payment_status').annotate(cnt=Count('id')).values_list('payment_status', 'cnt'))
    invoice_payment_states = {
        st.value: inv_pay_status_raw.get(st.value, 0)
        for st in Invoice.PaymentStatus
    }
    overdue_invoices_count = invoices_qs.filter(invoice_status=Invoice.InvoiceStatus.OVERDUE).count()

    # 14. Monthly Revenue Trend (Timeline)
    # Group eligible sales by year and month
    monthly_sales = list(
        sales_qs.values('sale_date__year', 'sale_date__month')
        .annotate(month_rev=Sum('net_amount'), order_cnt=Count('id'))
        .order_by('sale_date__year', 'sale_date__month')
    )
    revenue_trend_labels = []
    revenue_trend_data = []
    for entry in monthly_sales:
        yr = entry['sale_date__year']
        mo = entry['sale_date__month']
        month_label = f"{calendar.month_abbr[mo]} {yr}"
        revenue_trend_labels.append(month_label)
        revenue_trend_data.append(float(entry['month_rev'] or Decimal('0.00')))

    return {
        'total_customers': total_customers,
        'active_leads_count': active_leads_count,
        'won_leads_count': won_leads,
        'lost_leads_count': lost_leads,
        'total_leads_count': total_leads_count,
        'closed_leads_count': closed_leads,
        'conversion_rate': conversion_rate,
        'pipeline_win_rate': pipeline_win_rate,
        'confirmed_revenue': confirmed_revenue,
        'collected_payments': collected_payments,
        'outstanding_balance': outstanding_balance,
        'sales_order_count': sales_order_count,
        'average_order_value': average_order_value,
        'low_stock_count': low_stock_count,
        'out_of_stock_count': out_of_stock_count,
        'overdue_invoices_count': overdue_invoices_count,
        'staff_revenue_list': staff_revenue_list,
        'top_products_list': top_products_list,
        'sales_by_status': sales_by_status,
        'lead_pipeline_distribution': lead_pipeline_distribution,
        'invoice_payment_states': invoice_payment_states,
        'revenue_trend_labels': revenue_trend_labels,
        'revenue_trend_data': revenue_trend_data,
    }


# -------------------------------------------------------------------------
# Sales Staff Analytics KPI Calculation (Strictly Scoped)
# -------------------------------------------------------------------------

def calculate_staff_kpis(staff_profile, start_date=None, end_date=None):
    """
    Computes personal analytics for a single SalesStaffProfile.
    Directly isolates sales to staff_profile and customer-invoices to assigned customers.
    """
    tz = get_crm_timezone()
    today = timezone.now().astimezone(tz).date()

    # Base querysets strictly filtered to this staff member
    personal_sales = get_eligible_sales(staff_profile=staff_profile, start_date=start_date, end_date=end_date)
    assigned_leads = get_eligible_leads(staff_profile=staff_profile, start_date=start_date, end_date=end_date)
    assigned_customers = get_eligible_customers(staff_profile=staff_profile, start_date=start_date, end_date=end_date)
    assigned_invoices = get_eligible_invoices(staff_profile=staff_profile, start_date=start_date, end_date=end_date)
    assigned_payments = get_eligible_payments(staff_profile=staff_profile, start_date=start_date, end_date=end_date)

    # 1. Assigned Counts
    assigned_customers_count = assigned_customers.count()
    assigned_leads_count = assigned_leads.count()
    assigned_active_leads = assigned_leads.exclude(status__in=[Lead.Status.WON, Lead.Status.LOST]).count()

    # 2. Personal Sales & Revenue
    personal_sales_agg = personal_sales.aggregate(
        total_revenue=Sum('net_amount'),
        order_count=Count('id')
    )
    personal_revenue = personal_sales_agg['total_revenue'] or Decimal('0.00')
    personal_sales_count = personal_sales_agg['order_count'] or 0

    if personal_sales_count > 0:
        personal_aov = (personal_revenue / Decimal(personal_sales_count)).quantize(Decimal('0.01'))
    else:
        personal_aov = Decimal('0.00')

    # 3. Monthly Target & Current Month Achievement
    assigned_target = staff_profile.assigned_target or Decimal('0.00')
    curr_month_sales = Sale.objects.filter(
        sales_staff=staff_profile,
        status__in=[Sale.Status.CONFIRMED, Sale.Status.COMPLETED],
        sale_date__year=today.year,
        sale_date__month=today.month
    )
    current_month_achieved = curr_month_sales.aggregate(total=Sum('net_amount'))['total'] or Decimal('0.00')

    if assigned_target > Decimal('0.00'):
        target_achievement_pct = round(float(current_month_achieved / assigned_target) * 100.0, 2)
    else:
        target_achievement_pct = 0.0
    target_variance = current_month_achieved - assigned_target

    # 4. Conversion Rate
    won_leads = assigned_leads.filter(status=Lead.Status.WON).count()
    lost_leads = assigned_leads.filter(status=Lead.Status.LOST).count()
    closed_leads = won_leads + lost_leads
    if closed_leads > 0:
        personal_conversion_rate = round((won_leads / closed_leads) * 100.0, 2)
    else:
        personal_conversion_rate = 0.0

    # 5. Pending Reminders for this staff user
    pending_reminders_count = Reminder.objects.filter(
        assigned_to=staff_profile.user,
        status=Reminder.Status.PENDING
    ).count()

    # 6. Outstanding Invoices for Assigned Customers
    outstanding_invoices_balance = assigned_invoices.aggregate(
        total=Sum('balance_amount')
    )['total'] or Decimal('0.00')

    # 7. Collected Payments from Assigned Customers
    collected_payments = assigned_payments.aggregate(
        total=Sum('amount')
    )['total'] or Decimal('0.00')

    # 8. Top Products Sold Personally
    top_items_qs = SaleItem.objects.filter(
        sale__in=personal_sales
    ).values(
        'product__id',
        'product__product_code',
        'product__product_name',
        'product__item_type'
    ).annotate(
        qty_sold=Sum('quantity'),
        rev_generated=Sum('line_total')
    ).order_by('-rev_generated')[:5]

    top_products_sold = []
    for item in top_items_qs:
        top_products_sold.append({
            'product_id': item['product__id'],
            'product_code': item['product__product_code'],
            'product_name': item['product__product_name'],
            'item_type': item['product__item_type'],
            'units_sold': item['qty_sold'] or 0,
            'total_revenue': item['rev_generated'] or Decimal('0.00'),
        })

    # 9. Monthly Performance Trend
    monthly_sales = list(
        personal_sales.values('sale_date__year', 'sale_date__month')
        .annotate(month_rev=Sum('net_amount'), order_cnt=Count('id'))
        .order_by('sale_date__year', 'sale_date__month')
    )
    performance_trend_labels = []
    performance_trend_data = []
    for entry in monthly_sales:
        yr = entry['sale_date__year']
        mo = entry['sale_date__month']
        month_label = f"{calendar.month_abbr[mo]} {yr}"
        performance_trend_labels.append(month_label)
        performance_trend_data.append(float(entry['month_rev'] or Decimal('0.00')))

    # 10. Assigned Leads Breakdown
    lead_stages_raw = dict(assigned_leads.values('status').annotate(cnt=Count('id')).values_list('status', 'cnt'))
    assigned_leads_by_status = {
        st.value: lead_stages_raw.get(st.value, 0)
        for st in Lead.Status
    }

    return {
        'staff_profile': staff_profile,
        'assigned_customers_count': assigned_customers_count,
        'assigned_leads_count': assigned_leads_count,
        'assigned_active_leads': assigned_active_leads,
        'won_leads_count': won_leads,
        'lost_leads_count': lost_leads,
        'personal_sales_count': personal_sales_count,
        'personal_revenue': personal_revenue,
        'personal_aov': personal_aov,
        'assigned_target': assigned_target,
        'current_month_achieved': current_month_achieved,
        'target_achievement_pct': target_achievement_pct,
        'target_variance': target_variance,
        'personal_conversion_rate': personal_conversion_rate,
        'pending_reminders_count': pending_reminders_count,
        'outstanding_invoices_balance': outstanding_invoices_balance,
        'collected_payments': collected_payments,
        'top_products_sold': top_products_sold,
        'performance_trend_labels': performance_trend_labels,
        'performance_trend_data': performance_trend_data,
        'assigned_leads_by_status': assigned_leads_by_status,
    }
