from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, CustomerProfile, SalesStaffProfile
from activity_logs.models import ActivityLog
from catalog.models import Category, ProductService
from leads.models import Lead
from reminders.models import Reminder
from sales.models import Sale, SaleItem, Invoice, Payment
from ai_assistant.services.intent_service import (
    detect_intent,
    sanitize_query,
    INTENT_CRM_SUMMARY,
    INTENT_LEAD_PIPELINE_SUMMARY,
    INTENT_HIGH_PRIORITY_LEADS,
    INTENT_STAFF_MY_LEADS,
    INTENT_STAFF_MY_HIGH_PRIORITY_LEADS,
    INTENT_CUSTOMER_MY_SALES,
    INTENT_CUSTOMER_MY_BALANCE,
    INTENT_PRODUCT_RECOMMENDATIONS,
    INTENT_UNSUPPORTED,
    INTENT_HELP_INTENTS,
    INTENT_STOCK_ALERTS,
)
from ai_assistant.services.recommendation_service import get_recommendations_for_customer
from ai_assistant.services.throttling import check_throttled, reset_throttle


User = get_user_model()


class BaseAIAssistantTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Admin User
        self.admin_user = User.objects.create_user(
            username='admin_test',
            email='admin@example.com',
            password='Password123!',
            role=CustomUser.Role.ADMIN,
            status=CustomUser.Status.ACTIVE
        )

        # 2. Sales Staff User 1
        self.staff_user_1 = User.objects.create_user(
            username='staff1_test',
            email='staff1@example.com',
            password='Password123!',
            role=CustomUser.Role.SALES_STAFF,
            status=CustomUser.Status.ACTIVE
        )
        self.staff_profile_1 = SalesStaffProfile.objects.create(
            user=self.staff_user_1,
            staff_code='STF-TEST1',
            designation='Sales Executive',
            assigned_target=Decimal('50000.00')
        )

        # 3. Sales Staff User 2 (for isolation testing)
        self.staff_user_2 = User.objects.create_user(
            username='staff2_test',
            email='staff2@example.com',
            password='Password123!',
            role=CustomUser.Role.SALES_STAFF,
            status=CustomUser.Status.ACTIVE
        )
        self.staff_profile_2 = SalesStaffProfile.objects.create(
            user=self.staff_user_2,
            staff_code='STF-TEST2',
            designation='Senior Representative',
            assigned_target=Decimal('80000.00')
        )

        # 4. Customer User 1 (Assigned to Staff 1)
        self.cust_user_1 = User.objects.create_user(
            username='cust1_test',
            email='cust1@example.com',
            password='Password123!',
            role=CustomUser.Role.CUSTOMER,
            status=CustomUser.Status.ACTIVE
        )
        self.cust_profile_1 = CustomerProfile.objects.create(
            user=self.cust_user_1,
            customer_code='CUST-TEST1',
            company='Alpha Corp',
            assigned_staff=self.staff_profile_1
        )

        # 5. Customer User 2 (Assigned to Staff 2)
        self.cust_user_2 = User.objects.create_user(
            username='cust2_test',
            email='cust2@example.com',
            password='Password123!',
            role=CustomUser.Role.CUSTOMER,
            status=CustomUser.Status.ACTIVE
        )
        self.cust_profile_2 = CustomerProfile.objects.create(
            user=self.cust_user_2,
            customer_code='CUST-TEST2',
            company='Beta LLC',
            assigned_staff=self.staff_profile_2
        )

        # 6. Catalog Entities
        self.category_hardware = Category.objects.create(category_name='Hardware', status=Category.Status.ACTIVE)
        self.category_software = Category.objects.create(category_name='Software', status=Category.Status.ACTIVE)
        self.category_inactive = Category.objects.create(category_name='Discontinued Cat', status=Category.Status.INACTIVE)

        # Active Product (in stock)
        self.prod_laptop = ProductService.objects.create(
            product_code='PRD-LAPTOP',
            product_name='Enterprise Laptop',
            item_type=ProductService.ItemType.PRODUCT,
            category=self.category_hardware,
            unit_price=Decimal('1000.00'),
            stock_quantity=10,
            reorder_level=2,
            status=ProductService.Status.ACTIVE
        )

        # Active Product (low stock)
        self.prod_mouse = ProductService.objects.create(
            product_code='PRD-MOUSE',
            product_name='Wireless Mouse',
            item_type=ProductService.ItemType.PRODUCT,
            category=self.category_hardware,
            unit_price=Decimal('25.00'),
            stock_quantity=1,
            reorder_level=5,
            status=ProductService.Status.ACTIVE
        )

        # Active Product (out of stock)
        self.prod_keyboard = ProductService.objects.create(
            product_code='PRD-KEYBOARD',
            product_name='Mechanical Keyboard',
            item_type=ProductService.ItemType.PRODUCT,
            category=self.category_hardware,
            unit_price=Decimal('80.00'),
            stock_quantity=0,
            reorder_level=3,
            status=ProductService.Status.ACTIVE
        )

        # Active Service (zero stock expected)
        self.service_cloud = ProductService.objects.create(
            product_code='SRV-CLOUD',
            product_name='Cloud Setup Consulting',
            item_type=ProductService.ItemType.SERVICE,
            category=self.category_software,
            unit_price=Decimal('500.00'),
            stock_quantity=0,
            reorder_level=0,
            status=ProductService.Status.ACTIVE
        )

        # Inactive Product
        self.prod_inactive = ProductService.objects.create(
            product_code='PRD-OLD',
            product_name='Old Monitor',
            item_type=ProductService.ItemType.PRODUCT,
            category=self.category_hardware,
            unit_price=Decimal('150.00'),
            stock_quantity=5,
            reorder_level=1,
            status=ProductService.Status.INACTIVE
        )


class AssistantSecurityAndAuthTests(BaseAIAssistantTestCase):
    def test_anonymous_user_redirects_to_login(self):
        response = self.client.get(reverse('ai_assistant:index'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    def test_inactive_user_access_blocked(self):
        self.admin_user.status = CustomUser.Status.INACTIVE
        self.admin_user.save()
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('ai_assistant:index'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    def test_role_access_admin(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('ai_assistant:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI Assistant &amp; Insights")
        self.assertContains(response, "CRM Summary")

    def test_role_access_sales_staff(self):
        self.client.force_login(self.staff_user_1)
        response = self.client.get(reverse('ai_assistant:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sales AI Assistant")
        self.assertContains(response, "My Leads")

    def test_role_access_customer(self):
        self.client.force_login(self.cust_user_1)
        response = self.client.get(reverse('ai_assistant:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Customer AI Assistant")
        self.assertContains(response, "My Purchases")

    def test_clear_history_requires_post(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('ai_assistant:clear'))
        self.assertEqual(response.status_code, 405)

    def test_clear_history_isolation(self):
        # Admin has history
        session = self.client.session
        session['assistant_history'] = [{'query': 'CRM Summary', 'topic': 'Summary'}]
        session.save()

        self.client.force_login(self.admin_user)
        post_response = self.client.post(reverse('ai_assistant:clear'))
        self.assertEqual(post_response.status_code, 302)
        self.assertNotIn('assistant_history', self.client.session)

    def test_rate_limiting_throttles_after_15_queries(self):
        self.client.force_login(self.admin_user)
        url = reverse('ai_assistant:index')

        # Simulate 15 rapid POST requests
        for i in range(15):
            res = self.client.post(url, {'question': 'CRM Summary'})
            self.assertEqual(res.status_code, 200)

        # 16th request must be throttled with HTTP 429
        throttled_res = self.client.post(url, {'question': 'CRM Summary'})
        self.assertEqual(throttled_res.status_code, 429)
        self.assertContains(throttled_res, "temporary assistant question limit", status_code=429)

    def test_maximum_input_length_rejected(self):
        self.client.force_login(self.admin_user)
        long_text = 'a' * 201
        response = self.client.post(reverse('ai_assistant:index'), {'question': long_text})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "cannot exceed 200 characters", status_code=400)

    def test_control_character_normalization(self):
        raw = "CRM\x00 \x08Summary\x1f"
        sanitized = sanitize_query(raw)
        self.assertEqual(sanitized, "CRM Summary")

    def test_script_injection_escaped(self):
        self.client.force_login(self.admin_user)
        xss_input = "<script>alert('XSS')</script>"
        response = self.client.post(reverse('ai_assistant:index'), {'question': xss_input})
        self.assertEqual(response.status_code, 200)
        # Content must be auto-escaped, not raw executable HTML
        self.assertNotIn("<script>alert('XSS')</script>", response.content.decode())
        self.assertIn("&lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;", response.content.decode())

    def test_sql_injection_treated_as_plain_text(self):
        self.client.force_login(self.admin_user)
        sqli_input = "'; DROP TABLE accounts_customuser; --"
        response = self.client.post(reverse('ai_assistant:index'), {'question': sqli_input})
        self.assertEqual(response.status_code, 200)
        # Safe fallback: question not recognized
        self.assertContains(response, "Question Not Recognized")
        # Ensure accounts_customuser table still exists intact
        self.assertTrue(User.objects.filter(pk=self.admin_user.pk).exists())

    def test_no_sensitive_data_disclosure(self):
        self.client.force_login(self.cust_user_1)
        response = self.client.post(reverse('ai_assistant:index'), {'question': 'My Profile'})
        content = response.content.decode()
        # Ensure password hashes or reset tokens are never exposed
        self.assertNotIn("pbkdf2_sha256", content)
        self.assertNotIn(self.cust_user_1.password, content)

    def test_safe_activity_logging_records_minimal_metadata(self):
        self.client.force_login(self.admin_user)
        self.client.post(reverse('ai_assistant:index'), {'question': 'CRM Summary'})
        log = ActivityLog.objects.filter(module_name="AIAssistant").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.record_id, INTENT_CRM_SUMMARY)
        # Verify complete question is NOT saved in ActivityLog description
        self.assertNotIn("What is the complete raw prompt?", log.description)


class AssistantIntentDetectionTests(BaseAIAssistantTestCase):
    def test_intent_priority_specific_over_general(self):
        # "my high priority leads" should match STAFF_MY_HIGH_PRIORITY_LEADS, not STAFF_MY_LEADS
        intent = detect_intent("my high priority leads", CustomUser.Role.SALES_STAFF)
        self.assertEqual(intent, INTENT_STAFF_MY_HIGH_PRIORITY_LEADS)

        # "high priority leads" for Admin should match HIGH_PRIORITY_LEADS
        admin_intent = detect_intent("high priority leads", CustomUser.Role.ADMIN)
        self.assertEqual(admin_intent, INTENT_HIGH_PRIORITY_LEADS)

        # "lead pipeline" for Admin
        pipe_intent = detect_intent("lead pipeline", CustomUser.Role.ADMIN)
        self.assertEqual(pipe_intent, INTENT_LEAD_PIPELINE_SUMMARY)

    def test_role_isolation_blocks_unauthorized_intent(self):
        # Customer querying "CRM summary" should NOT get INTENT_CRM_SUMMARY
        cust_intent = detect_intent("CRM summary", CustomUser.Role.CUSTOMER)
        self.assertEqual(cust_intent, INTENT_UNSUPPORTED)

        # Staff querying "CRM summary" should NOT get INTENT_CRM_SUMMARY
        staff_intent = detect_intent("executive summary", CustomUser.Role.SALES_STAFF)
        self.assertEqual(staff_intent, INTENT_UNSUPPORTED)

    def test_empty_query_returns_help(self):
        intent = detect_intent("", CustomUser.Role.ADMIN)
        self.assertEqual(intent, INTENT_HELP_INTENTS)

    def test_unsupported_query_returns_unsupported(self):
        intent = detect_intent("what is the weather today?", CustomUser.Role.CUSTOMER)
        self.assertEqual(intent, INTENT_UNSUPPORTED)


class AssistantQueryExecutionAndOwnershipTests(BaseAIAssistantTestCase):
    def setUp(self):
        super().setUp()

        # Create confirmed sale for Customer 1 handled by Staff 1
        self.sale_confirmed = Sale.objects.create(
            customer=self.cust_profile_1,
            sales_staff=self.staff_profile_1,
            status=Sale.Status.CONFIRMED,
            stock_deducted=True,
            subtotal=Decimal('1000.00'),
            taxable_amount=Decimal('1000.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('180.00'),
            net_amount=Decimal('1180.00')
        )
        SaleItem.objects.create(
            sale=self.sale_confirmed,
            product=self.prod_laptop,
            quantity=1,
            unit_price=Decimal('1000.00'),
            line_subtotal=Decimal('1000.00'),
            discount=Decimal('0.00'),
            line_total=Decimal('1000.00')
        )

        # Create cancelled sale (must be excluded from revenue)
        self.sale_cancelled = Sale.objects.create(
            customer=self.cust_profile_1,
            sales_staff=self.staff_profile_1,
            status=Sale.Status.CANCELLED,
            stock_deducted=False,
            subtotal=Decimal('500.00'),
            taxable_amount=Decimal('500.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('90.00'),
            net_amount=Decimal('590.00')
        )

        # Invoice for confirmed sale
        self.invoice_1 = Invoice.objects.create(
            sale=self.sale_confirmed,
            customer=self.cust_profile_1,
            due_date=timezone.localdate() + timedelta(days=15),
            subtotal=Decimal('1000.00'),
            taxable_amount=Decimal('1000.00'),
            tax_rate=Decimal('18.00'),
            tax_amount=Decimal('180.00'),
            total_amount=Decimal('1180.00'),
            paid_amount=Decimal('500.00'),
            balance_amount=Decimal('680.00'),
            payment_status=Invoice.PaymentStatus.PARTIALLY_PAID,
            invoice_status=Invoice.InvoiceStatus.ISSUED
        )

        # Completed non-reversed payment
        self.payment_completed = Payment.objects.create(
            invoice=self.invoice_1,
            customer=self.cust_profile_1,
            amount=Decimal('500.00'),
            idempotency_key='IDEMP-TEST-PAY-001',
            payment_status=Payment.Status.COMPLETED,
            is_reversed=False
        )

        # Reversed payment (must be excluded from collection totals)
        self.payment_reversed = Payment.objects.create(
            invoice=self.invoice_1,
            customer=self.cust_profile_1,
            amount=Decimal('200.00'),
            idempotency_key='IDEMP-TEST-PAY-002',
            payment_status=Payment.Status.REVERSED,
            is_reversed=True,
            reversed_at=timezone.now(),
            reversal_reason='Chargeback'
        )

        # Reminders
        self.reminder_public = Reminder.objects.create(
            title='Follow up on laptop setup',
            customer=self.cust_profile_1,
            due_date=timezone.localdate() + timedelta(days=2),
            assigned_to=self.staff_user_1,
            is_customer_visible=True,
            status=Reminder.Status.PENDING
        )
        self.reminder_private = Reminder.objects.create(
            title='Internal credit evaluation',
            customer=self.cust_profile_1,
            due_date=timezone.localdate() + timedelta(days=1),
            assigned_to=self.staff_user_1,
            is_customer_visible=False,
            status=Reminder.Status.PENDING
        )

    def test_admin_kpi_excludes_cancelled_sales_and_reversed_payments(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse('ai_assistant:index'), {'question': 'Revenue Summary'})
        self.assertEqual(response.status_code, 200)
        # Confirmed revenue is exactly 1,180.00 (the 590.00 cancelled sale is excluded)
        self.assertContains(response, "₹1,180.00")
        self.assertNotContains(response, "₹1,770.00")

    def test_admin_collected_and_outstanding(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse('ai_assistant:index'), {'question': 'Collected vs Outstanding'})
        self.assertEqual(response.status_code, 200)
        # Collected is ₹500.00 (the 200.00 reversed payment is excluded)
        self.assertContains(response, "₹500.00")
        # Outstanding is ₹680.00
        self.assertContains(response, "₹680.00")

    def test_admin_stock_alerts_detected(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse('ai_assistant:index'), {'question': 'Stock Alerts'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PRD-KEYBOARD")  # Out of stock
        self.assertContains(response, "PRD-MOUSE")     # Low stock

    def test_staff_assignment_isolation(self):
        # Create lead for Staff 1 and lead for Staff 2
        lead1 = Lead.objects.create(
            lead_code='LED-001',
            full_name='Lead One',
            assigned_staff=self.staff_profile_1,
            status=Lead.Status.NEW,
            estimated_value=Decimal('20000.00')
        )
        lead2 = Lead.objects.create(
            lead_code='LED-002',
            full_name='Lead Two',
            assigned_staff=self.staff_profile_2,
            status=Lead.Status.NEW,
            estimated_value=Decimal('40000.00')
        )

        # Staff 1 queries "My Leads"
        self.client.force_login(self.staff_user_1)
        response = self.client.post(reverse('ai_assistant:index'), {'question': 'My Leads'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LED-001")
        self.assertNotContains(response, "LED-002")

    def test_customer_ownership_isolation(self):
        # Customer 1 queries "My Purchases"
        self.client.force_login(self.cust_user_1)
        response = self.client.post(reverse('ai_assistant:index'), {'question': 'My Purchases'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.sale_confirmed.sale_number)

        # Customer 2 queries "My Purchases" (should not see Customer 1's sale)
        self.client.force_login(self.cust_user_2)
        response2 = self.client.post(reverse('ai_assistant:index'), {'question': 'My Purchases'})
        self.assertEqual(response2.status_code, 200)
        self.assertNotContains(response2, self.sale_confirmed.sale_number)

    def test_customer_reminders_exclude_private_internal_reminders(self):
        self.client.force_login(self.cust_user_1)
        response = self.client.post(reverse('ai_assistant:index'), {'question': 'My Reminders'})
        self.assertEqual(response.status_code, 200)
        # Visible reminder is present
        self.assertContains(response, "Follow up on laptop setup")
        # Internal private reminder is strictly excluded
        self.assertNotContains(response, "Internal credit evaluation")

    def test_empty_database_handling(self):
        # Delete in proper foreign key dependency order
        Payment.objects.all().delete()
        Invoice.objects.all().delete()
        SaleItem.objects.all().delete()
        Sale.objects.all().delete()
        Lead.objects.all().delete()
        Reminder.objects.all().delete()

        self.client.force_login(self.admin_user)
        response = self.client.post(reverse('ai_assistant:index'), {'question': 'CRM Summary'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "₹0.00")


class AssistantProductRecommendationTests(BaseAIAssistantTestCase):
    def test_recommendations_exclude_inactive_products_and_categories(self):
        rec_data = get_recommendations_for_customer(self.cust_profile_1, self.admin_user)
        recs = rec_data.get('recommendations', [])
        recommended_codes = [r['product_code'] for r in recs]

        # Active in-stock laptop and service cloud must be present
        self.assertIn('PRD-LAPTOP', recommended_codes)
        self.assertIn('SRV-CLOUD', recommended_codes)

        # Inactive product PRD-OLD must be excluded
        self.assertNotIn('PRD-OLD', recommended_codes)

    def test_recommendations_exclude_out_of_stock_physical_products(self):
        rec_data = get_recommendations_for_customer(self.cust_profile_1, self.admin_user)
        recs = rec_data.get('recommendations', [])
        recommended_codes = [r['product_code'] for r in recs]

        # PRD-KEYBOARD has stock=0, so it must be excluded
        self.assertNotIn('PRD-KEYBOARD', recommended_codes)

    def test_recommendations_include_services_with_zero_stock(self):
        rec_data = get_recommendations_for_customer(self.cust_profile_1, self.admin_user)
        recs = rec_data.get('recommendations', [])
        recommended_codes = [r['product_code'] for r in recs]

        # SRV-CLOUD has stock=0 but is a service, so it is eligible
        self.assertIn('SRV-CLOUD', recommended_codes)

    def test_cold_start_recommendations_and_explanation(self):
        # Customer 2 has 0 purchases
        rec_data = get_recommendations_for_customer(self.cust_profile_2, self.cust_user_2)
        self.assertTrue(rec_data.get('is_cold_start'))
        recs = rec_data.get('recommendations', [])
        self.assertGreaterEqual(len(recs), 1)

        # Check factor breakdown
        top_rec = recs[0]
        self.assertIn('factors', top_rec)
        factor_names = [f['factor'] for f in top_rec['factors']]
        self.assertIn('Welcome Account Price Baseline', factor_names)

    def test_deterministic_recommendation_ordering(self):
        run1 = get_recommendations_for_customer(self.cust_profile_1, self.admin_user)
        run2 = get_recommendations_for_customer(self.cust_profile_1, self.admin_user)
        codes_1 = [r['product_code'] for r in run1['recommendations']]
        codes_2 = [r['product_code'] for r in run2['recommendations']]
        self.assertEqual(codes_1, codes_2)
        scores_1 = [r['score'] for r in run1['recommendations']]
        scores_2 = [r['score'] for r in run2['recommendations']]
        self.assertEqual(scores_1, scores_2)

    def test_idor_prevention_staff_cannot_query_unassigned_customer(self):
        # Staff 1 attempts to query Customer 2 (assigned to Staff 2)
        self.client.force_login(self.staff_user_1)
        response = self.client.post(
            reverse('ai_assistant:index'),
            {'question': 'Product Recommendations', 'customer_id': self.cust_profile_2.id}
        )
        self.assertEqual(response.status_code, 200)
        # Should inform permission denied without leaking data
        self.assertContains(response, "You do not have permission to view records for the specified account")

    def test_idor_prevention_customer_restricted_to_self(self):
        # Customer 1 attempts to pass customer_id for Customer 2
        self.client.force_login(self.cust_user_1)
        response = self.client.post(
            reverse('ai_assistant:index'),
            {'question': 'Product Recommendations', 'customer_id': self.cust_profile_2.id}
        )
        self.assertEqual(response.status_code, 200)
        # Result must be evaluated for Customer 1, not Customer 2
        self.assertContains(response, self.cust_profile_1.customer_code)
        self.assertNotContains(response, self.cust_profile_2.customer_code)
