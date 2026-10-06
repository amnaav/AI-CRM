from decimal import Decimal
from datetime import timedelta
from io import StringIO
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models.deletion import ProtectedError
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomerProfile, SalesStaffProfile
from leads.models import Lead
from sales.models import Invoice, Sale
from interactions.models import Interaction
from notifications.models import Notification
from reminders.models import Reminder
from reminders.services import (
    create_reminder,
    complete_reminder,
    cancel_reminder
)

User = get_user_model()


class ReminderValidationAndIntegrityTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_rem1', email='sr1@crm.local', password='Password123!', role='SALES_STAFF'
        )
        self.staff_profile = SalesStaffProfile.objects.create(user=self.staff_user)

        self.cust_user = User.objects.create_user(
            username='cust_rem1', email='cr1@crm.local', password='Password123!', role='CUSTOMER'
        )
        self.customer = CustomerProfile.objects.create(user=self.cust_user)

        self.lead = Lead.objects.create(
            lead_code='LD-REM01',
            full_name='Lead Reminder',
            email='lr@example.com',
            phone='1234567890'
        )

    def test_completed_requires_completed_at(self):
        reminder = Reminder(
            reminder_type=Reminder.ReminderType.CALL,
            title='Check payment receipt',
            due_date=timezone.now() + timedelta(days=1),
            assigned_to=self.staff_user,
            status=Reminder.Status.COMPLETED,
            completed_at=None  # Missing completed_at
        )
        with self.assertRaises(ValidationError):
            reminder.full_clean()

    def test_pending_cannot_have_completed_at(self):
        reminder = Reminder(
            reminder_type=Reminder.ReminderType.CALL,
            title='Pending call',
            due_date=timezone.now() + timedelta(days=1),
            assigned_to=self.staff_user,
            status=Reminder.Status.PENDING,
            completed_at=timezone.now()  # Invalid completed_at
        )
        with self.assertRaises(ValidationError):
            reminder.full_clean()

    def test_customer_visible_requires_customer(self):
        reminder = Reminder(
            reminder_type=Reminder.ReminderType.TASK,
            title='Public meeting',
            due_date=timezone.now() + timedelta(days=1),
            assigned_to=self.staff_user,
            is_customer_visible=True,
            customer=None  # Missing customer
        )
        with self.assertRaises(ValidationError):
            reminder.full_clean()

    def test_assignee_must_be_staff_or_admin(self):
        reminder = Reminder(
            reminder_type=Reminder.ReminderType.TASK,
            title='Internal task',
            due_date=timezone.now() + timedelta(days=1),
            assigned_to=self.cust_user,  # Invalid: CUSTOMER role
        )
        with self.assertRaises(ValidationError):
            reminder.full_clean()

    def test_timezone_aware_overdue_logic(self):
        today = timezone.localdate()
        past_reminder = Reminder.objects.create(
            reminder_type=Reminder.ReminderType.TASK,
            title='Past Task',
            due_date=today - timedelta(days=1),
            assigned_to=self.staff_user,
            status=Reminder.Status.PENDING
        )
        self.assertTrue(past_reminder.is_overdue)

        today_reminder = Reminder.objects.create(
            reminder_type=Reminder.ReminderType.TASK,
            title='Today Task',
            due_date=today,
            assigned_to=self.staff_user,
            status=Reminder.Status.PENDING
        )
        self.assertFalse(today_reminder.is_overdue)

        future_reminder = Reminder.objects.create(
            reminder_type=Reminder.ReminderType.TASK,
            title='Future Task',
            due_date=today + timedelta(days=1),
            assigned_to=self.staff_user,
            status=Reminder.Status.PENDING
        )
        self.assertFalse(future_reminder.is_overdue)


class ReminderDeletionSafetyTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_del_rem', email='sdr@crm.local', password='Password123!', role='SALES_STAFF'
        )
        self.cust_user = User.objects.create_user(
            username='cust_del_rem', email='cdr@crm.local', password='Password123!', role='CUSTOMER'
        )
        self.customer = CustomerProfile.objects.create(user=self.cust_user)
        self.lead = Lead.objects.create(
            lead_code='LD-DEL-R', full_name='Del R Lead', email='delr@example.com', phone='1112223333'
        )
        self.reminder = Reminder.objects.create(
            reminder_type=Reminder.ReminderType.TASK,
            title='Protected Task',
            due_date=timezone.now() + timedelta(days=2),
            assigned_to=self.staff_user,
            customer=self.customer,
            lead=self.lead
        )

    def test_customer_deletion_protected(self):
        with self.assertRaises(ProtectedError):
            self.customer.delete()

    def test_lead_deletion_protected(self):
        with self.assertRaises(ProtectedError):
            self.lead.delete()

    def test_assigned_user_deletion_protected(self):
        with self.assertRaises(ProtectedError):
            self.staff_user.delete()


class ReminderLifecycleAndServiceTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_life', email='sl@crm.local', password='Password123!', role='SALES_STAFF'
        )
        self.admin_user = User.objects.create_user(
            username='admin_life', email='al@crm.local', password='Password123!', role='ADMIN'
        )

    def test_complete_reminder_idempotent(self):
        reminder = create_reminder(
            user=self.admin_user,
            reminder_type=Reminder.ReminderType.FOLLOW_UP,
            title='Follow up on proposal',
            due_date=timezone.now() + timedelta(days=1),
            assigned_to=self.staff_user
        )
        self.assertEqual(reminder.status, Reminder.Status.PENDING)
        self.assertIsNone(reminder.completed_at)

        # First completion
        completed = complete_reminder(reminder.pk, user=self.staff_user)
        self.assertEqual(completed.status, Reminder.Status.COMPLETED)
        self.assertIsNotNone(completed.completed_at)
        self.assertEqual(completed.completed_by, self.staff_user)
        first_completed_at = completed.completed_at

        # Idempotent repeat completion
        completed_again = complete_reminder(reminder.pk, user=self.admin_user)
        self.assertEqual(completed_again.status, Reminder.Status.COMPLETED)
        self.assertEqual(completed_again.completed_at, first_completed_at)

    def test_cancel_reminder_idempotent(self):
        reminder = create_reminder(
            user=self.admin_user,
            reminder_type=Reminder.ReminderType.CALL,
            title='Call to be cancelled',
            due_date=timezone.now() + timedelta(days=1),
            assigned_to=self.staff_user
        )
        cancelled = cancel_reminder(reminder.pk, user=self.admin_user)
        self.assertEqual(cancelled.status, Reminder.Status.CANCELLED)
        self.assertIsNone(cancelled.completed_at)

        # Idempotent second cancel
        cancelled_again = cancel_reminder(reminder.pk, user=self.staff_user)
        self.assertEqual(cancelled_again.status, Reminder.Status.CANCELLED)


class ReminderViewsAndPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            username='staff_views', email='sv@crm.local', password='Password123!', role='SALES_STAFF'
        )
        self.staff_profile = SalesStaffProfile.objects.create(user=self.staff_user)

        self.cust_user1 = User.objects.create_user(
            username='cust_views1', email='cv1@crm.local', password='Password123!', role='CUSTOMER'
        )
        self.customer1 = CustomerProfile.objects.create(user=self.cust_user1, company='Alpha Co')

        self.cust_user2 = User.objects.create_user(
            username='cust_views2', email='cv2@crm.local', password='Password123!', role='CUSTOMER'
        )
        self.customer2 = CustomerProfile.objects.create(user=self.cust_user2, company='Beta Co')

        # Visible reminder for Customer 1
        self.rem_visible = Reminder.objects.create(
            reminder_type=Reminder.ReminderType.MEETING,
            title='Annual Onboarding Session',
            due_date=timezone.now() + timedelta(days=1),
            assigned_to=self.staff_user,
            customer=self.customer1,
            is_customer_visible=True
        )

        # Internal reminder for Customer 1
        self.rem_internal = Reminder.objects.create(
            reminder_type=Reminder.ReminderType.TASK,
            title='Internal Account Health Audit',
            due_date=timezone.now() + timedelta(days=1),
            assigned_to=self.staff_user,
            customer=self.customer1,
            is_customer_visible=False
        )

    def test_staff_schedule_tabs(self):
        self.client.login(username='staff_views', password='Password123!')
        res = self.client.get(reverse('reminders:reminder_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Annual Onboarding Session')
        self.assertContains(res, 'Internal Account Health Audit')

    def test_complete_and_cancel_post_only(self):
        self.client.login(username='staff_views', password='Password123!')

        # GET should be rejected with 405
        res_get_c = self.client.get(reverse('reminders:reminder_complete', args=[self.rem_visible.pk]))
        self.assertEqual(res_get_c.status_code, 405)

        res_get_x = self.client.get(reverse('reminders:reminder_cancel', args=[self.rem_visible.pk]))
        self.assertEqual(res_get_x.status_code, 405)

        # POST completes successfully
        res_post_c = self.client.post(reverse('reminders:reminder_complete', args=[self.rem_visible.pk]))
        self.assertEqual(res_post_c.status_code, 302)
        self.rem_visible.refresh_from_db()
        self.assertEqual(self.rem_visible.status, Reminder.Status.COMPLETED)

    def test_customer_portal_sees_only_visible_reminders(self):
        self.client.login(username='cust_views1', password='Password123!')
        res = self.client.get(reverse('reminders:customer_reminder_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Annual Onboarding Session')
        self.assertNotContains(res, 'Internal Account Health Audit')

    def test_customer_cannot_access_internal_reminder_detail(self):
        self.client.login(username='cust_views1', password='Password123!')
        res = self.client.get(reverse('reminders:reminder_detail', args=[self.rem_internal.pk]))
        self.assertEqual(res.status_code, 404)

    def test_customer_cannot_access_other_customer_reminder(self):
        self.client.login(username='cust_views2', password='Password123!')
        res = self.client.get(reverse('reminders:reminder_detail', args=[self.rem_visible.pk]))
        self.assertEqual(res.status_code, 404)

    def test_customer_cannot_create_or_complete_reminders(self):
        self.client.login(username='cust_views1', password='Password123!')
        res_create = self.client.get(reverse('reminders:reminder_create'))
        self.assertEqual(res_create.status_code, 403)

        res_complete = self.client.post(reverse('reminders:reminder_complete', args=[self.rem_visible.pk]))
        self.assertEqual(res_complete.status_code, 403)


class SmartFollowUpManagementCommandTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin_sfu', email='asfu@crm.local', password='Password123!', role='ADMIN'
        )
        self.staff_user = User.objects.create_user(
            username='staff_sfu', email='ssfu@crm.local', password='Password123!', role='SALES_STAFF'
        )
        self.staff_profile = SalesStaffProfile.objects.create(user=self.staff_user)

        self.cust_user = User.objects.create_user(
            username='cust_sfu', email='csfu@crm.local', password='Password123!', role='CUSTOMER'
        )
        self.customer = CustomerProfile.objects.create(user=self.cust_user, assigned_staff=self.staff_profile)

        # Stale lead (> 3 days old)
        self.stale_lead = Lead.objects.create(
            lead_code='LD-STALE',
            full_name='Stale Lead Guy',
            email='stale@example.com',
            phone='1112223333',
            status=Lead.Status.NEW,
            assigned_staff=self.staff_profile
        )
        # Manually backdate created_at if possible or use overdue invoice
        self.sale = Sale.objects.create(
            sale_number='ORD-SFU-01',
            customer=self.customer,
            sales_staff=self.staff_profile,
            subtotal=Decimal('500.00'),
            taxable_amount=Decimal('500.00'),
            tax_amount=Decimal('90.00'),
            net_amount=Decimal('590.00'),
            status=Sale.Status.CONFIRMED,
            stock_deducted=True
        )
        self.overdue_invoice = Invoice.objects.create(
            invoice_number='INV-SFU-01',
            sale=self.sale,
            customer=self.customer,
            due_date=timezone.localdate() - timedelta(days=5),
            subtotal=Decimal('500.00'),
            taxable_amount=Decimal('500.00'),
            tax_amount=Decimal('90.00'),
            total_amount=Decimal('590.00'),
            balance_amount=Decimal('590.00'),
            invoice_status=Invoice.InvoiceStatus.OVERDUE,
            payment_status=Invoice.PaymentStatus.UNPAID
        )

    def test_dry_run_creates_zero_records(self):
        initial_reminders = Reminder.objects.count()
        initial_notifs = Notification.objects.count()

        out = StringIO()
        call_command('smart_follow_up', '--dry-run', stdout=out)
        output = out.getvalue()

        self.assertIn('DRY RUN', output)
        self.assertEqual(Reminder.objects.count(), initial_reminders)
        self.assertEqual(Notification.objects.count(), initial_notifs)

    def test_real_run_and_idempotency_on_repeat(self):
        out1 = StringIO()
        call_command('smart_follow_up', stdout=out1)
        output1 = out1.getvalue()
        self.assertIn('SMART FOLLOW-UP RUN COMPLETE', output1)

        rem_count_after_first = Reminder.objects.count()
        notif_count_after_first = Notification.objects.count()
        self.assertGreater(rem_count_after_first, 0)

        # Second execution on the same day must be idempotent
        out2 = StringIO()
        call_command('smart_follow_up', stdout=out2)
        output2 = out2.getvalue()

        self.assertEqual(Reminder.objects.count(), rem_count_after_first)
        self.assertEqual(Notification.objects.count(), notif_count_after_first)
        self.assertIn('Created: 0', output2)
