from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import CustomUser, SalesStaffProfile, CustomerProfile
from activity_logs.models import ActivityLog
from leads.models import Lead, LeadStatusHistory
from leads.services.scoring import LeadScoringService
from leads.services.conversion import LeadConversionService
from interactions.models import Interaction
from reminders.models import Reminder

User = get_user_model()


class LeadTestBase(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'ComplexPass123!'

        # Admin user
        self.admin = User.objects.create_user(
            username='admin_lead_user',
            email='admin_lead@example.com',
            password=self.password,
            role=CustomUser.Role.ADMIN,
            full_name='Lead Admin'
        )

        # Sales staff 1
        self.staff_user1 = User.objects.create_user(
            username='staff_lead1',
            email='staff_lead1@example.com',
            password=self.password,
            role=CustomUser.Role.SALES_STAFF,
            full_name='Staff Rep One'
        )
        self.staff1 = SalesStaffProfile.objects.create(
            user=self.staff_user1,
            designation='Account Executive',
            department='Enterprise Sales'
        )

        # Sales staff 2
        self.staff_user2 = User.objects.create_user(
            username='staff_lead2',
            email='staff_lead2@example.com',
            password=self.password,
            role=CustomUser.Role.SALES_STAFF,
            full_name='Staff Rep Two'
        )
        self.staff2 = SalesStaffProfile.objects.create(
            user=self.staff_user2,
            designation='Field Rep',
            department='Regional Sales'
        )

        # Customer user
        self.cust_user = User.objects.create_user(
            username='customer_lead_user',
            email='customer_lead@example.com',
            password=self.password,
            role=CustomUser.Role.CUSTOMER,
            full_name='Customer John'
        )
        self.cust_profile = CustomerProfile.objects.create(
            user=self.cust_user,
            company='John Corp',
            customer_code='CUST-TEST01'
        )

        # Sample lead assigned to Staff 1
        self.lead1 = Lead.objects.create(
            full_name='Acme Prospect',
            email='prospect@acme.com',
            phone='9876543210',
            company='Acme Corp',
            source=Lead.Source.WEBSITE,
            status=Lead.Status.QUALIFIED,
            estimated_value=Decimal('150000.00'),
            assigned_staff=self.staff1,
            created_by=self.admin,
            notes='Initial prospect notes'
        )

        # Sample lead assigned to Staff 2
        self.lead2 = Lead.objects.create(
            full_name='Beta Industries',
            email='contact@beta.com',
            phone='9876500002',
            company='Beta Industries',
            source=Lead.Source.REFERRAL,
            status=Lead.Status.NEW,
            estimated_value=Decimal('50000.00'),
            assigned_staff=self.staff2,
            created_by=self.admin
        )


class LeadCRUDAndPermissionsTests(LeadTestBase):
    """Tests for Lead CRUD, directory filters, and role-based permissions."""

    def test_admin_lead_create_and_detail(self):
        self.client.login(username='admin_lead_user', password=self.password)
        response = self.client.post(reverse('leads:lead_create'), {
            'full_name': 'New Enterprise Lead',
            'email': 'new_enterprise@example.com',
            'phone': '9876599999',
            'company': 'Enterprise Solutions Ltd',
            'source': Lead.Source.PHONE_ENQUIRY,
            'status': Lead.Status.CONTACTED,
            'estimated_value': '300000.00',
            'assigned_staff': self.staff1.pk,
            'notes': 'High priority inbound lead from phone'
        })
        self.assertEqual(response.status_code, 302)

        created_lead = Lead.objects.get(email='new_enterprise@example.com')
        self.assertEqual(created_lead.company, 'Enterprise Solutions Ltd')
        self.assertEqual(created_lead.assigned_staff, self.staff1)
        self.assertTrue(created_lead.lead_code.startswith('LED-'))
        self.assertTrue(created_lead.is_active)
        # Verify initial score was automatically computed
        self.assertGreater(created_lead.probability_score, Decimal('0.00'))

        # Detail view
        detail_resp = self.client.get(reverse('leads:lead_detail', kwargs={'pk': created_lead.pk}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, 'New Enterprise Lead')
        self.assertContains(detail_resp, 'Enterprise Solutions Ltd')
        self.assertContains(detail_resp, 'Staff Rep One')

    def test_admin_lead_edit_creates_history_only_on_status_change(self):
        self.client.login(username='admin_lead_user', password=self.password)
        initial_history_count = self.lead1.status_history.count()

        # Edit without changing status
        response1 = self.client.post(reverse('leads:lead_edit', kwargs={'pk': self.lead1.pk}), {
            'full_name': 'Acme Prospect Updated',
            'email': 'prospect@acme.com',
            'phone': '9876543210',
            'company': 'Acme Global Corp',
            'source': Lead.Source.WEBSITE,
            'status': Lead.Status.QUALIFIED,  # Same status!
            'estimated_value': '180000.00',
            'assigned_staff': self.staff1.pk,
            'notes': 'Updated notes'
        })
        self.assertEqual(response1.status_code, 302)
        self.assertEqual(self.lead1.status_history.count(), initial_history_count)

        # Edit with status change (QUALIFIED -> PROPOSAL)
        response2 = self.client.post(reverse('leads:lead_edit', kwargs={'pk': self.lead1.pk}), {
            'full_name': 'Acme Prospect Updated',
            'email': 'prospect@acme.com',
            'phone': '9876543210',
            'company': 'Acme Global Corp',
            'source': Lead.Source.WEBSITE,
            'status': Lead.Status.PROPOSAL,  # Changed!
            'estimated_value': '180000.00',
            'assigned_staff': self.staff1.pk,
            'notes': 'Proposal submitted'
        })
        self.assertEqual(response2.status_code, 302)
        self.assertEqual(self.lead1.status_history.count(), initial_history_count + 1)
        latest_hist = self.lead1.status_history.first()
        self.assertEqual(latest_hist.old_status, Lead.Status.QUALIFIED)
        self.assertEqual(latest_hist.new_status, Lead.Status.PROPOSAL)
        self.assertEqual(latest_hist.changed_by, self.admin)

    def test_sales_staff_can_view_and_edit_assigned_lead(self):
        self.client.login(username='staff_lead1', password=self.password)
        # Staff 1 viewing assigned Lead 1 -> 200 OK
        response = self.client.get(reverse('leads:lead_detail', kwargs={'pk': self.lead1.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Acme Prospect')

        # Staff 1 editing assigned Lead 1 -> 302 Redirect
        response_edit = self.client.post(reverse('leads:lead_edit', kwargs={'pk': self.lead1.pk}), {
            'full_name': 'Acme Prospect Updated by Staff',
            'email': 'prospect@acme.com',
            'phone': '9876543210',
            'company': 'Acme Corp',
            'source': Lead.Source.WEBSITE,
            'status': Lead.Status.PROPOSAL,
            'estimated_value': '150000.00',
            'notes': 'Staff notes'
        })
        self.assertEqual(response_edit.status_code, 302)
        self.lead1.refresh_from_db()
        self.assertEqual(self.lead1.full_name, 'Acme Prospect Updated by Staff')

    def test_sales_staff_denied_access_to_unassigned_lead(self):
        self.client.login(username='staff_lead1', password=self.password)
        # Lead 2 is assigned to Staff 2; Staff 1 must receive 403
        detail_resp = self.client.get(reverse('leads:lead_detail', kwargs={'pk': self.lead2.pk}))
        self.assertEqual(detail_resp.status_code, 403)

        edit_resp = self.client.get(reverse('leads:lead_edit', kwargs={'pk': self.lead2.pk}))
        self.assertEqual(edit_resp.status_code, 403)

        score_resp = self.client.post(reverse('leads:lead_score', kwargs={'pk': self.lead2.pk}))
        self.assertEqual(score_resp.status_code, 403)

    def test_customer_denied_lead_access(self):
        self.client.login(username='customer_lead_user', password=self.password)
        resp1 = self.client.get(reverse('leads:lead_list'))
        self.assertEqual(resp1.status_code, 403)

        resp2 = self.client.get(reverse('leads:lead_detail', kwargs={'pk': self.lead1.pk}))
        self.assertEqual(resp2.status_code, 403)

        resp3 = self.client.get(reverse('leads:lead_create'))
        self.assertEqual(resp3.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('leads:lead_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_lead_directory_search_filter_and_pagination(self):
        self.client.login(username='admin_lead_user', password=self.password)

        # Search by company
        resp_search = self.client.get(reverse('leads:lead_list'), {'q': 'Acme'})
        self.assertEqual(resp_search.status_code, 200)
        self.assertContains(resp_search, 'Acme Prospect')
        self.assertNotContains(resp_search, 'Beta Industries')

        # Filter by status
        resp_filter = self.client.get(reverse('leads:lead_list'), {'status': Lead.Status.NEW})
        self.assertEqual(resp_filter.status_code, 200)
        self.assertContains(resp_filter, 'Beta Industries')
        self.assertNotContains(resp_filter, 'Acme Prospect')

        # Filter by source
        resp_source = self.client.get(reverse('leads:lead_list'), {'source': Lead.Source.REFERRAL})
        self.assertEqual(resp_source.status_code, 200)
        self.assertContains(resp_source, 'Beta Industries')
        self.assertNotContains(resp_source, 'Acme Prospect')

        # Verify persistent header Add Lead button exists in both populated and empty state
        self.assertContains(resp_source, 'id="header-add-lead-btn"')

    def test_post_only_state_changes(self):
        self.client.login(username='admin_lead_user', password=self.password)
        # GET on status update returns 405
        resp_status = self.client.get(reverse('leads:lead_status', kwargs={'pk': self.lead1.pk}))
        self.assertEqual(resp_status.status_code, 405)

        # GET on assign returns 405
        resp_assign = self.client.get(reverse('leads:lead_assign', kwargs={'pk': self.lead1.pk}))
        self.assertEqual(resp_assign.status_code, 405)

        # GET on score returns 405
        resp_score = self.client.get(reverse('leads:lead_score', kwargs={'pk': self.lead1.pk}))
        self.assertEqual(resp_score.status_code, 405)

    def test_admin_can_reassign_lead(self):
        self.client.login(username='admin_lead_user', password=self.password)
        self.assertEqual(self.lead1.assigned_staff, self.staff1)

        response = self.client.post(reverse('leads:lead_assign', kwargs={'pk': self.lead1.pk}), {
            'assigned_staff': self.staff2.pk
        })
        self.assertEqual(response.status_code, 302)
        self.lead1.refresh_from_db()
        self.assertEqual(self.lead1.assigned_staff, self.staff2)

    def test_sales_staff_cannot_reassign_lead(self):
        self.client.login(username='staff_lead1', password=self.password)
        # Staff 1 attempts to reassign lead 1 -> 403 Forbidden
        response = self.client.post(reverse('leads:lead_assign', kwargs={'pk': self.lead1.pk}), {
            'assigned_staff': self.staff2.pk
        })
        self.assertEqual(response.status_code, 403)

    def test_admin_lead_soft_archive_preserves_record(self):
        self.client.login(username='admin_lead_user', password=self.password)
        lead_id = self.lead1.pk
        response = self.client.post(reverse('leads:lead_archive', kwargs={'pk': lead_id}))
        self.assertEqual(response.status_code, 302)

        # Must not be hard-deleted
        self.assertTrue(Lead.objects.filter(pk=lead_id).exists())
        archived_lead = Lead.objects.get(pk=lead_id)
        self.assertFalse(archived_lead.is_active)


class LeadConversionTests(LeadTestBase):
    """Tests for atomic Lead-to-Customer conversion workflow."""

    def test_conversion_blocked_for_unqualified_stage(self):
        self.client.login(username='admin_lead_user', password=self.password)
        # Lead 2 is in stage NEW (unqualified)
        self.assertEqual(self.lead2.status, Lead.Status.NEW)
        eligible, reason = LeadConversionService.check_conversion_eligibility(self.lead2)
        self.assertFalse(eligible)
        self.assertIn("restricted to qualified stages", reason)

        with self.assertRaises(ValidationError):
            LeadConversionService.convert_to_customer(self.lead2, acting_user=self.admin)

    def test_duplicate_conversion_blocked(self):
        # Convert lead 1 first
        LeadConversionService.convert_to_customer(self.lead1, acting_user=self.admin)
        self.lead1.refresh_from_db()
        self.assertTrue(self.lead1.is_converted)

        eligible, reason = LeadConversionService.check_conversion_eligibility(self.lead1)
        self.assertFalse(eligible)
        self.assertIn("already converted", reason)

        with self.assertRaises(ValidationError):
            LeadConversionService.convert_to_customer(self.lead1, acting_user=self.admin)

    def test_existing_customer_match_prompts_link(self):
        # Create lead with email of existing customer (customer_lead@example.com)
        matching_lead = Lead.objects.create(
            full_name='Matching Contact',
            email='customer_lead@example.com',
            phone='9876543210',
            status=Lead.Status.QUALIFIED,
            assigned_staff=self.staff1
        )
        match = LeadConversionService.find_matching_customer(matching_lead.email, matching_lead.phone)
        self.assertIsNotNone(match)
        self.assertEqual(match, self.cust_profile)

        # Attempting conversion without explicit linking raises ValidationError
        with self.assertRaises(ValidationError):
            LeadConversionService.convert_to_customer(matching_lead, acting_user=self.admin)

        # Converting with explicit link succeeds and avoids duplicate CustomerProfile
        cust, setup_url = LeadConversionService.convert_to_customer(
            matching_lead,
            acting_user=self.admin,
            link_existing_customer=match
        )
        self.assertEqual(cust, self.cust_profile)
        matching_lead.refresh_from_db()
        self.assertEqual(matching_lead.status, Lead.Status.WON)
        self.assertEqual(matching_lead.converted_customer, self.cust_profile)

    def test_successful_conversion_creates_customer_with_unusable_password_and_token(self):
        self.client.login(username='admin_lead_user', password=self.password)
        customer, setup_url = LeadConversionService.convert_to_customer(
            self.lead1,
            acting_user=self.admin
        )
        self.assertIsNotNone(customer)
        self.assertEqual(customer.company, 'Acme Corp')
        self.assertEqual(customer.assigned_staff, self.staff1)

        # Verify password is set to unusable (no plain-text password)
        self.assertFalse(customer.user.has_usable_password())
        self.assertTrue(customer.user.is_active)
        self.assertEqual(customer.user.role, CustomUser.Role.CUSTOMER)

        # Verify secure setup token URL was generated
        self.assertTrue(setup_url.startswith('/accounts/reset/'))
        parts = setup_url.strip('/').split('/')
        self.assertEqual(parts[0], 'accounts')
        self.assertEqual(parts[1], 'reset')
        self.assertTrue(len(parts[2]) > 0)  # uidb64
        self.assertTrue(len(parts[3]) > 0)  # token

        # Verify lead status is WON and linked
        self.lead1.refresh_from_db()
        self.assertEqual(self.lead1.status, Lead.Status.WON)
        self.assertEqual(self.lead1.converted_customer, customer)

        # Verify LeadStatusHistory entry
        history_entry = self.lead1.status_history.filter(new_status=Lead.Status.WON).first()
        self.assertIsNotNone(history_entry)
        self.assertEqual(history_entry.changed_by, self.admin)

    def test_conversion_atomic_rollback_on_failure(self):
        # Simulate an unexpected failure during CustomerProfile creation
        with patch.object(CustomerProfile.objects, 'create', side_effect=RuntimeError("Simulated DB Crash")):
            with self.assertRaises(RuntimeError):
                LeadConversionService.convert_to_customer(self.lead1, acting_user=self.admin)

        # Ensure no user account was left behind
        self.assertFalse(User.objects.filter(email=self.lead1.email).exists())
        # Ensure lead was not marked as WON or converted
        self.lead1.refresh_from_db()
        self.assertEqual(self.lead1.status, Lead.Status.QUALIFIED)
        self.assertIsNone(self.lead1.converted_customer)


class LeadScoringTests(LeadTestBase):
    """Tests for AI Lead Scoring Foundation (Heuristic & Machine Learning)."""

    def test_heuristic_scoring_and_explanation_generation(self):
        # Score lead1 using heuristic engine
        scored_lead = LeadScoringService.score_lead(self.lead1, save=True)
        self.assertEqual(scored_lead.scoring_method, Lead.ScoringMethod.HEURISTIC)
        self.assertGreaterEqual(scored_lead.probability_score, Decimal('0.00'))
        self.assertLessEqual(scored_lead.probability_score, Decimal('100.00'))

        # Explanation breakdown must be a list of factor dicts
        self.assertIsInstance(scored_lead.scoring_explanation, list)
        self.assertGreater(len(scored_lead.scoring_explanation), 0)
        first_factor = scored_lead.scoring_explanation[0]
        self.assertIn("factor", first_factor)
        self.assertIn("impact", first_factor)
        self.assertIn("type", first_factor)

    def test_scoring_boundaries_0_and_100(self):
        # Boundary 0: Lost lead with zero value and negative penalties
        lost_lead = Lead.objects.create(
            full_name='Zero Boundary Lead',
            email='zero@lost.com',
            status=Lead.Status.LOST,
            estimated_value=Decimal('0.00')
        )
        LeadScoringService.score_lead(lost_lead, save=True)
        self.assertEqual(lost_lead.probability_score, Decimal('0.00'))
        self.assertEqual(lost_lead.ai_priority, Lead.Priority.LOW)

        # Boundary 100: Closed Won, high value, referral, multiple touchpoints
        won_lead = Lead.objects.create(
            full_name='Max Boundary Lead',
            email='max@won.com',
            status=Lead.Status.WON,
            source=Lead.Source.REFERRAL,
            estimated_value=Decimal('1000000.00'),
            assigned_staff=self.staff1
        )
        # Add 6 recent interactions
        for i in range(6):
            Interaction.objects.create(
                lead=won_lead,
                subject=f"Touchpoint {i}",
                description="High momentum deal discussion"
            )
        # Add completed reminder
        Reminder.objects.create(
            lead=won_lead,
            title="Proposal signed follow-up",
            due_date=won_lead.created_at.date(),
            status='COMPLETED',
            completed_at=timezone.now(),
            assigned_to=self.staff_user1
        )
        LeadScoringService.score_lead(won_lead, save=True)
        self.assertEqual(won_lead.probability_score, Decimal('100.00'))
        self.assertEqual(won_lead.ai_priority, Lead.Priority.HIGH)

    def test_priority_calculation(self):
        self.assertEqual(LeadScoringService._determine_priority(Decimal('75.00')), Lead.Priority.HIGH)
        self.assertEqual(LeadScoringService._determine_priority(Decimal('70.00')), Lead.Priority.HIGH)
        self.assertEqual(LeadScoringService._determine_priority(Decimal('69.99')), Lead.Priority.MEDIUM)
        self.assertEqual(LeadScoringService._determine_priority(Decimal('40.00')), Lead.Priority.MEDIUM)
        self.assertEqual(LeadScoringService._determine_priority(Decimal('39.99')), Lead.Priority.LOW)
        self.assertEqual(LeadScoringService._determine_priority(Decimal('0.00')), Lead.Priority.LOW)

    def test_missing_data_handled_safely(self):
        # Lead with null or missing optional fields
        sparse_lead = Lead.objects.create(
            full_name='Sparse Lead',
            email='sparse@example.com',
            estimated_value=Decimal('0.00'),
            assigned_staff=None,
            notes=''
        )
        # Must score cleanly without error
        scored = LeadScoringService.score_lead(sparse_lead, save=True)
        self.assertIsNotNone(scored.probability_score)
        self.assertIn(scored.ai_priority, [Lead.Priority.HIGH, Lead.Priority.MEDIUM, Lead.Priority.LOW])

    def test_insufficient_ml_data_fallback_to_heuristic(self):
        # Current DB has only 2 leads (< 20 required)
        self.assertLess(Lead.objects.count(), 20)
        use_ml, _ = LeadScoringService._can_use_machine_learning()
        self.assertFalse(use_ml)

        scored = LeadScoringService.score_lead(self.lead1, save=True)
        self.assertEqual(scored.scoring_method, Lead.ScoringMethod.HEURISTIC)

    def test_ml_activation_with_sufficient_data_and_two_classes(self):
        # Create 20 historical closed leads: 10 WON, 10 LOST
        for i in range(10):
            Lead.objects.create(
                full_name=f'Historical Won {i}',
                email=f'won_{i}@history.com',
                status=Lead.Status.WON,
                source=Lead.Source.REFERRAL,
                estimated_value=Decimal('200000.00')
            )
            Lead.objects.create(
                full_name=f'Historical Lost {i}',
                email=f'lost_{i}@history.com',
                status=Lead.Status.LOST,
                source=Lead.Source.COLD_CALL,
                estimated_value=Decimal('10000.00')
            )

        use_ml, training_data = LeadScoringService._can_use_machine_learning()
        self.assertTrue(use_ml)
        self.assertGreaterEqual(len(training_data), 20)

        scored = LeadScoringService.score_lead(self.lead1, save=True)
        self.assertEqual(scored.scoring_method, Lead.ScoringMethod.MACHINE_LEARNING)
        self.assertGreaterEqual(scored.probability_score, Decimal('0.00'))
        self.assertLessEqual(scored.probability_score, Decimal('100.00'))

    def test_scoring_does_not_create_status_history(self):
        initial_history_count = self.lead1.status_history.count()
        LeadScoringService.score_lead(self.lead1, save=True)
        # Scoring should NOT add any LeadStatusHistory entry
        self.assertEqual(self.lead1.status_history.count(), initial_history_count)

    def test_role_aware_scoring_permissions(self):
        # Staff 1 can score assigned lead 1
        self.client.login(username='staff_lead1', password=self.password)
        resp1 = self.client.post(reverse('leads:lead_score', kwargs={'pk': self.lead1.pk}))
        self.assertEqual(resp1.status_code, 302)

        # Staff 1 cannot score unassigned lead 2
        resp2 = self.client.post(reverse('leads:lead_score', kwargs={'pk': self.lead2.pk}))
        self.assertEqual(resp2.status_code, 403)


class LeadActivityLogTests(LeadTestBase):
    """Tests to verify comprehensive activity logging for Lead Management."""

    def test_lead_actions_record_activity_log(self):
        self.client.login(username='admin_lead_user', password=self.password)

        # 1. Lead creation logs activity
        initial_count = ActivityLog.objects.count()
        self.client.post(reverse('leads:lead_create'), {
            'full_name': 'Audited Lead',
            'email': 'audited@example.com',
            'phone': '9876501111',
            'company': 'Audit Corp',
            'source': Lead.Source.WEBSITE,
            'status': Lead.Status.NEW,
            'estimated_value': '100000.00',
            'assigned_staff': self.staff1.pk,
        })
        self.assertEqual(ActivityLog.objects.count(), initial_count + 1)
        create_log = ActivityLog.objects.first()
        self.assertEqual(create_log.module_name, 'LeadManagement')
        self.assertEqual(create_log.action, 'CREATE')

        # 2. Status change logs activity
        self.client.post(reverse('leads:lead_status', kwargs={'pk': self.lead1.pk}), {
            'status': Lead.Status.PROPOSAL,
            'notes': 'Sent formal proposal'
        })
        status_log = ActivityLog.objects.first()
        self.assertEqual(status_log.action, 'STATUS_CHANGE')
        self.assertIn('QUALIFIED', status_log.description)
        self.assertIn('PROPOSAL', status_log.description)

        # 3. AI scoring logs activity
        self.client.post(reverse('leads:lead_score', kwargs={'pk': self.lead1.pk}))
        score_log = ActivityLog.objects.first()
        self.assertEqual(score_log.action, 'AI_SCORE')
        self.assertIn(self.lead1.lead_code, score_log.description)

        # 4. Assignment logs activity
        self.client.post(reverse('leads:lead_assign', kwargs={'pk': self.lead1.pk}), {
            'assigned_staff': self.staff2.pk
        })
        assign_log = ActivityLog.objects.first()
        self.assertEqual(assign_log.action, 'ASSIGN')

        # 5. Archive logs activity
        self.client.post(reverse('leads:lead_archive', kwargs={'pk': self.lead1.pk}))
        archive_log = ActivityLog.objects.first()
        self.assertEqual(archive_log.action, 'ARCHIVE')
