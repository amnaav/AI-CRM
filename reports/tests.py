import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import io
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
import openpyxl

from accounts.models import CustomerProfile, CustomUser, SalesStaffProfile
from activity_logs.models import ActivityLog
from catalog.models import Category, ProductService
from interactions.models import Interaction
from leads.models import Lead
from reminders.models import Reminder
from reports.services.analytics_service import (
    calculate_admin_kpis,
    calculate_staff_kpis,
    get_crm_timezone,
    get_eligible_invoices,
    get_eligible_payments,
    get_eligible_sales,
    parse_date_range,
)
from reports.services.export_service import (
    CSV_MAX_ROWS,
    EXCEL_MAX_ROWS,
    PDF_MAX_ROWS,
    generate_csv_response,
    generate_excel_response,
    generate_pdf_response,
    sanitize_cell_value,
    sanitize_filename,
)
from reports.services.forecast_service import _add_months, generate_sales_forecast
from sales.models import Invoice, Payment, Sale, SaleItem


User = get_user_model()


class BaseReportsTestSetup(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Create Users with distinct roles
        cls.admin_user = User.objects.create_superuser(
            username='admin_reports',
            email='admin_reports@example.com',
            password='TestPassword123!',
            full_name='Admin Commander'
        )

        cls.staff_user_1 = User.objects.create_user(
            username='staff_alpha',
            email='staff_alpha@example.com',
            password='TestPassword123!',
            role=CustomUser.Role.SALES_STAFF,
            full_name='Alpha Representative'
        )
        cls.staff_profile_1 = SalesStaffProfile.objects.create(
            user=cls.staff_user_1,
            staff_code='STF-ALPHA',
            assigned_target=Decimal('50000.00'),
            department='Field Sales'
        )

        cls.staff_user_2 = User.objects.create_user(
            username='staff_beta',
            email='staff_beta@example.com',
            password='TestPassword123!',
            role=CustomUser.Role.SALES_STAFF,
            full_name='Beta Representative'
        )
        cls.staff_profile_2 = SalesStaffProfile.objects.create(
            user=cls.staff_user_2,
            staff_code='STF-BETA',
            assigned_target=Decimal('30000.00'),
            department='Inside Sales'
        )

        cls.cust_user_1 = User.objects.create_user(
            username='cust_acme',
            email='acme@example.com',
            password='TestPassword123!',
            role=CustomUser.Role.CUSTOMER,
            full_name='Acme Corporation User'
        )
        cls.customer_1 = CustomerProfile.objects.create(
            user=cls.cust_user_1,
            customer_code='CUST-ACME',
            company='Acme Corp',
            customer_type=CustomerProfile.CustomerType.BUSINESS,
            assigned_staff=cls.staff_profile_1,
            is_active=True
        )

        cls.cust_user_2 = User.objects.create_user(
            username='cust_beta_client',
            email='beta_client@example.com',
            password='TestPassword123!',
            role=CustomUser.Role.CUSTOMER,
            full_name='Beta Client User'
        )
        cls.customer_2 = CustomerProfile.objects.create(
            user=cls.cust_user_2,
            customer_code='CUST-BETA',
            company='Beta Industries',
            customer_type=CustomerProfile.CustomerType.BUSINESS,
            assigned_staff=cls.staff_profile_2,
            is_active=True
        )

        # Inactive Customer
        cls.cust_user_inactive = User.objects.create_user(
            username='cust_inactive',
            email='inactive@example.com',
            password='TestPassword123!',
            role=CustomUser.Role.CUSTOMER,
            full_name='Inactive Client'
        )
        cls.customer_inactive = CustomerProfile.objects.create(
            user=cls.cust_user_inactive,
            customer_code='CUST-INACTIVE',
            is_active=False
        )

        # Catalog
        cls.category = Category.objects.create(category_name='Software Solutions')
        cls.product_1 = ProductService.objects.create(
            product_code='PRD-CRM',
            product_name='Cloud CRM Enterprise',
            category=cls.category,
            item_type=ProductService.ItemType.PRODUCT,
            unit_price=Decimal('1000.00'),
            stock_quantity=10,
            reorder_level=5
        )
        cls.product_low_stock = ProductService.objects.create(
            product_code='PRD-ROUTER',
            product_name='Edge Router',
            category=cls.category,
            item_type=ProductService.ItemType.PRODUCT,
            unit_price=Decimal('500.00'),
            stock_quantity=3,
            reorder_level=5
        )
        cls.product_out_stock = ProductService.objects.create(
            product_code='PRD-SERVER',
            product_name='Rack Server',
            category=cls.category,
            item_type=ProductService.ItemType.PRODUCT,
            unit_price=Decimal('5000.00'),
            stock_quantity=0,
            reorder_level=2
        )
        cls.service_item = ProductService.objects.create(
            product_code='SRV-CONSULT',
            product_name='Architecture Consulting',
            category=cls.category,
            item_type=ProductService.ItemType.SERVICE,
            unit_price=Decimal('2000.00'),
            stock_quantity=0,
            reorder_level=0
        )


class KPICalculationTests(BaseReportsTestSetup):
    def test_zero_dataset_kpi_fallbacks(self):
        """When no sales or invoices exist, formulas must return exact 0 without division errors."""
        kpis = calculate_admin_kpis()
        self.assertEqual(kpis['confirmed_revenue'], Decimal('0.00'))
        self.assertEqual(kpis['collected_payments'], Decimal('0.00'))
        self.assertEqual(kpis['outstanding_balance'], Decimal('0.00'))
        self.assertEqual(kpis['sales_order_count'], 0)
        self.assertEqual(kpis['average_order_value'], Decimal('0.00'))
        self.assertEqual(kpis['conversion_rate'], 0.0)
        self.assertEqual(kpis['pipeline_win_rate'], 0.0)
        # Active customers only
        self.assertEqual(kpis['total_customers'], 2)
        self.assertEqual(kpis['low_stock_count'], 1)
        self.assertEqual(kpis['out_of_stock_count'], 1)

    def test_lead_conversion_rate_exact_formula(self):
        """Conversion rate must be WON / (WON + LOST) * 100, returning 0.0 when closed_leads == 0."""
        # 1 open lead -> closed = 0 -> 0.0%
        Lead.objects.create(
            lead_code='LED-OPEN-1',
            full_name='Open Lead',
            email='open@example.com',
            phone='1234567890',
            status=Lead.Status.NEW,
            assigned_staff=self.staff_profile_1
        )
        kpis = calculate_admin_kpis()
        self.assertEqual(kpis['conversion_rate'], 0.0)
        self.assertEqual(kpis['pipeline_win_rate'], 0.0)
        self.assertEqual(kpis['active_leads_count'], 1)

        # Add 1 WON and 1 LOST lead -> 1 WON / (1 + 1) = 50.0%
        Lead.objects.create(
            lead_code='LED-WON-1',
            full_name='Won Lead',
            email='won@example.com',
            phone='1234567891',
            status=Lead.Status.WON,
            assigned_staff=self.staff_profile_1
        )
        Lead.objects.create(
            lead_code='LED-LOST-1',
            full_name='Lost Lead',
            email='lost@example.com',
            phone='1234567892',
            status=Lead.Status.LOST,
            assigned_staff=self.staff_profile_1
        )

        kpis2 = calculate_admin_kpis()
        self.assertEqual(kpis2['conversion_rate'], 50.0)
        # 1 WON out of 3 total leads = 33.33%
        self.assertEqual(kpis2['pipeline_win_rate'], 33.33)
        self.assertEqual(kpis2['active_leads_count'], 1)

    def test_financial_status_exclusions(self):
        """
        Draft and Cancelled sales are excluded from revenue.
        Reversed payments are excluded from collections.
        Cancelled invoices are excluded from receivables.
        """
        today = timezone.localdate()

        # 1. Confirmed Sale
        s_confirmed = Sale.objects.create(
            customer=self.customer_1,
            sales_staff=self.staff_profile_1,
            sale_date=today,
            status=Sale.Status.CONFIRMED,
            stock_deducted=True,
            subtotal=Decimal('1000.00'),
            taxable_amount=Decimal('1000.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('180.00'),
            net_amount=Decimal('1180.00')
        )
        SaleItem.objects.create(
            sale=s_confirmed,
            product=self.product_1,
            quantity=1,
            unit_price=Decimal('1000.00')
        )

        # 2. Completed Sale
        s_completed = Sale.objects.create(
            customer=self.customer_1,
            sales_staff=self.staff_profile_1,
            sale_date=today,
            status=Sale.Status.COMPLETED,
            stock_deducted=True,
            subtotal=Decimal('2000.00'),
            taxable_amount=Decimal('2000.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('360.00'),
            net_amount=Decimal('2360.00')
        )

        # 3. Draft Sale (MUST BE EXCLUDED)
        Sale.objects.create(
            customer=self.customer_1,
            sales_staff=self.staff_profile_1,
            sale_date=today,
            status=Sale.Status.DRAFT,
            stock_deducted=False,
            subtotal=Decimal('5000.00'),
            taxable_amount=Decimal('5000.00'),
            tax_amount=Decimal('900.00'),
            net_amount=Decimal('5900.00')
        )

        # 4. Cancelled Sale (MUST BE EXCLUDED)
        Sale.objects.create(
            customer=self.customer_1,
            sales_staff=self.staff_profile_1,
            sale_date=today,
            status=Sale.Status.CANCELLED,
            stock_deducted=False,
            subtotal=Decimal('3000.00'),
            taxable_amount=Decimal('3000.00'),
            tax_amount=Decimal('540.00'),
            net_amount=Decimal('3540.00')
        )

        # Verify eligible sales
        eligible = get_eligible_sales()
        self.assertEqual(eligible.count(), 2)
        self.assertNotIn(Sale.Status.DRAFT, [s.status for s in eligible])
        self.assertNotIn(Sale.Status.CANCELLED, [s.status for s in eligible])

        # Verify revenue sum: 1180 + 2360 = 3540.00
        kpis = calculate_admin_kpis()
        self.assertEqual(kpis['confirmed_revenue'], Decimal('3540.00'))
        self.assertEqual(kpis['sales_order_count'], 2)
        # AOV = 3540.00 / 2 = 1770.00
        self.assertEqual(kpis['average_order_value'], Decimal('1770.00'))

        # Invoices: 1 Issued, 1 Cancelled
        inv_issued = Invoice.objects.create(
            sale=s_confirmed,
            customer=self.customer_1,
            invoice_date=today,
            due_date=today + timedelta(days=15),
            subtotal=Decimal('1000.00'),
            taxable_amount=Decimal('1000.00'),
            tax_amount=Decimal('180.00'),
            total_amount=Decimal('1180.00'),
            paid_amount=Decimal('500.00'),
            balance_amount=Decimal('680.00'),
            payment_status=Invoice.PaymentStatus.PARTIALLY_PAID,
            invoice_status=Invoice.InvoiceStatus.ISSUED
        )
        Invoice.objects.create(
            sale=s_completed,
            customer=self.customer_1,
            invoice_date=today,
            due_date=today + timedelta(days=15),
            subtotal=Decimal('2000.00'),
            taxable_amount=Decimal('2000.00'),
            tax_amount=Decimal('360.00'),
            total_amount=Decimal('2360.00'),
            paid_amount=Decimal('0.00'),
            balance_amount=Decimal('2360.00'),
            invoice_status=Invoice.InvoiceStatus.CANCELLED  # Must be excluded!
        )

        kpis_inv = calculate_admin_kpis()
        # Outstanding balance must be only 680.00 (from issued invoice), NOT 680 + 2360
        self.assertEqual(kpis_inv['outstanding_balance'], Decimal('680.00'))

        # Payments: 1 Completed, 1 Reversed
        Payment.objects.create(
            invoice=inv_issued,
            customer=self.customer_1,
            idempotency_key='PAY-IDEM-VALID',
            amount=Decimal('500.00'),
            payment_status=Payment.Status.COMPLETED,
            is_reversed=False,
            payment_method=Payment.Method.CASH
        )
        Payment.objects.create(
            invoice=inv_issued,
            customer=self.customer_1,
            idempotency_key='PAY-IDEM-REV',
            amount=Decimal('300.00'),
            payment_status=Payment.Status.REVERSED,
            is_reversed=True,
            reversed_at=timezone.now(),
            reversal_reason='Wrong UTR reference',
            payment_method=Payment.Method.CASH
        )

        kpis_pay = calculate_admin_kpis()
        # Collected payments must only be 500.00, reversed 300.00 must be excluded
        self.assertEqual(kpis_pay['collected_payments'], Decimal('500.00'))

    def test_date_range_validation_and_rejection(self):
        """Rejects start_date > end_date and invalid formats gracefully."""
        # Valid range
        s, e, preset, err = parse_date_range(None, '2026-01-01', '2026-01-31')
        self.assertIsNone(err)
        self.assertEqual(s, date(2026, 1, 1))
        self.assertEqual(e, date(2026, 1, 31))

        # Reversed range
        s, e, preset, err = parse_date_range(None, '2026-02-01', '2026-01-01')
        self.assertEqual(err, "Start date cannot be after end date.")

        # Invalid format
        s, e, preset, err = parse_date_range(None, 'not-a-date', '2026-01-01')
        self.assertEqual(err, "Invalid start date format. Use YYYY-MM-DD.")


class StaffScopingAndDataIsolationTests(BaseReportsTestSetup):
    def test_staff_analytics_data_isolation(self):
        """Sales staff must see only their own handled sales, assigned leads, and assigned customer invoices."""
        today = timezone.localdate()

        # Staff 1 Sale
        s1 = Sale.objects.create(
            customer=self.customer_1,
            sales_staff=self.staff_profile_1,
            sale_date=today,
            status=Sale.Status.CONFIRMED,
            stock_deducted=True,
            subtotal=Decimal('5000.00'),
            taxable_amount=Decimal('5000.00'),
            tax_amount=Decimal('900.00'),
            net_amount=Decimal('5900.00')
        )
        # Staff 2 Sale
        s2 = Sale.objects.create(
            customer=self.customer_2,
            sales_staff=self.staff_profile_2,
            sale_date=today,
            status=Sale.Status.CONFIRMED,
            stock_deducted=True,
            subtotal=Decimal('20000.00'),
            taxable_amount=Decimal('20000.00'),
            tax_amount=Decimal('3600.00'),
            net_amount=Decimal('23600.00')
        )

        # Calculate staff 1 KPIs
        kpis_staff_1 = calculate_staff_kpis(self.staff_profile_1)
        self.assertEqual(kpis_staff_1['personal_revenue'], Decimal('5900.00'))
        self.assertEqual(kpis_staff_1['personal_sales_count'], 1)
        self.assertEqual(kpis_staff_1['assigned_customers_count'], 1)

        # Calculate staff 2 KPIs
        kpis_staff_2 = calculate_staff_kpis(self.staff_profile_2)
        self.assertEqual(kpis_staff_2['personal_revenue'], Decimal('23600.00'))
        self.assertEqual(kpis_staff_2['personal_sales_count'], 1)
        self.assertEqual(kpis_staff_2['assigned_customers_count'], 1)

        # Target achievement for Staff 1: 5900 / 50000 = 11.8%
        self.assertEqual(kpis_staff_1['target_achievement_pct'], 11.8)
        self.assertEqual(kpis_staff_1['target_variance'], Decimal('-44100.00'))


class ExportSecurityAndCorrectnessTests(BaseReportsTestSetup):
    def test_formula_injection_escaping(self):
        """Formula injection characters =, @, tab, cr, and formula-like + / - are escaped on text values."""
        self.assertEqual(sanitize_cell_value("=1+1"), "'=1+1")
        self.assertEqual(sanitize_cell_value("@SUM(A1:A5)"), "'@SUM(A1:A5)")
        self.assertEqual(sanitize_cell_value("\tmalicious_tab"), "'\tmalicious_tab")
        self.assertEqual(sanitize_cell_value("\rmalicious_cr"), "'\rmalicious_cr")
        self.assertEqual(sanitize_cell_value("+cmd|' /C calc'!A0"), "'+cmd|' /C calc'!A0")
        self.assertEqual(sanitize_cell_value("-cmd|' /C calc'!A0"), "'-cmd|' /C calc'!A0")

    def test_numeric_preservation_not_converted_to_escaped_text(self):
        """Legitimate numbers (including negative numbers and Decimals) must remain numbers."""
        self.assertEqual(sanitize_cell_value(Decimal('-500.00')), Decimal('-500.00'))
        self.assertEqual(sanitize_cell_value(Decimal('1250.75')), Decimal('1250.75'))
        self.assertEqual(sanitize_cell_value(-42), -42)
        self.assertEqual(sanitize_cell_value(100), 100)
        self.assertEqual(sanitize_cell_value("-123.45"), "-123.45")  # Valid numeric string

    def test_export_row_limits_and_truncation_notices(self):
        """Row limits (5000 for CSV/Excel, 2500 for PDF) slice data and show notices."""
        large_rows = [["PRD-" + str(i), "Product " + str(i), Decimal('100.00')] for i in range(5005)]

        # CSV limit test
        csv_resp = generate_csv_response("Large CSV", ["Code", "Name", "Price"], large_rows, "test_csv")
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn(b"Notice: Export truncated at maximum limit of 5,000 rows", csv_resp.content)

        # Excel limit test
        excel_resp = generate_excel_response("Large Excel", ["Code", "Name", "Price"], large_rows, "test_excel")
        self.assertEqual(excel_resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(excel_resp.content))
        ws = wb.active
        self.assertIn("Export truncated at maximum limit of 5,000 rows", ws.cell(row=4, column=1).value)

        # PDF limit test (2500)
        large_pdf_rows = [["PRD-" + str(i), "Product " + str(i), Decimal('100.00')] for i in range(2505)]
        pdf_resp = generate_pdf_response("Large PDF", ["Code", "Name", "Price"], large_pdf_rows, "test_pdf")
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertTrue(pdf_resp.content.startswith(b"%PDF-"))

    def test_activity_log_export_creation(self):
        """Every export download must create an immutable ActivityLog entry with action='EXPORT'."""
        initial_count = ActivityLog.objects.filter(action=ActivityLog.Action.EXPORT).count()
        generate_csv_response("Audit Report", ["A", "B"], [["val1", "val2"]], "audit_test")
        new_count = ActivityLog.objects.filter(action=ActivityLog.Action.EXPORT).count()
        self.assertEqual(new_count, initial_count + 1)
        latest_log = ActivityLog.objects.filter(action=ActivityLog.Action.EXPORT).first()
        self.assertEqual(latest_log.module_name, "Reports")
        self.assertEqual(latest_log.metadata['format'], 'csv')
        self.assertEqual(latest_log.metadata['row_count'], 1)


class SalesForecastingTests(BaseReportsTestSetup):
    def test_forecast_insufficient_data_0_and_1_month(self):
        """0 and 1 month history returns INSUFFICIENT_DATA with all projections at 0.00."""
        # 0 months
        res_0 = generate_sales_forecast()
        self.assertEqual(res_0['status'], 'INSUFFICIENT_DATA')
        self.assertFalse(res_0['is_ml'])
        self.assertEqual(res_0['num_months_used'], 0)
        for pred in res_0['predictions']:
            self.assertEqual(pred['forecast_value'], Decimal('0.00'))

        # 1 month
        Sale.objects.create(
            customer=self.customer_1,
            sales_staff=self.staff_profile_1,
            sale_date=date(2026, 1, 15),
            status=Sale.Status.CONFIRMED,
            stock_deducted=True,
            subtotal=Decimal('1000.00'),
            taxable_amount=Decimal('1000.00'),
            net_amount=Decimal('1000.00')
        )
        res_1 = generate_sales_forecast()
        self.assertEqual(res_1['status'], 'INSUFFICIENT_DATA')
        self.assertFalse(res_1['is_ml'])
        self.assertEqual(res_1['num_months_used'], 1)
        for pred in res_1['predictions']:
            self.assertEqual(pred['forecast_value'], Decimal('0.00'))

    def test_forecast_moving_average_fallback_2_to_5_months(self):
        """2 to 5 months engages MOVING_AVERAGE_FALLBACK with is_ml=False."""
        # Seed 3 contiguous months: Jan=1000, Feb=2000, Mar=3000 -> SMA = (1000+2000+3000)/3 = 2000.00
        Sale.objects.create(customer=self.customer_1, sales_staff=self.staff_profile_1, sale_date=date(2026, 1, 10), status=Sale.Status.CONFIRMED, stock_deducted=True, net_amount=Decimal('1000.00'))
        Sale.objects.create(customer=self.customer_1, sales_staff=self.staff_profile_1, sale_date=date(2026, 2, 10), status=Sale.Status.CONFIRMED, stock_deducted=True, net_amount=Decimal('2000.00'))
        Sale.objects.create(customer=self.customer_1, sales_staff=self.staff_profile_1, sale_date=date(2026, 3, 10), status=Sale.Status.CONFIRMED, stock_deducted=True, net_amount=Decimal('3000.00'))

        res = generate_sales_forecast()
        self.assertEqual(res['status'], 'FALLBACK')
        self.assertEqual(res['method'], 'MOVING_AVERAGE_FALLBACK')
        self.assertFalse(res['is_ml'])
        self.assertEqual(res['num_months_used'], 3)
        # Check that predictions are set to moving average 2000.00
        for pred in res['predictions']:
            self.assertEqual(pred['forecast_value'], Decimal('2000.00'))

    def test_forecast_linear_regression_activation_6_months(self):
        """6 or more contiguous months activates Scikit-Learn Linear Regression with is_ml=True."""
        # Seed 6 contiguous months with upward trend: 1000, 2000, 3000, 4000, 5000, 6000
        for m in range(1, 7):
            Sale.objects.create(
                customer=self.customer_1,
                sales_staff=self.staff_profile_1,
                sale_date=date(2026, m, 15),
                status=Sale.Status.CONFIRMED,
                stock_deducted=True,
                net_amount=Decimal(m * 1000)
            )

        res = generate_sales_forecast()
        self.assertEqual(res['status'], 'ACTIVE')
        self.assertEqual(res['method'], 'LINEAR_REGRESSION')
        self.assertTrue(res['is_ml'])
        self.assertEqual(res['num_months_used'], 6)
        self.assertEqual(res['r_squared'], 1.0)  # Perfect line
        self.assertEqual(res['trend_direction'], 'Upward / Growing')
        self.assertEqual(res['monthly_slope'], Decimal('1000.00'))

        # Projections for month 7, 8, 9 should be 7000.00, 8000.00, 9000.00
        self.assertEqual(res['predictions'][0]['forecast_value'], Decimal('7000.00'))
        self.assertEqual(res['predictions'][1]['forecast_value'], Decimal('8000.00'))
        self.assertEqual(res['predictions'][2]['forecast_value'], Decimal('9000.00'))

    def test_forecast_missing_month_zero_filling(self):
        """Missing calendar months inside the timeline are continuous and filled with 0.00."""
        # Sales in Jan 2026 and June 2026 (gap in Feb, Mar, Apr, May)
        Sale.objects.create(customer=self.customer_1, sales_staff=self.staff_profile_1, sale_date=date(2026, 1, 10), status=Sale.Status.CONFIRMED, stock_deducted=True, net_amount=Decimal('1000.00'))
        Sale.objects.create(customer=self.customer_1, sales_staff=self.staff_profile_1, sale_date=date(2026, 6, 10), status=Sale.Status.CONFIRMED, stock_deducted=True, net_amount=Decimal('2000.00'))

        res = generate_sales_forecast()
        # Timeline Jan to Jun is 6 months (Jan, Feb, Mar, Apr, May, Jun)
        self.assertEqual(res['num_months_used'], 6)
        self.assertEqual(res['status'], 'ACTIVE')
        # Check that intermediate months were zero filled
        historical_revs = [p['revenue'] for p in res['historical_points']]
        self.assertEqual(historical_revs, [1000.0, 0.0, 0.0, 0.0, 0.0, 2000.0])

    def test_forecast_negative_prediction_clamped_to_zero(self):
        """Steep downward trend must clamp negative projections to 0.00."""
        # Revenue drops rapidly: 10000, 8000, 6000, 4000, 2000, 100
        revs = [10000, 8000, 6000, 4000, 2000, 100]
        for idx, r in enumerate(revs, start=1):
            Sale.objects.create(
                customer=self.customer_1,
                sales_staff=self.staff_profile_1,
                sale_date=date(2026, idx, 10),
                status=Sale.Status.CONFIRMED,
                stock_deducted=True,
                net_amount=Decimal(r)
            )

        res = generate_sales_forecast()
        for pred in res['predictions']:
            self.assertGreaterEqual(pred['forecast_value'], Decimal('0.00'))

    def test_forecast_three_month_labels_advance_across_year_boundary(self):
        """Future predictions correctly label months advancing across Dec -> Jan."""
        for m in range(7, 13):  # July to December
            Sale.objects.create(
                customer=self.customer_1,
                sales_staff=self.staff_profile_1,
                sale_date=date(2026, m, 10),
                status=Sale.Status.CONFIRMED,
                stock_deducted=True,
                net_amount=Decimal('1000.00')
            )
        res = generate_sales_forecast()
        # Predictions must be Jan 2027, Feb 2027, Mar 2027
        self.assertEqual(res['predictions'][0]['label'], 'Jan 2027')
        self.assertEqual(res['predictions'][1]['label'], 'Feb 2027')
        self.assertEqual(res['predictions'][2]['label'], 'Mar 2027')


class RoleBasedAccessControlAndViewsTests(BaseReportsTestSetup):
    def setUp(self):
        self.client = Client()

    def test_anonymous_redirect_to_login(self):
        """Anonymous requests must redirect to accounts:login."""
        response = self.client.get(reverse('reports:index'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    def test_customer_denied_access_403(self):
        """Customer role must receive 403 Forbidden on all report and analytics endpoints."""
        self.client.force_login(self.cust_user_1)
        urls_to_test = [
            reverse('reports:index'),
            reverse('reports:admin_analytics'),
            reverse('reports:staff_analytics'),
            reverse('reports:sales_forecast'),
            reverse('reports:customer_report'),
            reverse('reports:lead_report'),
            reverse('reports:sales_report'),
            reverse('reports:invoice_report'),
            reverse('reports:payment_report'),
            reverse('reports:catalog_report'),
            reverse('reports:staff_performance_report'),
            reverse('reports:activity_report'),
        ]
        for url in urls_to_test:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 403, f"Customer should receive 403 on {url}")

    def test_sales_staff_access_and_admin_analytics_denial(self):
        """Sales staff can access personal reports and forecast, but is denied (403) from admin analytics."""
        self.client.force_login(self.staff_user_1)

        # Permitted endpoints
        self.assertEqual(self.client.get(reverse('reports:index')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:staff_analytics')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:sales_forecast')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:customer_report')).status_code, 200)

        # Admin analytics must be 403 for sales staff
        self.assertEqual(self.client.get(reverse('reports:admin_analytics')).status_code, 403)

    def test_sales_staff_cannot_view_other_staff_analytics_via_url(self):
        """Direct URL manipulation with ?staff_id=other does not expose other staff's analytics to sales staff."""
        self.client.force_login(self.staff_user_1)
        # Attempt to access staff 2's profile
        res = self.client.get(f"{reverse('reports:staff_analytics')}?staff_id={self.staff_profile_2.id}")
        self.assertEqual(res.status_code, 200)
        # The view must strictly use request.user.staff_profile, ignoring staff_id
        self.assertEqual(res.context['target_staff'].id, self.staff_profile_1.id)

    def test_staff_performance_report_shows_only_own_row_to_sales_staff(self):
        """Sales staff on the performance report must see only their own row."""
        self.client.force_login(self.staff_user_1)
        res = self.client.get(reverse('reports:staff_performance_report'))
        self.assertEqual(res.status_code, 200)
        report_rows = res.context['report_rows']
        self.assertEqual(len(report_rows), 1)
        self.assertEqual(report_rows[0]['staff_code'], self.staff_profile_1.staff_code)

    def test_admin_can_access_all_reports_and_analytics(self):
        """Admin user can access global analytics, personal analytics, forecast, and all 8 reports."""
        self.client.force_login(self.admin_user)
        self.assertEqual(self.client.get(reverse('reports:index')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:admin_analytics')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:staff_analytics')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:sales_forecast')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:customer_report')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:lead_report')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:sales_report')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:invoice_report')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:payment_report')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:catalog_report')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:staff_performance_report')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reports:activity_report')).status_code, 200)

    def test_all_reports_export_formats_return_200(self):
        """All 8 reports export successfully to CSV, XLSX, and PDF."""
        self.client.force_login(self.admin_user)
        report_endpoints = [
            reverse('reports:customer_report'),
            reverse('reports:lead_report'),
            reverse('reports:sales_report'),
            reverse('reports:invoice_report'),
            reverse('reports:payment_report'),
            reverse('reports:catalog_report'),
            reverse('reports:staff_performance_report'),
            reverse('reports:activity_report'),
        ]

        for ep in report_endpoints:
            # CSV
            res_csv = self.client.get(f"{ep}?export=csv")
            self.assertEqual(res_csv.status_code, 200, f"Failed CSV on {ep}")
            self.assertIn('text/csv', res_csv['Content-Type'])

            # XLSX
            res_xlsx = self.client.get(f"{ep}?export=xlsx")
            self.assertEqual(res_xlsx.status_code, 200, f"Failed XLSX on {ep}")
            self.assertEqual(res_xlsx['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

            # PDF
            res_pdf = self.client.get(f"{ep}?export=pdf")
            self.assertEqual(res_pdf.status_code, 200, f"Failed PDF on {ep}")
            self.assertEqual(res_pdf['Content-Type'], 'application/pdf')
            self.assertTrue(res_pdf.content.startswith(b"%PDF-"))

    def test_chart_json_script_escaping_prevents_xss(self):
        """Chart data passed through json_script safely escapes HTML/script injection."""
        self.client.force_login(self.admin_user)
        # Create a lead with XSS string in name
        Lead.objects.create(
            lead_code='LED-XSS-1',
            full_name='<script>alert("XSS Attack")</script>',
            email='xss@example.com',
            phone='1234567890',
            status=Lead.Status.NEW
        )
        res = self.client.get(reverse('reports:admin_analytics'))
        self.assertEqual(res.status_code, 200)
        # Verify content contains json-script element and does NOT contain raw unescaped script breakout
        self.assertIn(b'id="lead-pipeline-data"', res.content)
        self.assertNotIn(b'<script>alert("XSS Attack")</script>', res.content)

    def test_forecast_threshold_exact_at_5_and_6_months(self):
        """At 5 months, method is MOVING_AVERAGE_FALLBACK. At 6 months, it switches to LINEAR_REGRESSION."""
        # 5 contiguous months
        for m in range(1, 6):
            Sale.objects.create(
                customer=self.customer_1,
                sales_staff=self.staff_profile_1,
                sale_date=date(2026, m, 10),
                status=Sale.Status.CONFIRMED,
                stock_deducted=True,
                net_amount=Decimal('1000.00')
            )
        f_5 = generate_sales_forecast()
        self.assertEqual(f_5['num_months_used'], 5)
        self.assertEqual(f_5['status'], 'FALLBACK')
        self.assertEqual(f_5['method'], 'MOVING_AVERAGE_FALLBACK')
        self.assertFalse(f_5['is_ml'])

        # Add 6th month -> switches to ML
        Sale.objects.create(
            customer=self.customer_1,
            sales_staff=self.staff_profile_1,
            sale_date=date(2026, 6, 10),
            status=Sale.Status.CONFIRMED,
            stock_deducted=True,
            net_amount=Decimal('1000.00')
        )
        f_6 = generate_sales_forecast()
        self.assertEqual(f_6['num_months_used'], 6)
        self.assertEqual(f_6['status'], 'ACTIVE')
        self.assertEqual(f_6['method'], 'LINEAR_REGRESSION')
        self.assertTrue(f_6['is_ml'])

    def test_timezone_day_boundary_includes_full_day(self):
        """Events created near midnight are correctly included in day filters in Asia/Kolkata."""
        tz = get_crm_timezone()
        test_date = date(2026, 5, 20)

        # Payment at 23:45 on test_date
        late_dt = timezone.make_aware(datetime(2026, 5, 20, 23, 45, 0), tz)

        s = Sale.objects.create(
            customer=self.customer_1,
            sales_staff=self.staff_profile_1,
            sale_date=test_date,
            status=Sale.Status.CONFIRMED,
            stock_deducted=True,
            net_amount=Decimal('1000.00')
        )
        inv = Invoice.objects.create(
            sale=s,
            customer=self.customer_1,
            invoice_date=test_date,
            due_date=test_date,
            subtotal=Decimal('1000.00'),
            taxable_amount=Decimal('1000.00'),
            total_amount=Decimal('1000.00'),
            paid_amount=Decimal('1000.00'),
            balance_amount=Decimal('0.00')
        )
        Payment.objects.create(
            invoice=inv,
            customer=self.customer_1,
            idempotency_key='PAY-BOUNDARY-TEST',
            payment_date=late_dt,
            amount=Decimal('1000.00'),
            payment_status=Payment.Status.COMPLETED,
            is_reversed=False,
            payment_method=Payment.Method.CASH
        )

        # Query eligible payments on test_date
        payments_today = get_eligible_payments(start_date=test_date, end_date=test_date)
        self.assertEqual(payments_today.count(), 1)
        self.assertEqual(payments_today.first().amount, Decimal('1000.00'))

