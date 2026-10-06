import json
from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from accounts.models import CustomUser, CustomerProfile, SalesStaffProfile
from catalog.models import Category, ProductService, StockAdjustment
from leads.models import Lead
from leads.services.conversion import LeadConversionService
from sales.models import Sale, SaleItem, Invoice, Payment
from sales.services import create_sale_order, confirm_sale_order, record_payment, reverse_payment
from reminders.models import Reminder
from interactions.models import Interaction
from core.views import error_400, error_403, error_404, error_500

User = get_user_model()


class Phase8SecurityAndJSONScriptTests(TestCase):
    """
    Verify mitigation of unsafe |safe JSON rendering in sales order form.
    Tests that Django's json_script safely escapes HTML characters, quotes,
    and </script> tags, preventing script-tag breakout XSS.
    """

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_json_test',
            email='staff_json@example.com',
            password='Password123!',
            role=CustomUser.Role.SALES_STAFF,
            full_name='Staff JSON Tester'
        )
        self.staff_profile = SalesStaffProfile.objects.create(
            user=self.staff_user,
            staff_code='STF-JSON-01',
            assigned_target=Decimal('50000.00')
        )
        self.category = Category.objects.create(category_name='Hardware Security', status=Category.Status.ACTIVE)
        # Create product with malicious-looking name containing </script> and quotes
        self.malicious_product = ProductService.objects.create(
            product_code='PROD-XSS-01',
            product_name='Router Pro </script><script>alert("XSS")</script> & "Quotes"',
            item_type=ProductService.ItemType.PRODUCT,
            category=self.category,
            unit_price=Decimal('1500.00'),
            stock_quantity=10,
            status=ProductService.Status.ACTIVE
        )
        self.client = Client()
        self.client.login(username='staff_json_test', password='Password123!')

    def test_json_script_safely_escapes_script_tags_in_sale_create(self):
        response = self.client.get(reverse('sales:sale_create'))
        self.assertEqual(response.status_code, 200)

        # 1. products_list must be in context as Python list
        self.assertIn('products_list', response.context)
        products_list = response.context['products_list']
        self.assertTrue(isinstance(products_list, list))
        prod_data = next((p for p in products_list if p['id'] == self.malicious_product.id), None)
        self.assertIsNotNone(prod_data)
        self.assertEqual(prod_data['product_name'], self.malicious_product.product_name)

        # 2. json_script must render a script tag with id="products-catalog-data"
        content = response.content.decode('utf-8')
        self.assertIn('<script id="products-catalog-data" type="application/json">', content)

        # 3. CRITICAL: Raw </script> must NEVER appear unescaped inside the JSON script element
        # Django json_script encodes '<' as '\u003c' and '>' as '\u003e'
        self.assertNotIn('</script><script>alert("XSS")</script>', content)
        self.assertTrue(
            '\\u003c/script\\u003e' in content.lower() or '\\u003C/script\\u003E' in content,
            "Escaped script tag should be present in json_script output"
        )

        # 4. JSON must parse cleanly back to original strings
        script_payload = content.split('<script id="products-catalog-data" type="application/json">')[1].split('</script>')[0]
        parsed = json.loads(script_payload)
        parsed_prod = next(p for p in parsed if p['id'] == self.malicious_product.id)
        self.assertEqual(parsed_prod['product_name'], self.malicious_product.product_name)


class Phase8CustomerDashboardFinancialTests(TestCase):
    """
    Verify customer dashboard financial calculations:
    - Cancelled invoices must be excluded from outstanding balance.
    - Reversed payments must be excluded from total spent.
    - Stored validated balances are used without conflicting recalculations.
    - All totals are Decimal instances.
    """

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_dash_fin',
            email='staff_dash@example.com',
            password='Password123!',
            role=CustomUser.Role.SALES_STAFF,
            full_name='Staff Financial Rep'
        )
        self.staff_profile = SalesStaffProfile.objects.create(
            user=self.staff_user,
            staff_code='STF-FIN-01'
        )
        self.cust_user = User.objects.create_user(
            username='cust_dash_fin',
            email='cust_dash@example.com',
            password='Password123!',
            role=CustomUser.Role.CUSTOMER,
            full_name='Customer Financial Test'
        )
        self.customer_profile = CustomerProfile.objects.create(
            user=self.cust_user,
            customer_code='CUST-FIN-01',
            assigned_staff=self.staff_profile
        )
        self.category = Category.objects.create(category_name='Financial Test Cat', status=Category.Status.ACTIVE)
        self.product = ProductService.objects.create(
            product_code='PROD-FIN-01',
            product_name='Standard Service Package',
            item_type=ProductService.ItemType.SERVICE,
            category=self.category,
            unit_price=Decimal('100.00'),
            stock_quantity=0,
            reorder_level=0,
            status=ProductService.Status.ACTIVE
        )

        # Order 1: Issued unpaid invoice (total: 100.00, paid: 0.00, balance: 100.00)
        self.sale1 = create_sale_order(
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            tax_rate=Decimal('0.00'),
            discount=Decimal('0.00'),
            items_data=[{'product': self.product, 'quantity': 1, 'unit_price': Decimal('100.00'), 'discount': Decimal('0.00')}]
        )
        confirm_sale_order(self.sale1, user=self.staff_user)
        self.inv1 = self.sale1.invoice

        # Order 2: Partially paid invoice (total: 200.00, paid: 50.00, balance: 150.00)
        self.sale2 = create_sale_order(
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            tax_rate=Decimal('0.00'),
            discount=Decimal('0.00'),
            items_data=[{'product': self.product, 'quantity': 2, 'unit_price': Decimal('100.00'), 'discount': Decimal('0.00')}]
        )
        confirm_sale_order(self.sale2, user=self.staff_user)
        self.inv2 = self.sale2.invoice
        record_payment(
            invoice=self.inv2,
            amount=Decimal('50.00'),
            payment_method=Payment.Method.UPI,
            transaction_reference='UPI-PAY-01',
            idempotency_key='IDEM-DASH-01',
            user=self.staff_user
        )

        # Order 3: Fully paid invoice (total: 300.00, paid: 300.00, balance: 0.00)
        self.sale3 = create_sale_order(
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            tax_rate=Decimal('0.00'),
            discount=Decimal('0.00'),
            items_data=[{'product': self.product, 'quantity': 3, 'unit_price': Decimal('100.00'), 'discount': Decimal('0.00')}]
        )
        confirm_sale_order(self.sale3, user=self.staff_user)
        self.inv3 = self.sale3.invoice
        record_payment(
            invoice=self.inv3,
            amount=Decimal('300.00'),
            payment_method=Payment.Method.BANK_TRANSFER,
            transaction_reference='NEFT-PAY-02',
            idempotency_key='IDEM-DASH-02',
            user=self.staff_user
        )

        # Order 4: Cancelled invoice (total: 400.00, paid: 0.00, balance: 400.00, status: CANCELLED)
        self.sale4 = create_sale_order(
            customer=self.customer_profile,
            sales_staff=self.staff_profile,
            tax_rate=Decimal('0.00'),
            discount=Decimal('0.00'),
            items_data=[{'product': self.product, 'quantity': 4, 'unit_price': Decimal('100.00'), 'discount': Decimal('0.00')}]
        )
        confirm_sale_order(self.sale4, user=self.staff_user)
        self.inv4 = self.sale4.invoice
        # Cancel sale4 (which cancels inv4)
        from sales.services import cancel_sale_order
        cancel_sale_order(self.sale4, cancellation_reason="Client requested cancellation before payment", user=self.staff_user)
        self.inv4.refresh_from_db()
        self.assertEqual(self.inv4.invoice_status, Invoice.InvoiceStatus.CANCELLED)

        self.client = Client()
        self.client.login(username='cust_dash_fin', password='Password123!')

    def test_customer_dashboard_excludes_cancelled_invoices(self):
        response = self.client.get(reverse('dashboard:customer_dashboard'))
        self.assertEqual(response.status_code, 200)

        # Expected:
        # total_spent = 50.00 (inv2) + 300.00 (inv3) = 350.00
        # total_balance = 100.00 (inv1) + 150.00 (inv2) = 250.00 (inv4 balance of 400 is EXCLUDED)
        total_spent = response.context['total_spent']
        total_balance = response.context['total_balance']

        self.assertTrue(isinstance(total_spent, Decimal))
        self.assertTrue(isinstance(total_balance, Decimal))
        self.assertEqual(total_spent, Decimal('350.00'))
        self.assertEqual(total_balance, Decimal('250.00'))

    def test_customer_dashboard_excludes_reversed_payments(self):
        # Reverse the 50.00 payment on inv2
        payment = self.inv2.payments.first()
        reverse_payment(payment, reversal_reason="Incorrect UPI account", user=self.staff_user)

        response = self.client.get(reverse('dashboard:customer_dashboard'))
        self.assertEqual(response.status_code, 200)

        # Now:
        # total_spent = 300.00 (only inv3)
        # total_balance = 100.00 (inv1) + 200.00 (inv2 balance restored) = 300.00
        self.assertEqual(response.context['total_spent'], Decimal('300.00'))
        self.assertEqual(response.context['total_balance'], Decimal('300.00'))


class Phase8NavigationAndSidebarTests(TestCase):
    """
    Verify sidebar navigation integrity:
    - Zero '#' placeholders exist in implemented navigation links.
    - AI Lead Suggestions links to /leads/?priority=HIGH for sales staff.
    - Clicking the link correctly applies the priority filter in LeadListView.
    """

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_nav_test',
            email='staff_nav@example.com',
            password='Password123!',
            role=CustomUser.Role.SALES_STAFF,
            full_name='Staff Navigation Tester'
        )
        self.staff_profile = SalesStaffProfile.objects.create(
            user=self.staff_user,
            staff_code='STF-NAV-01'
        )
        # Create leads with different priorities
        self.high_lead = Lead.objects.create(
            full_name='High Priority Lead',
            email='high@example.com',
            phone='9876543210',
            assigned_staff=self.staff_profile,
            ai_priority=Lead.Priority.HIGH
        )
        self.low_lead = Lead.objects.create(
            full_name='Low Priority Lead',
            email='low@example.com',
            phone='9876543211',
            assigned_staff=self.staff_profile,
            ai_priority=Lead.Priority.LOW
        )
        self.client = Client()
        self.client.login(username='staff_nav_test', password='Password123!')

    def test_sidebar_ai_lead_suggestions_link_valid_and_functional(self):
        response = self.client.get(reverse('dashboard:staff_dashboard'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Assert no placeholder href="#" exists in sidebar
        self.assertNotIn('<a href="#" class="sidebar-link">', content)

        # Assert correct high priority link is rendered
        expected_url = reverse('leads:lead_list') + '?priority=HIGH'
        self.assertIn(expected_url, content)
        self.assertIn('AI Lead Suggestions', content)

        # Follow the link and verify that only high priority leads are shown
        list_response = self.client.get(expected_url)
        self.assertEqual(list_response.status_code, 200)
        page_leads = list_response.context['leads']
        lead_ids = [l.id for l in page_leads]
        self.assertIn(self.high_lead.id, lead_ids)
        self.assertNotIn(self.low_lead.id, lead_ids)


class Phase8ErrorHandlingTests(TestCase):
    """
    Test custom branded error handlers: 400, 403, 404, 500.
    Ensures safe error pages render without disclosing internal debug traces.
    """

    def setUp(self):
        self.factory = RequestFactory()

    def test_custom_400_error_view(self):
        request = self.factory.get('/bad-request/')
        response = error_400(request)
        self.assertEqual(response.status_code, 400)
        content = response.content.decode('utf-8')
        self.assertIn('400', content)
        self.assertIn('Bad Request', content)
        self.assertIn('Return to Dashboard', content)
        self.assertNotIn('Traceback', content)

    def test_custom_403_error_view(self):
        request = self.factory.get('/forbidden/')
        response = error_403(request)
        self.assertEqual(response.status_code, 403)
        content = response.content.decode('utf-8')
        self.assertIn('403', content)
        self.assertIn('Access Forbidden', content)

    def test_custom_404_error_view(self):
        request = self.factory.get('/not-found/')
        response = error_404(request)
        self.assertEqual(response.status_code, 404)
        content = response.content.decode('utf-8')
        self.assertIn('404', content)
        self.assertIn('Page Not Found', content)

    def test_custom_500_error_view(self):
        request = self.factory.get('/server-error/')
        response = error_500(request)
        self.assertEqual(response.status_code, 500)
        content = response.content.decode('utf-8')
        self.assertIn('500', content)
        self.assertIn('Server Error', content)


class Phase8RoleAccessAndIDORTests(TestCase):
    """
    Verify cross-role isolation, anonymous redirection, and IDOR defenses.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='p8_admin', email='p8_admin@example.com', password='Password123!',
            role=CustomUser.Role.ADMIN
        )
        self.staff1 = User.objects.create_user(
            username='p8_staff1', email='p8_staff1@example.com', password='Password123!',
            role=CustomUser.Role.SALES_STAFF
        )
        self.staff1_profile = SalesStaffProfile.objects.create(user=self.staff1, staff_code='STF-P8-01')

        self.staff2 = User.objects.create_user(
            username='p8_staff2', email='p8_staff2@example.com', password='Password123!',
            role=CustomUser.Role.SALES_STAFF
        )
        self.staff2_profile = SalesStaffProfile.objects.create(user=self.staff2, staff_code='STF-P8-02')

        self.cust1_user = User.objects.create_user(
            username='p8_cust1', email='p8_cust1@example.com', password='Password123!',
            role=CustomUser.Role.CUSTOMER
        )
        self.cust1_profile = CustomerProfile.objects.create(
            user=self.cust1_user, customer_code='CUST-P8-01', assigned_staff=self.staff1_profile
        )

        self.cust2_user = User.objects.create_user(
            username='p8_cust2', email='p8_cust2@example.com', password='Password123!',
            role=CustomUser.Role.CUSTOMER
        )
        self.cust2_profile = CustomerProfile.objects.create(
            user=self.cust2_user, customer_code='CUST-P8-02', assigned_staff=self.staff2_profile
        )

        self.lead2 = Lead.objects.create(
            full_name='Lead for Staff 2', email='lead2@example.com', phone='1234567890',
            assigned_staff=self.staff2_profile
        )

    def test_anonymous_redirected_to_login(self):
        client = Client()
        protected_urls = [
            reverse('dashboard:index'),
            reverse('accounts:customer_list'),
            reverse('accounts:staff_list'),
            reverse('leads:lead_list'),
            reverse('sales:sale_list'),
            reverse('catalog:product_list'),
            reverse('reports:index'),
            reverse('ai_assistant:index'),
        ]
        for url in protected_urls:
            resp = client.get(url)
            self.assertEqual(resp.status_code, 302, f"Failed for {url}")
            self.assertIn('/accounts/login/', resp.url)

    def test_customer_denied_admin_and_staff_views(self):
        client = Client()
        client.login(username='p8_cust1', password='Password123!')
        forbidden_urls = [
            reverse('dashboard:admin_dashboard'),
            reverse('dashboard:staff_dashboard'),
            reverse('accounts:customer_list'),
            reverse('accounts:staff_list'),
            reverse('leads:lead_list'),
            reverse('sales:sale_list'),
            reverse('catalog:product_list'),
            reverse('reports:admin_analytics'),
            reverse('activity_logs:log_list'),
        ]
        for url in forbidden_urls:
            resp = client.get(url)
            self.assertEqual(resp.status_code, 403, f"Expected 403 for customer on {url}")

    def test_sales_staff_denied_unassigned_customer_and_lead(self):
        client = Client()
        client.login(username='p8_staff1', password='Password123!')

        # Staff 1 attempts to access Customer 2 (assigned to Staff 2)
        resp_cust = client.get(reverse('accounts:customer_detail', kwargs={'pk': self.cust2_profile.pk}))
        self.assertEqual(resp_cust.status_code, 403)

        resp_cust_edit = client.get(reverse('accounts:customer_edit', kwargs={'pk': self.cust2_profile.pk}))
        self.assertEqual(resp_cust_edit.status_code, 403)

        # Staff 1 attempts to access Lead 2 (assigned to Staff 2)
        resp_lead = client.get(reverse('leads:lead_detail', kwargs={'pk': self.lead2.pk}))
        self.assertEqual(resp_lead.status_code, 403)

        resp_lead_edit = client.get(reverse('leads:lead_edit', kwargs={'pk': self.lead2.pk}))
        self.assertEqual(resp_lead_edit.status_code, 403)


class Phase8EndToEndWorkflowTests(TestCase):
    """
    End-to-end multi-role business workflow test:
    Admin creates lead -> Staff scores & converts lead -> Sales order created & confirmed ->
    Stock deducted -> Invoice generated -> Payment recorded & reversed -> Integrity verified.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='e2e_admin', email='e2e_admin@example.com', password='Password123!',
            role=CustomUser.Role.ADMIN
        )
        self.staff = User.objects.create_user(
            username='e2e_staff', email='e2e_staff@example.com', password='Password123!',
            role=CustomUser.Role.SALES_STAFF
        )
        self.staff_profile = SalesStaffProfile.objects.create(user=self.staff, staff_code='STF-E2E-01')

        self.category = Category.objects.create(category_name='Laptops E2E', status=Category.Status.ACTIVE)
        self.product = ProductService.objects.create(
            product_code='LAP-E2E-01',
            product_name='Workstation Laptop',
            item_type=ProductService.ItemType.PRODUCT,
            category=self.category,
            unit_price=Decimal('50000.00'),
            stock_quantity=5,
            reorder_level=2,
            status=ProductService.Status.ACTIVE
        )

    def test_complete_crm_sales_lifecycle(self):
        # 1. Lead creation & qualification
        lead = Lead.objects.create(
            full_name='Apex Corp Lead',
            email='procurement@apexcorp.com',
            phone='9988776655',
            company='Apex Technologies',
            status=Lead.Status.QUALIFIED,
            estimated_value=Decimal('50000.00'),
            assigned_staff=self.staff_profile
        )
        self.assertTrue(lead.is_active)
        self.assertFalse(lead.is_converted)

        # 2. Conversion to CustomerProfile
        customer, setup_url = LeadConversionService.convert_to_customer(
            lead=lead, acting_user=self.staff
        )
        self.assertIsNotNone(customer)
        self.assertEqual(lead.status, Lead.Status.WON)
        self.assertEqual(customer.assigned_staff, self.staff_profile)
        self.assertTrue(customer.user.is_customer)

        # 3. Create Sales Order
        sale = create_sale_order(
            customer=customer,
            sales_staff=self.staff_profile,
            tax_rate=Decimal('18.00'),
            discount=Decimal('1000.00'),
            items_data=[{
                'product': self.product,
                'quantity': 2,
                'unit_price': Decimal('50000.00'),
                'discount': Decimal('0.00')
            }]
        )
        # Financial assertions: subtotal = 100000, discount = 1000, taxable = 99000, tax = 17820, net = 116820
        self.assertEqual(sale.status, Sale.Status.DRAFT)
        self.assertEqual(sale.subtotal, Decimal('100000.00'))
        self.assertEqual(sale.taxable_amount, Decimal('99000.00'))
        self.assertEqual(sale.tax_amount, Decimal('17820.00'))
        self.assertEqual(sale.net_amount, Decimal('116820.00'))
        self.assertFalse(sale.stock_deducted)

        # 4. Confirm Sale Order
        initial_stock = self.product.stock_quantity  # 5
        confirm_sale_order(sale=sale, user=self.staff)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.CONFIRMED)
        self.assertTrue(sale.stock_deducted)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, initial_stock - 2)  # 3

        # Invoice automatically created with snapshot
        self.assertTrue(sale.has_invoice)
        invoice = sale.invoice
        self.assertEqual(invoice.total_amount, Decimal('116820.00'))
        self.assertEqual(invoice.paid_amount, Decimal('0.00'))
        self.assertEqual(invoice.balance_amount, Decimal('116820.00'))
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.UNPAID)

        # 5. Record Payment
        payment, created = record_payment(
            invoice=invoice,
            amount=Decimal('50000.00'),
            payment_method=Payment.Method.BANK_TRANSFER,
            transaction_reference='E2E-UTR-999',
            idempotency_key='IDEM-E2E-KEY-01',
            user=self.staff
        )
        self.assertTrue(created)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal('50000.00'))
        self.assertEqual(invoice.balance_amount, Decimal('66820.00'))
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.PARTIALLY_PAID)

        # 6. Test Payment Idempotency (Duplicate submission with same idempotency key)
        dup_payment, dup_created = record_payment(
            invoice=invoice,
            amount=Decimal('50000.00'),
            payment_method=Payment.Method.BANK_TRANSFER,
            transaction_reference='E2E-UTR-999',
            idempotency_key='IDEM-E2E-KEY-01',
            user=self.staff
        )
        self.assertFalse(dup_created)
        self.assertEqual(dup_payment.id, payment.id)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal('50000.00'))  # Not altered twice!

        # 7. Reverse Payment
        reverse_payment(payment, reversal_reason="Client bank dispute", user=self.admin)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal('0.00'))
        self.assertEqual(invoice.balance_amount, Decimal('116820.00'))
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.UNPAID)

        # 8. Customer Dashboard verification
        client = Client()
        client.force_login(customer.user)
        dash_resp = client.get(reverse('dashboard:customer_dashboard'))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertEqual(dash_resp.context['total_spent'], Decimal('0.00'))
        self.assertEqual(dash_resp.context['total_balance'], Decimal('116820.00'))


class Phase8AllNamedURLReversalTests(TestCase):
    """
    Verify that all key named URLs across the 12 CRM applications
    can be reversed without error.
    """

    def test_core_named_routes_reverse(self):
        routes_without_params = [
            'home',
            'accounts:login',
            'accounts:logout',
            'accounts:register',
            'accounts:profile',
            'accounts:customer_list',
            'accounts:customer_create',
            'accounts:staff_list',
            'accounts:staff_create',
            'dashboard:index',
            'dashboard:admin_dashboard',
            'dashboard:staff_dashboard',
            'dashboard:customer_dashboard',
            'leads:lead_list',
            'leads:lead_create',
            'catalog:product_list',
            'catalog:product_create',
            'catalog:category_list',
            'catalog:category_create',
            'catalog:customer_catalog',
            'sales:sale_list',
            'sales:sale_create',
            'sales:invoice_list',
            'sales:payment_list',
            'sales:customer_sale_list',
            'sales:customer_invoice_list',
            'sales:customer_payment_list',
            'interactions:interaction_list',
            'interactions:interaction_create',
            'interactions:customer_interaction_list',
            'reminders:reminder_list',
            'reminders:reminder_create',
            'reminders:customer_reminder_list',
            'notifications:notification_list',
            'activity_logs:log_list',
            'reports:index',
            'reports:admin_analytics',
            'reports:staff_analytics',
            'reports:sales_forecast',
            'ai_assistant:index',
        ]
        for route in routes_without_params:
            url = reverse(route)
            self.assertTrue(url.startswith('/'), f"Failed to reverse route: {route}")


class Phase8LandingPageComprehensiveTests(TestCase):
    """
    Comprehensive verification of the public Home / Landing Page:
    - Anonymous and authenticated access returns 200 OK without redirection
    - Context-dependent navigation (Anonymous CTAs vs Authenticated Dashboard/Role)
    - Role-based dashboard destination resolution
    - CSRF-protected POST logout form verification (no plain GET links)
    - Public catalog preview security: max 6 items, active only, zero stock exposure
    - XSS script tag escaping
    - Public protection against management/sales actions
    - Mobile navigation accessibility markup
    """

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username='landing_admin', email='l_admin@example.com', password='Password123!',
            role=CustomUser.Role.ADMIN, full_name='Admin Commander'
        )
        self.staff = User.objects.create_user(
            username='landing_staff', email='l_staff@example.com', password='Password123!',
            role=CustomUser.Role.SALES_STAFF, full_name='Staff Representative'
        )
        self.staff_profile = SalesStaffProfile.objects.create(
            user=self.staff, staff_code='STF-LND-01'
        )
        self.cust_user = User.objects.create_user(
            username='landing_cust', email='l_cust@example.com', password='Password123!',
            role=CustomUser.Role.CUSTOMER, full_name='Valued Customer'
        )
        self.customer_profile = CustomerProfile.objects.create(
            user=self.cust_user, customer_code='CUST-LND-01', assigned_staff=self.staff_profile
        )

        # Categories
        self.active_category = Category.objects.create(
            category_name='Cloud Software', status=Category.Status.ACTIVE
        )
        self.inactive_category = Category.objects.create(
            category_name='Deprecated Systems', status=Category.Status.INACTIVE
        )

        # Active Products
        self.active_products = []
        for i in range(8):
            p = ProductService.objects.create(
                product_code=f'PROD-LND-{i+1:02d}',
                product_name=f'Software Suite {chr(65+i)}',
                item_type=ProductService.ItemType.PRODUCT,
                category=self.active_category,
                unit_price=Decimal(f'{(i+1)*1000}.00'),
                stock_quantity=7421 if i == 0 else 10,
                reorder_level=3311 if i == 0 else 5,
                description=f'Full enterprise software solution tier {chr(65+i)}.',
                status=ProductService.Status.ACTIVE
            )
            self.active_products.append(p)

        # Inactive Product
        self.inactive_product = ProductService.objects.create(
            product_code='PROD-LND-INACT',
            product_name='Decommissioned Tool',
            item_type=ProductService.ItemType.PRODUCT,
            category=self.active_category,
            unit_price=Decimal('500.00'),
            stock_quantity=5,
            status=ProductService.Status.INACTIVE
        )

        # Product in Inactive Category
        self.hidden_category_product = ProductService.objects.create(
            product_code='PROD-LND-CATINACT',
            product_name='Legacy Module',
            item_type=ProductService.ItemType.PRODUCT,
            category=self.inactive_category,
            unit_price=Decimal('750.00'),
            stock_quantity=20,
            status=ProductService.Status.ACTIVE
        )

    def test_anonymous_root_returns_200_with_public_navigation(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Public navigation elements
        self.assertIn(reverse('accounts:login'), content)
        self.assertIn(reverse('accounts:register'), content)
        self.assertIn('Log In', content)
        self.assertIn('Create Customer Account', content)

        # Core landing sections present
        self.assertIn('id="hero"', content)
        self.assertIn('id="features"', content)
        self.assertIn('id="products"', content)
        self.assertIn('id="preview"', content)
        self.assertIn('id="about"', content)
        self.assertIn('id="contact"', content)

        # Hero headline
        self.assertIn('Build Stronger Customer Relationships. Grow With Confidence.', content)

    def test_admin_access_root_returns_200_with_admin_dashboard_cta(self):
        self.client.login(username='landing_admin', password='Password123!')
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Authenticated destination points to admin dashboard
        expected_admin_url = reverse('dashboard:admin_dashboard')
        self.assertIn(expected_admin_url, content)
        self.assertIn('Go to Dashboard', content)
        self.assertIn('Administrator', content)

    def test_sales_staff_access_root_returns_200_with_staff_dashboard_cta(self):
        self.client.login(username='landing_staff', password='Password123!')
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Authenticated destination points to staff dashboard
        expected_staff_url = reverse('dashboard:staff_dashboard')
        self.assertIn(expected_staff_url, content)
        self.assertIn('Go to Dashboard', content)
        self.assertIn('Sales Staff', content)

    def test_customer_access_root_returns_200_with_customer_dashboard_cta(self):
        self.client.login(username='landing_cust', password='Password123!')
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Authenticated destination points to customer dashboard
        expected_customer_url = reverse('dashboard:customer_dashboard')
        self.assertIn(expected_customer_url, content)
        self.assertIn('Go to Dashboard', content)
        self.assertIn('Customer', content)

    def test_logout_is_post_only_and_csrf_protected(self):
        self.client.login(username='landing_cust', password='Password123!')
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # 1. Form uses POST method and targets accounts:logout
        logout_url = reverse('accounts:logout')
        self.assertIn(f'action="{logout_url}"', content)
        self.assertIn('method="post"', content)

        # 2. Contains CSRF middleware token input
        self.assertIn('csrfmiddlewaretoken', content)

        # 3. CRITICAL: Zero plain GET <a href=".../logout/"> links exist
        self.assertNotIn(f'<a href="{logout_url}"', content)
        self.assertNotIn(f'<a class="dropdown-item" href="{logout_url}"', content)

    def test_catalog_preview_capped_at_six_and_excludes_inactive(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        preview_items = response.context['preview_items']

        # Max 6 items
        self.assertEqual(len(preview_items), 6)

        # Inactive item excluded
        item_ids = [p.id for p in preview_items]
        self.assertNotIn(self.inactive_product.id, item_ids)

        # Item in inactive category excluded
        self.assertNotIn(self.hidden_category_product.id, item_ids)

    def test_catalog_preview_does_not_expose_stock_quantities_or_history(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Check that unique sensitive stock numbers never appear in public HTML
        self.assertNotIn('7421', content)
        self.assertNotIn('3311', content)

        # Check safe availability label is rendered
        self.assertIn('Available', content)

    def test_catalog_preview_empty_state_renders_clean_message(self):
        # Deactivate all active products
        ProductService.objects.all().update(status=ProductService.Status.INACTIVE)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('New products and services will appear here when available.', content)

    def test_missing_product_image_renders_fallback_placeholder(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Styled placeholder icon rendered when product has no image
        self.assertIn('landing-product-placeholder', content)
        self.assertIn('bi-box-seam', content)

    def test_html_script_content_is_escaped_in_catalog_preview(self):
        # Create item with script tag
        xss_product = ProductService.objects.create(
            product_code='PROD-XSS-PREV',
            product_name='Security <script>alert("XSS")</script>',
            item_type=ProductService.ItemType.PRODUCT,
            category=self.active_category,
            unit_price=Decimal('999.00'),
            stock_quantity=10,
            description='Safe description <script>bad()</script>',
            status=ProductService.Status.ACTIVE
        )
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Raw script tags must NEVER appear unescaped in rendered HTML
        self.assertNotIn('<script>alert("XSS")</script>', content)
        self.assertNotIn('<script>bad()</script>', content)

        # Escaped text must appear
        self.assertIn('&lt;script&gt;alert(&quot;XSS&quot;)&lt;/script&gt;', content)

    def test_public_visitors_cannot_perform_catalog_or_sales_actions(self):
        # Catalog create blocked for anonymous
        resp1 = self.client.post(reverse('catalog:product_create'), {'product_name': 'Hacker Item'})
        self.assertEqual(resp1.status_code, 302)
        self.assertIn('/accounts/login/', resp1.url)

        # Sale create blocked for anonymous
        resp2 = self.client.post(reverse('sales:sale_create'), {})
        self.assertEqual(resp2.status_code, 302)
        self.assertIn('/accounts/login/', resp2.url)

    def test_existing_post_login_role_redirects_remain_correct(self):
        from accounts.views import CustomLoginView
        from django.test import RequestFactory

        rf = RequestFactory()

        # Admin login success redirect
        req_admin = rf.get('/accounts/login/')
        req_admin.user = self.admin
        view_admin = CustomLoginView()
        view_admin.request = req_admin
        self.assertEqual(str(view_admin.get_success_url()), reverse('dashboard:admin_dashboard'))

        # Staff login success redirect
        req_staff = rf.get('/accounts/login/')
        req_staff.user = self.staff
        view_staff = CustomLoginView()
        view_staff.request = req_staff
        self.assertEqual(str(view_staff.get_success_url()), reverse('dashboard:staff_dashboard'))

        # Customer login success redirect
        req_cust = rf.get('/accounts/login/')
        req_cust.user = self.cust_user
        view_cust = CustomLoginView()
        view_cust.request = req_cust
        self.assertEqual(str(view_cust.get_success_url()), reverse('dashboard:customer_dashboard'))

    def test_mobile_navigation_markup_and_accessibility_labels(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # ARIA and keyboard accessibility attributes
        self.assertIn('aria-label="Toggle navigation"', content)
        self.assertIn('aria-label="Main Navigation"', content)
        self.assertIn('data-bs-toggle="collapse"', content)
        self.assertIn('data-bs-target="#landingNavbarNav"', content)
        self.assertIn('aria-expanded="false"', content)

    def test_no_prohibited_academic_or_stack_wording_on_landing_page(self):
        """
        Verify that no academic, student, final-year, demo, or raw implementation
        stack terms appear on the public landing page.
        """
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content_lower = response.content.decode('utf-8').lower()

        prohibited_phrases = [
            'college project',
            'academic project',
            'student project',
            'final-year project',
            'final year project',
            'academic software development lab',
            'campus demonstration',
            'final-year b.voc',
            'b.voc software development',
            'final year academic demonstration',
            'academic demonstration',
            'three role tiers',
            'sqlite 3',
            'scikit-learn',
            'demo.internal',
            '1800-crm-demo',
        ]
        for phrase in prohibited_phrases:
            self.assertNotIn(phrase, content_lower, f"Prohibited phrase '{phrase}' found on public landing page.")

    def test_commercial_branding_and_features_present_on_landing_page(self):
        """
        Verify production-grade commercial CRM copy, genuine feature modules,
        workflow stages, business insights, and responsible security claims.
        """
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Hero content
        self.assertIn('Customer Relationships, Simplified', content)
        self.assertIn('Build Stronger Customer Relationships. Grow With Confidence.', content)
        self.assertIn('Your Business at a Glance', content)
        self.assertIn('Intelligent CRM Assistance', content)

        # Feature modules
        expected_modules = [
            'Customer Management',
            'Lead Management',
            'Products & Inventory',
            'Sales Orders',
            'Invoices & Payments',
            'Tasks & Follow-ups',
            'Customer Interactions',
            'Reports & Analytics',
            'AI CRM Assistant',
        ]
        for module in expected_modules:
            self.assertIn(module, content)

        # Four-step workflow
        for step in ['Capture', 'Engage', 'Convert', 'Retain']:
            self.assertIn(step, content)

        # Business Insights & Security sections
        self.assertIn('Make Better Decisions With Clear Business Insights', content)
        self.assertIn('Designed Around Responsible Access', content)
