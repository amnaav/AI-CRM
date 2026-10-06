from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomerProfile, SalesStaffProfile
from leads.models import Lead
from sales.models import Sale
from interactions.models import Interaction
from interactions.services import create_interaction, update_interaction
from activity_logs.models import ActivityLog

User = get_user_model()


class InteractionValidationTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff1',
            email='staff1@crm.local',
            password='Password123!',
            role='SALES_STAFF'
        )
        self.staff_profile = SalesStaffProfile.objects.create(user=self.staff_user)

        self.cust_user1 = User.objects.create_user(
            username='cust1',
            email='cust1@crm.local',
            password='Password123!',
            role='CUSTOMER'
        )
        self.customer1 = CustomerProfile.objects.create(user=self.cust_user1, company='Acme Corp')

        self.cust_user2 = User.objects.create_user(
            username='cust2',
            email='cust2@crm.local',
            password='Password123!',
            role='CUSTOMER'
        )
        self.customer2 = CustomerProfile.objects.create(user=self.cust_user2, company='Beta Ltd')

        self.lead = Lead.objects.create(
            lead_code='LD-TEST01',
            full_name='Lead One',
            email='lead1@example.com',
            phone='1234567890',
            assigned_staff=self.staff_profile
        )

    def test_internal_note_cannot_be_customer_visible(self):
        interaction = Interaction(
            interaction_type=Interaction.InteractionType.NOTE,
            subject='Internal Staff Assessment',
            notes='Confidential review',
            customer=self.customer1,
            is_customer_visible=True,
            created_by=self.staff_user,
            staff=self.staff_profile
        )
        with self.assertRaises(ValidationError):
            interaction.full_clean()

    def test_customer_visible_requires_customer(self):
        interaction = Interaction(
            interaction_type=Interaction.InteractionType.CALL,
            subject='Phone Discussion',
            lead=self.lead,
            customer=None,
            is_customer_visible=True,
            created_by=self.staff_user,
            staff=self.staff_profile
        )
        with self.assertRaises(ValidationError):
            interaction.full_clean()

    def test_converted_lead_and_customer_mismatch(self):
        self.lead.converted_customer = self.customer1
        self.lead.save()

        # Associating interaction with customer2 while lead belongs to customer1
        interaction = Interaction(
            interaction_type=Interaction.InteractionType.MEETING,
            subject='Project Kickoff',
            lead=self.lead,
            customer=self.customer2,
            created_by=self.staff_user,
            staff=self.staff_profile
        )
        with self.assertRaises(ValidationError):
            interaction.full_clean()

    def test_sale_and_customer_mismatch(self):
        sale = Sale.objects.create(
            sale_number='ORD-001',
            customer=self.customer1,
            sales_staff=self.staff_profile,
            subtotal=Decimal('100.00'),
            taxable_amount=Decimal('100.00'),
            tax_amount=Decimal('18.00'),
            net_amount=Decimal('118.00')
        )
        interaction = Interaction(
            interaction_type=Interaction.InteractionType.EMAIL,
            subject='Invoice dispatch',
            customer=self.customer2,  # mismatch with sale.customer1
            sale=sale,
            created_by=self.staff_user,
            staff=self.staff_profile
        )
        with self.assertRaises(ValidationError):
            interaction.full_clean()


class InteractionDeletionSafetyTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_del', email='sd@crm.local', password='Password123!', role='SALES_STAFF'
        )
        self.staff_profile = SalesStaffProfile.objects.create(user=self.staff_user)

        self.cust_user = User.objects.create_user(
            username='cust_del', email='cd@crm.local', password='Password123!', role='CUSTOMER'
        )
        self.customer = CustomerProfile.objects.create(user=self.cust_user)

        self.lead = Lead.objects.create(
            lead_code='LD-DEL01',
            full_name='Delete Test Lead',
            email='lead_del@example.com',
            phone='9876543210'
        )

        self.interaction = Interaction.objects.create(
            interaction_type=Interaction.InteractionType.CALL,
            subject='Follow-up Call',
            customer=self.customer,
            lead=self.lead,
            staff=self.staff_profile,
            created_by=self.staff_user
        )

    def test_customer_deletion_protected(self):
        with self.assertRaises(ProtectedError):
            self.customer.delete()

    def test_lead_deletion_protected(self):
        with self.assertRaises(ProtectedError):
            self.lead.delete()

    def test_created_by_user_deletion_sets_null(self):
        user_id = self.staff_user.id
        self.staff_user.delete()
        self.interaction.refresh_from_db()
        self.assertIsNone(self.interaction.created_by)


class InteractionServiceAndLoggingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin_act', email='adm@crm.local', password='Password123!', role='ADMIN'
        )
        self.staff_user = User.objects.create_user(
            username='staff_act', email='stf@crm.local', password='Password123!', role='SALES_STAFF'
        )
        self.staff_profile = SalesStaffProfile.objects.create(user=self.staff_user)
        self.cust_user = User.objects.create_user(
            username='cust_act', email='cst@crm.local', password='Password123!', role='CUSTOMER'
        )
        self.customer = CustomerProfile.objects.create(user=self.cust_user)

    def test_create_interaction_service_logs_activity(self):
        interaction = create_interaction(
            user=self.admin,
            interaction_type=Interaction.InteractionType.FOLLOW_UP,
            subject='Contract review meeting',
            notes='Client agreed to review terms',
            customer=self.customer,
            staff=self.staff_profile,
            is_customer_visible=True,
            metadata={'confidential_token': 'secret-12345'}
        )
        self.assertIsNotNone(interaction.pk)
        self.assertEqual(interaction.created_by, self.admin)
        self.assertEqual(interaction.staff, self.staff_profile)
        self.assertTrue(interaction.is_customer_visible)

        # Verify activity log
        log = ActivityLog.objects.filter(entity_type='Interaction', entity_id=interaction.pk).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.action, ActivityLog.ActionChoices.CREATE)
        self.assertNotIn('secret-12345', str(log.metadata))

    def test_update_interaction_service_logs_activity(self):
        interaction = create_interaction(
            user=self.admin,
            interaction_type=Interaction.InteractionType.CALL,
            subject='Initial pitch',
            customer=self.customer,
            staff=self.staff_profile
        )
        updated = update_interaction(
            interaction=interaction,
            user=self.admin,
            subject='Updated pitch title',
            notes='Updated details'
        )
        self.assertEqual(updated.subject, 'Updated pitch title')
        log = ActivityLog.objects.filter(
            entity_type='Interaction',
            entity_id=interaction.pk,
            action=ActivityLog.ActionChoices.UPDATE
        ).first()
        self.assertIsNotNone(log)


class InteractionViewPermissionsAndCustomerIsolationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(username='admin_ui', email='admin_ui@crm.local', password='Password123!', role='ADMIN', is_staff=True)
        self.staff_user = User.objects.create_user(username='staff_ui', email='staff_ui@crm.local', password='Password123!', role='SALES_STAFF')
        self.staff_profile = SalesStaffProfile.objects.create(user=self.staff_user)

        self.cust_user1 = User.objects.create_user(username='cust1_ui', email='c1@crm.local', password='Password123!', role='CUSTOMER')
        self.customer1 = CustomerProfile.objects.create(user=self.cust_user1, company='Company A')

        self.cust_user2 = User.objects.create_user(username='cust2_ui', email='c2@crm.local', password='Password123!', role='CUSTOMER')
        self.customer2 = CustomerProfile.objects.create(user=self.cust_user2, company='Company B')

        # Visible interaction for Customer 1
        self.it_visible = Interaction.objects.create(
            interaction_type=Interaction.InteractionType.CALL,
            subject='Order Update Call',
            customer=self.customer1,
            is_customer_visible=True,
            staff=self.staff_profile,
            created_by=self.staff_user
        )

        # Hidden internal note for Customer 1
        self.it_internal = Interaction.objects.create(
            interaction_type=Interaction.InteractionType.NOTE,
            subject='Internal Staff Review',
            notes='Risk evaluation',
            customer=self.customer1,
            is_customer_visible=False,
            staff=self.staff_profile,
            created_by=self.staff_user
        )

    def test_staff_can_view_all_interactions(self):
        self.client.login(username='staff_ui', password='Password123!')
        res = self.client.get(reverse('interactions:interaction_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Order Update Call')
        self.assertContains(res, 'Internal Staff Review')

    def test_customer_portal_sees_only_visible_own_interactions(self):
        self.client.login(username='cust1_ui', password='Password123!')
        res = self.client.get(reverse('interactions:customer_interaction_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Order Update Call')
        self.assertNotContains(res, 'Internal Staff Review')

    def test_customer_cannot_access_invisible_interaction_detail(self):
        self.client.login(username='cust1_ui', password='Password123!')
        res = self.client.get(reverse('interactions:interaction_detail', args=[self.it_internal.pk]))
        self.assertEqual(res.status_code, 404)

    def test_customer_cannot_access_other_customer_interaction(self):
        self.client.login(username='cust2_ui', password='Password123!')
        res = self.client.get(reverse('interactions:interaction_detail', args=[self.it_visible.pk]))
        self.assertEqual(res.status_code, 404)

    def test_customer_cannot_create_or_update_interactions(self):
        self.client.login(username='cust1_ui', password='Password123!')
        res_create = self.client.get(reverse('interactions:interaction_create'))
        self.assertEqual(res_create.status_code, 403)

        res_update = self.client.get(reverse('interactions:interaction_update', args=[self.it_visible.pk]))
        self.assertEqual(res_update.status_code, 403)
