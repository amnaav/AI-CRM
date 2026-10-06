from django.test import TestCase, Client
from django.urls import reverse
from django.core import mail
from django.contrib.auth import get_user_model
from unittest.mock import patch
from accounts.models import CustomUser, CustomerProfile, SalesStaffProfile
from accounts.forms import CustomerRegistrationForm
from activity_logs.models import ActivityLog

User = get_user_model()


class CustomUserModelTests(TestCase):
    """Test CustomUser model creation, roles, and auto-generated fields."""

    def test_create_admin_user(self):
        admin = User.objects.create_user(
            username='admin_test',
            email='admin@example.com',
            password='Password123!',
            role=CustomUser.Role.ADMIN,
            full_name='System Administrator'
        )
        self.assertEqual(admin.role, CustomUser.Role.ADMIN)
        self.assertTrue(admin.is_admin)
        self.assertFalse(admin.is_sales_staff)
        self.assertFalse(admin.is_customer)
        self.assertTrue(admin.user_code.startswith('ADM-'))
        self.assertEqual(admin.status, CustomUser.Status.ACTIVE)
        self.assertTrue(admin.is_account_active)

    def test_create_sales_staff_user(self):
        staff = User.objects.create_user(
            username='staff_test',
            email='staff@example.com',
            password='Password123!',
            role=CustomUser.Role.SALES_STAFF,
            full_name='Sales Representative'
        )
        self.assertEqual(staff.role, CustomUser.Role.SALES_STAFF)
        self.assertFalse(staff.is_admin)
        self.assertTrue(staff.is_sales_staff)
        self.assertFalse(staff.is_customer)
        self.assertTrue(staff.user_code.startswith('STF-'))

    def test_create_customer_user(self):
        customer = User.objects.create_user(
            username='customer_test',
            email='customer@example.com',
            password='Password123!',
            role=CustomUser.Role.CUSTOMER,
            full_name='Valued Client'
        )
        self.assertEqual(customer.role, CustomUser.Role.CUSTOMER)
        self.assertFalse(customer.is_admin)
        self.assertFalse(customer.is_sales_staff)
        self.assertTrue(customer.is_customer)
        self.assertTrue(customer.user_code.startswith('CUST-'))

    def test_email_and_username_uniqueness(self):
        User.objects.create_user(
            username='unique_user',
            email='unique@example.com',
            password='Password123!'
        )
        with self.assertRaises(Exception):
            User.objects.create_user(
                username='unique_user_2',
                email='unique@example.com',
                password='Password123!'
            )


class ProfileRelationshipTests(TestCase):
    """Test 1-to-1 relationships for CustomerProfile and SalesStaffProfile."""

    def test_sales_staff_profile_relationship(self):
        staff_user = User.objects.create_user(
            username='staff_rel',
            email='staff_rel@example.com',
            password='Password123!',
            role=CustomUser.Role.SALES_STAFF
        )
        profile = SalesStaffProfile.objects.create(
            user=staff_user,
            designation='Account Manager',
            department='Enterprise Sales'
        )
        self.assertEqual(staff_user.staff_profile, profile)
        self.assertTrue(profile.staff_code.startswith('STF-'))

    def test_customer_profile_relationship_and_staff_assignment(self):
        staff_user = User.objects.create_user(
            username='staff_rep',
            email='staff_rep@example.com',
            password='Password123!',
            role=CustomUser.Role.SALES_STAFF
        )
        staff_profile = SalesStaffProfile.objects.create(user=staff_user)

        customer_user = User.objects.create_user(
            username='cust_rel',
            email='cust_rel@example.com',
            password='Password123!',
            role=CustomUser.Role.CUSTOMER
        )
        cust_profile = CustomerProfile.objects.create(
            user=customer_user,
            company='Apex Tech Ltd',
            customer_type=CustomerProfile.CustomerType.BUSINESS,
            assigned_staff=staff_profile
        )
        self.assertEqual(customer_user.customer_profile, cust_profile)
        self.assertEqual(cust_profile.assigned_staff, staff_profile)
        self.assertIn(cust_profile, staff_profile.assigned_customers.all())


class PublicRegistrationSecurityTests(TestCase):
    """Test that public registration strictly creates Customer accounts and blocks privilege escalation."""

    def test_public_registration_creates_customer(self):
        form_data = {
            'full_name': 'New Customer',
            'username': 'newcustomer',
            'email': 'newcustomer@example.com',
            'phone': '9876543210',
            'company': 'Tech Corp',
            'customer_type': 'BUSINESS',
            'address': '123 Main Street',
            'city': 'Bengaluru',
            'state': 'Karnataka',
            'postal_code': '560001',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
        }
        form = CustomerRegistrationForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        # Strict security assertions
        self.assertEqual(user.role, CustomUser.Role.CUSTOMER)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.status, CustomUser.Status.ACTIVE)
        self.assertTrue(hasattr(user, 'customer_profile'))
        self.assertEqual(user.customer_profile.company, 'Tech Corp')

    def test_public_registration_cannot_elevate_to_admin(self):
        """Even if malicious POST payload injects role=ADMIN, role must be CUSTOMER."""
        form_data = {
            'full_name': 'Attacker User',
            'username': 'attacker',
            'email': 'attacker@example.com',
            'phone': '9123456780',
            'customer_type': 'INDIVIDUAL',
            'role': 'ADMIN',
            'is_staff': True,
            'is_superuser': True,
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
        }
        form = CustomerRegistrationForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.role, CustomUser.Role.CUSTOMER)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_registration_phone_validation(self):
        form_data = {
            'full_name': 'Bad Phone',
            'username': 'badphone',
            'email': 'badphone@example.com',
            'phone': '123',  # Too short
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
        }
        form = CustomerRegistrationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)


class AuthenticationAndStatusTests(TestCase):
    """Test login, logout, and rejection of locked/inactive accounts."""

    def setUp(self):
        self.client = Client()
        self.password = 'StrongAuthPassword123!'
        self.active_user = User.objects.create_user(
            username='active_user',
            email='active@example.com',
            password=self.password,
            role=CustomUser.Role.CUSTOMER,
            status=CustomUser.Status.ACTIVE
        )
        CustomerProfile.objects.create(user=self.active_user)

        self.inactive_user = User.objects.create_user(
            username='inactive_user',
            email='inactive@example.com',
            password=self.password,
            role=CustomUser.Role.CUSTOMER,
            status=CustomUser.Status.INACTIVE
        )
        CustomerProfile.objects.create(user=self.inactive_user)

        self.locked_user = User.objects.create_user(
            username='locked_user',
            email='locked@example.com',
            password=self.password,
            role=CustomUser.Role.CUSTOMER,
            status=CustomUser.Status.LOCKED
        )
        CustomerProfile.objects.create(user=self.locked_user)

    def test_successful_login_with_username(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'active_user',
            'password': self.password,
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('dashboard:customer_dashboard'))

    def test_successful_login_with_email(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'active@example.com',
            'password': self.password,
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('dashboard:customer_dashboard'))

    def test_login_rejection_for_inactive_account(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'inactive_user',
            'password': self.password,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'inactive')

    def test_login_rejection_for_locked_account(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'locked_user',
            'password': self.password,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'locked')

    def test_successful_logout(self):
        self.client.login(username='active_user', password=self.password)
        response = self.client.post(reverse('accounts:logout'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('accounts:login'))


class RoleBasedAccessControlTests(TestCase):
    """Test dashboard redirection and role authorization restrictions."""

    def setUp(self):
        self.client = Client()
        self.password = 'SecurePassword123!'

        self.admin = User.objects.create_user(
            username='admin_rbac',
            email='admin_rbac@example.com',
            password=self.password,
            role=CustomUser.Role.ADMIN
        )

        self.staff_user = User.objects.create_user(
            username='staff_rbac',
            email='staff_rbac@example.com',
            password=self.password,
            role=CustomUser.Role.SALES_STAFF
        )
        self.staff_profile = SalesStaffProfile.objects.create(user=self.staff_user)

        self.customer_user = User.objects.create_user(
            username='cust_rbac',
            email='cust_rbac@example.com',
            password=self.password,
            role=CustomUser.Role.CUSTOMER
        )
        self.customer_profile = CustomerProfile.objects.create(user=self.customer_user)

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse('dashboard:index'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('accounts:login')))

    def test_admin_dashboard_redirection_and_access(self):
        self.client.login(username='admin_rbac', password=self.password)
        # Root dashboard redirects to admin dashboard
        response = self.client.get(reverse('dashboard:index'))
        self.assertRedirects(response, reverse('dashboard:admin_dashboard'))

        # Direct access succeeds
        dash_res = self.client.get(reverse('dashboard:admin_dashboard'))
        self.assertEqual(dash_res.status_code, 200)

    def test_sales_staff_dashboard_redirection_and_access(self):
        self.client.login(username='staff_rbac', password=self.password)
        response = self.client.get(reverse('dashboard:index'))
        self.assertRedirects(response, reverse('dashboard:staff_dashboard'))

        dash_res = self.client.get(reverse('dashboard:staff_dashboard'))
        self.assertEqual(dash_res.status_code, 200)

    def test_customer_dashboard_redirection_and_access(self):
        self.client.login(username='cust_rbac', password=self.password)
        response = self.client.get(reverse('dashboard:index'))
        self.assertRedirects(response, reverse('dashboard:customer_dashboard'))

        dash_res = self.client.get(reverse('dashboard:customer_dashboard'))
        self.assertEqual(dash_res.status_code, 200)

    def test_customer_cannot_access_admin_dashboard(self):
        self.client.login(username='cust_rbac', password=self.password)
        response = self.client.get(reverse('dashboard:admin_dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_customer_cannot_access_staff_dashboard(self):
        self.client.login(username='cust_rbac', password=self.password)
        response = self.client.get(reverse('dashboard:staff_dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_access_admin_dashboard(self):
        self.client.login(username='staff_rbac', password=self.password)
        response = self.client.get(reverse('dashboard:admin_dashboard'))
        self.assertEqual(response.status_code, 403)


class PasswordWorkflowFoundationTests(TestCase):
    """Test password change and password reset workflow endpoints."""

    def setUp(self):
        self.client = Client()
        self.password = 'InitialPassword123!'
        self.user = User.objects.create_user(
            username='pwd_user',
            email='pwd_user@example.com',
            password=self.password,
            role=CustomUser.Role.CUSTOMER
        )
        CustomerProfile.objects.create(user=self.user)

    def test_password_change_authenticated(self):
        self.client.login(username='pwd_user', password=self.password)
        response = self.client.post(reverse('accounts:password_change'), {
            'old_password': self.password,
            'new_password1': 'UpdatedPassword456!',
            'new_password2': 'UpdatedPassword456!',
        })
        self.assertEqual(response.status_code, 302)
        # Verify user can log in with new password
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('UpdatedPassword456!'))

    def test_password_reset_request_sends_email(self):
        response = self.client.post(reverse('accounts:password_reset'), {
            'email': 'pwd_user@example.com'
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Password Reset Request', mail.outbox[0].subject)


class CustomerManagementTests(TestCase):
    """Phase 3A Customer Management module automated tests."""

    def setUp(self):
        self.client = Client()
        self.password = 'ComplexPass123!'

        # Admin user
        self.admin = User.objects.create_user(
            username='admin_cm',
            email='admin_cm@example.com',
            password=self.password,
            role=CustomUser.Role.ADMIN,
            full_name='Admin Manager'
        )

        # Sales Staff 1 & 2
        self.staff_user1 = User.objects.create_user(
            username='staff_cm1',
            email='staff_cm1@example.com',
            password=self.password,
            role=CustomUser.Role.SALES_STAFF,
            full_name='Sales Staff Alpha'
        )
        self.staff1 = SalesStaffProfile.objects.create(user=self.staff_user1, designation='Senior Executive')

        self.staff_user2 = User.objects.create_user(
            username='staff_cm2',
            email='staff_cm2@example.com',
            password=self.password,
            role=CustomUser.Role.SALES_STAFF,
            full_name='Sales Staff Beta'
        )
        self.staff2 = SalesStaffProfile.objects.create(user=self.staff_user2, designation='Junior Executive')

        # Customer 1 (Assigned to Staff 1)
        self.cust_user1 = User.objects.create_user(
            username='cust_cm1',
            email='cust_cm1@example.com',
            password=self.password,
            role=CustomUser.Role.CUSTOMER,
            full_name='Customer Assigned Alpha',
            phone='9876500001'
        )
        self.cust1 = CustomerProfile.objects.create(
            user=self.cust_user1,
            company='Alpha Corp',
            customer_type='BUSINESS',
            assigned_staff=self.staff1
        )

        # Customer 2 (Assigned to Staff 2)
        self.cust_user2 = User.objects.create_user(
            username='cust_cm2',
            email='cust_cm2@example.com',
            password=self.password,
            role=CustomUser.Role.CUSTOMER,
            full_name='Customer Assigned Beta',
            phone='9876500002'
        )
        self.cust2 = CustomerProfile.objects.create(
            user=self.cust_user2,
            company='Beta Corp',
            customer_type='INDIVIDUAL',
            assigned_staff=self.staff2
        )

    def test_admin_can_list_all_customers(self):
        self.client.login(username='admin_cm', password=self.password)
        response = self.client.get(reverse('accounts:customer_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Customer Assigned Alpha')
        self.assertContains(response, 'Customer Assigned Beta')

    def test_sales_staff_sees_only_assigned_customers_in_list(self):
        self.client.login(username='staff_cm1', password=self.password)
        response = self.client.get(reverse('accounts:customer_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Customer Assigned Alpha')
        self.assertNotContains(response, 'Customer Assigned Beta')

    def test_customer_user_cannot_access_customer_list(self):
        self.client.login(username='cust_cm1', password=self.password)
        response = self.client.get(reverse('accounts:customer_list'))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_access_customer_list(self):
        response = self.client.get(reverse('accounts:customer_list'))
        self.assertEqual(response.status_code, 302)

    def test_admin_can_create_customer_with_linked_account(self):
        self.client.login(username='admin_cm', password=self.password)
        response = self.client.post(reverse('accounts:customer_create'), {
            'full_name': 'New Direct Customer',
            'username': 'direct_cust',
            'email': 'direct@example.com',
            'phone': '9876543299',
            'company': 'Global Tech',
            'customer_type': 'BUSINESS',
            'assigned_staff': self.staff1.pk,
            'address': '456 Tech Park',
            'city': 'Hyderabad',
            'state': 'Telangana',
            'postal_code': '500081',
            'notes': 'High potential enterprise lead',
            'password': 'SecureClientPassword123!',
            'status': 'ACTIVE',
        })
        self.assertEqual(response.status_code, 302)
        created_user = User.objects.get(username='direct_cust')
        self.assertEqual(created_user.role, CustomUser.Role.CUSTOMER)
        self.assertEqual(created_user.email, 'direct@example.com')
        self.assertTrue(hasattr(created_user, 'customer_profile'))
        self.assertEqual(created_user.customer_profile.assigned_staff, self.staff1)

    def test_create_customer_duplicate_validation(self):
        self.client.login(username='admin_cm', password=self.password)
        # Duplicate email test
        response = self.client.post(reverse('accounts:customer_create'), {
            'full_name': 'Duplicate Person',
            'username': 'dup_user_unique',
            'email': 'cust_cm1@example.com',  # Duplicate!
            'phone': '9998887776',
            'password': 'Password123!',
            'status': 'ACTIVE',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'email', 'An account with this email address already exists.')

    def test_sales_staff_can_view_assigned_customer_detail(self):
        self.client.login(username='staff_cm1', password=self.password)
        response = self.client.get(reverse('accounts:customer_detail', kwargs={'pk': self.cust1.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Customer Assigned Alpha')

    def test_sales_staff_cannot_view_unassigned_customer_detail(self):
        self.client.login(username='staff_cm1', password=self.password)
        # Customer 2 is assigned to staff 2, so staff 1 must receive 403 Forbidden
        response = self.client.get(reverse('accounts:customer_detail', kwargs={'pk': self.cust2.pk}))
        self.assertEqual(response.status_code, 403)

    def test_sales_staff_can_edit_permitted_fields_on_assigned_customer(self):
        self.client.login(username='staff_cm1', password=self.password)
        response = self.client.post(reverse('accounts:customer_edit', kwargs={'pk': self.cust1.pk}), {
            'full_name': 'Alpha Updated Name',
            'phone': '9876500001',
            'company': 'Alpha Corp Revamped',
            'address': '789 Updated Blvd',
            'city': 'Pune',
            'state': 'Maharashtra',
            'postal_code': '411001',
            'notes': 'Updated notes from staff 1'
        })
        self.assertEqual(response.status_code, 302)
        self.cust1.refresh_from_db()
        self.cust1.user.refresh_from_db()
        self.assertEqual(self.cust1.company, 'Alpha Corp Revamped')
        self.assertEqual(self.cust1.user.full_name, 'Alpha Updated Name')

    def test_sales_staff_cannot_edit_unassigned_customer(self):
        self.client.login(username='staff_cm1', password=self.password)
        response = self.client.post(reverse('accounts:customer_edit', kwargs={'pk': self.cust2.pk}), {
            'full_name': 'Hacked Name',
            'phone': '9876500002',
        })
        self.assertEqual(response.status_code, 403)

    def test_admin_can_toggle_customer_status(self):
        self.client.login(username='admin_cm', password=self.password)
        # Lock account
        response = self.client.post(reverse('accounts:customer_status', kwargs={'pk': self.cust1.pk}), {
            'status': 'LOCKED'
        })
        self.assertEqual(response.status_code, 302)
        self.cust1.user.refresh_from_db()
        self.assertEqual(self.cust1.user.status, CustomUser.Status.LOCKED)

    def test_admin_can_reassign_customer_to_different_staff(self):
        self.client.login(username='admin_cm', password=self.password)
        self.assertEqual(self.cust1.assigned_staff, self.staff1)
        response = self.client.post(reverse('accounts:customer_assign', kwargs={'pk': self.cust1.pk}), {
            'assigned_staff': self.staff2.pk
        })
        self.assertEqual(response.status_code, 302)
        self.cust1.refresh_from_db()
        self.assertEqual(self.cust1.assigned_staff, self.staff2)

    def test_customer_deletion_protection_with_sales_history(self):
        from sales.models import Sale
        # Create a sale record linked to customer
        Sale.objects.create(
            customer=self.cust1,
            sales_staff=self.staff1,
            subtotal=5000.00,
            net_amount=5000.00
        )
        self.client.login(username='admin_cm', password=self.password)
        response = self.client.post(reverse('accounts:customer_delete', kwargs={'pk': self.cust1.pk}))
        self.assertEqual(response.status_code, 302)

        # Must not be hard-deleted; must be soft-deactivated
        self.assertTrue(CustomerProfile.objects.filter(pk=self.cust1.pk).exists())
        self.cust1.refresh_from_db()
        self.cust1.user.refresh_from_db()
        self.assertFalse(self.cust1.is_active)
        self.assertEqual(self.cust1.user.status, CustomUser.Status.INACTIVE)

    def test_customer_deletion_soft_deactivates_account(self):
        self.client.login(username='admin_cm', password=self.password)
        cust2_pk = self.cust2.pk
        response = self.client.post(reverse('accounts:customer_delete', kwargs={'pk': cust2_pk}))
        self.assertEqual(response.status_code, 302)
        # Account must be soft-deactivated, preserving records
        cust = CustomerProfile.objects.get(pk=cust2_pk)
        self.assertFalse(cust.is_active)
        self.assertFalse(cust.user.is_active)
        self.assertEqual(cust.user.status, CustomUser.Status.INACTIVE)

    def test_customer_list_single_add_button_populated_and_empty(self):
        self.client.login(username='admin_cm', password=self.password)
        # Populated state:
        response = self.client.get(reverse('accounts:customer_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="header-add-customer-btn"')
        self.assertNotContains(response, 'id="top-add-customer-btn"')
        self.assertNotContains(response, 'Add First Customer')

        # Accessible tooltips & labels for View, Edit and Status
        self.assertContains(response, 'title="View Details"')
        self.assertContains(response, 'title="Edit Customer"')
        self.assertContains(response, 'title="Deactivate Customer"')
        self.assertContains(response, 'data-bs-toggle="tooltip"')

        # Empty state:
        CustomerProfile.objects.all().delete()
        response_empty = self.client.get(reverse('accounts:customer_list'))
        self.assertEqual(response_empty.status_code, 200)
        self.assertContains(response_empty, 'id="header-add-customer-btn"')
        self.assertContains(response_empty, 'Add First Customer')

    def test_customer_status_toggle_post_only(self):
        self.client.login(username='admin_cm', password=self.password)
        # GET request must return 405 Method Not Allowed
        response = self.client.get(reverse('accounts:customer_status', kwargs={'pk': self.cust1.pk}))
        self.assertEqual(response.status_code, 405)
        self.cust1.user.refresh_from_db()
        self.assertEqual(self.cust1.user.status, CustomUser.Status.ACTIVE)

    def test_customer_status_csrf_protected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username='admin_cm', password=self.password)
        # POST without CSRF token must return 403 Forbidden
        response = csrf_client.post(reverse('accounts:customer_status', kwargs={'pk': self.cust1.pk}), {
            'status': 'INACTIVE'
        })
        self.assertEqual(response.status_code, 403)

    def test_customer_status_safe_and_unsafe_next_redirect(self):
        self.client.login(username='admin_cm', password=self.password)
        # Safe next redirect within site
        response = self.client.post(reverse('accounts:customer_status', kwargs={'pk': self.cust1.pk}), {
            'status': 'INACTIVE',
            'next': '/accounts/customers/?page=1'
        })
        self.assertRedirects(response, '/accounts/customers/?page=1')

        # Unsafe external redirect must be rejected and fallback to customer_list
        response_unsafe = self.client.post(reverse('accounts:customer_status', kwargs={'pk': self.cust1.pk}), {
            'status': 'ACTIVE',
            'next': 'https://malicious-site.com/steal'
        })
        self.assertRedirects(response_unsafe, reverse('accounts:customer_list'))

    def test_customer_status_synchronizes_is_active(self):
        self.client.login(username='admin_cm', password=self.password)
        # Deactivate
        self.client.post(reverse('accounts:customer_status', kwargs={'pk': self.cust1.pk}), {
            'status': 'INACTIVE'
        })
        self.cust1.refresh_from_db()
        self.cust1.user.refresh_from_db()
        self.assertEqual(self.cust1.user.status, CustomUser.Status.INACTIVE)
        self.assertFalse(self.cust1.user.is_active)
        self.assertFalse(self.cust1.is_active)

        # Reactivate
        self.client.post(reverse('accounts:customer_status', kwargs={'pk': self.cust1.pk}), {
            'status': 'ACTIVE'
        })
        self.cust1.refresh_from_db()
        self.cust1.user.refresh_from_db()
        self.assertEqual(self.cust1.user.status, CustomUser.Status.ACTIVE)
        self.assertTrue(self.cust1.user.is_active)
        self.assertTrue(self.cust1.is_active)

    def test_admin_cannot_deactivate_own_account(self):
        # Admin cannot deactivate own customer account
        admin_cust = CustomerProfile.objects.create(user=self.admin, customer_code='CUST-ADM1')
        self.client.login(username='admin_cm', password=self.password)
        response = self.client.post(reverse('accounts:customer_status', kwargs={'pk': admin_cust.pk}), {
            'status': 'INACTIVE'
        })
        self.assertRedirects(response, reverse('accounts:customer_list'))
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.status, CustomUser.Status.ACTIVE)
        self.assertTrue(self.admin.is_active)

    def test_status_change_creates_activity_log(self):
        self.client.login(username='admin_cm', password=self.password)
        initial_count = ActivityLog.objects.count()
        self.client.post(reverse('accounts:customer_status', kwargs={'pk': self.cust1.pk}), {
            'status': 'INACTIVE'
        })
        self.assertEqual(ActivityLog.objects.count(), initial_count + 1)
        log = ActivityLog.objects.first()
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.module_name, 'CustomerManagement')
        self.assertIn('admin_cm', log.description)
        self.assertIn(self.cust1.customer_code, log.description)
        self.assertIn('ACTIVE', log.description)
        self.assertIn('INACTIVE', log.description)

    def test_customer_create_form_atomic_creation(self):
        from accounts.management_forms import AdminCustomerCreateForm
        form_data = {
            'full_name': 'Atomic Customer',
            'username': 'atomic_cust',
            'email': 'atomic_cust@example.com',
            'phone': '9876543210',
            'customer_type': 'INDIVIDUAL',
            'password': 'SecurePassword123!',
            'status': 'ACTIVE',
        }
        form = AdminCustomerCreateForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)

        # Simulate exception during profile creation
        with patch.object(CustomerProfile.objects, 'create', side_effect=RuntimeError("Profile DB Error")):
            with self.assertRaises(RuntimeError):
                form.save()

        # User creation must have rolled back
        self.assertFalse(User.objects.filter(username='atomic_cust').exists())


class SalesStaffManagementTests(TestCase):
    """Phase 3A Sales Staff Management module automated tests."""

    def setUp(self):
        self.client = Client()
        self.password = 'ComplexPass123!'

        self.admin = User.objects.create_user(
            username='admin_sm',
            email='admin_sm@example.com',
            password=self.password,
            role=CustomUser.Role.ADMIN,
            full_name='Super Admin'
        )

        self.staff_user = User.objects.create_user(
            username='staff_sm',
            email='staff_sm@example.com',
            password=self.password,
            role=CustomUser.Role.SALES_STAFF,
            full_name='Staff Member One'
        )
        self.staff = SalesStaffProfile.objects.create(
            user=self.staff_user,
            designation='Account Executive',
            department='Enterprise Direct',
            assigned_target=150000.00
        )

        self.staff_user2 = User.objects.create_user(
            username='staff_sm2',
            email='staff_sm2@example.com',
            password=self.password,
            role=CustomUser.Role.SALES_STAFF,
            full_name='Staff Member Two'
        )
        self.staff2 = SalesStaffProfile.objects.create(
            user=self.staff_user2,
            designation='Field Rep'
        )

        self.customer_user = User.objects.create_user(
            username='cust_sm',
            email='cust_sm@example.com',
            password=self.password,
            role=CustomUser.Role.CUSTOMER
        )
        self.customer = CustomerProfile.objects.create(user=self.customer_user, assigned_staff=self.staff)

    def test_admin_can_list_staff(self):
        self.client.login(username='admin_sm', password=self.password)
        response = self.client.get(reverse('accounts:staff_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Staff Member One')
        self.assertContains(response, 'Staff Member Two')

    def test_non_admin_cannot_access_staff_list(self):
        # Sales staff cannot view complete staff directory
        self.client.login(username='staff_sm', password=self.password)
        response = self.client.get(reverse('accounts:staff_list'))
        self.assertEqual(response.status_code, 403)

        # Customer cannot view staff directory
        self.client.login(username='cust_sm', password=self.password)
        response = self.client.get(reverse('accounts:staff_list'))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_staff_with_linked_account(self):
        self.client.login(username='admin_sm', password=self.password)
        response = self.client.post(reverse('accounts:staff_create'), {
            'full_name': 'New Sales Rep',
            'username': 'new_rep',
            'email': 'new_rep@example.com',
            'phone': '9876512345',
            'designation': 'Territory Manager',
            'department': 'Regional Sales',
            'assigned_target': '250000.00',
            'joining_date': '2026-01-15',
            'address': '101 Corporate Way',
            'city': 'Mumbai',
            'state': 'Maharashtra',
            'postal_code': '400001',
            'password': 'SecureStaffPassword123!',
            'status': 'ACTIVE',
        })
        self.assertEqual(response.status_code, 302)
        new_user = User.objects.get(username='new_rep')
        self.assertEqual(new_user.role, CustomUser.Role.SALES_STAFF)
        self.assertEqual(new_user.staff_profile.designation, 'Territory Manager')
        self.assertEqual(new_user.staff_profile.assigned_target, 250000.00)

    def test_create_staff_duplicate_validation(self):
        self.client.login(username='admin_sm', password=self.password)
        response = self.client.post(reverse('accounts:staff_create'), {
            'full_name': 'Duplicate Rep',
            'username': 'staff_sm',  # Duplicate!
            'email': 'another@example.com',
            'phone': '9876500000',
            'designation': 'Rep',
            'department': 'Sales',
            'assigned_target': '100000.00',
            'password': 'Password123!',
            'status': 'ACTIVE',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'username', 'A user with this username already exists.')

    def test_admin_can_edit_staff_details_and_target(self):
        self.client.login(username='admin_sm', password=self.password)
        response = self.client.post(reverse('accounts:staff_edit', kwargs={'pk': self.staff.pk}), {
            'full_name': 'Staff Member One Promoted',
            'email': 'staff_sm@example.com',
            'phone': '9876543210',
            'designation': 'VP of Sales',
            'department': 'Executive Sales',
            'assigned_target': '500000.00',
            'joining_date': '2025-06-01',
            'address': 'New Suite 500',
            'city': 'Delhi',
            'state': 'Delhi',
            'postal_code': '110001',
            'status': 'ACTIVE',
        })
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.staff.user.refresh_from_db()
        self.assertEqual(self.staff.designation, 'VP of Sales')
        self.assertEqual(self.staff.assigned_target, 500000.00)
        self.assertEqual(self.staff.user.full_name, 'Staff Member One Promoted')

    def test_admin_can_toggle_staff_status(self):
        self.client.login(username='admin_sm', password=self.password)
        response = self.client.post(reverse('accounts:staff_status', kwargs={'pk': self.staff.pk}), {
            'status': 'INACTIVE'
        })
        self.assertEqual(response.status_code, 302)
        self.staff.user.refresh_from_db()
        self.assertEqual(self.staff.user.status, CustomUser.Status.INACTIVE)
        self.assertFalse(self.staff.user.is_active)

    def test_staff_deletion_protection_with_assigned_customers(self):
        self.client.login(username='admin_sm', password=self.password)
        # Staff has self.customer assigned
        self.assertTrue(self.staff.assigned_customers.exists())
        response = self.client.post(reverse('accounts:staff_delete', kwargs={'pk': self.staff.pk}))
        self.assertEqual(response.status_code, 302)

        # Must NOT be deleted; must be deactivated
        self.assertTrue(SalesStaffProfile.objects.filter(pk=self.staff.pk).exists())
        self.staff.user.refresh_from_db()
        self.assertEqual(self.staff.user.status, CustomUser.Status.INACTIVE)
        self.assertFalse(self.staff.user.is_active)

    def test_staff_deletion_soft_deactivates_account(self):
        self.client.login(username='admin_sm', password=self.password)
        staff2_pk = self.staff2.pk
        response = self.client.post(reverse('accounts:staff_delete', kwargs={'pk': staff2_pk}))
        self.assertEqual(response.status_code, 302)
        # Account must be soft-deactivated, preserving records
        staff = SalesStaffProfile.objects.get(pk=staff2_pk)
        self.assertFalse(staff.user.is_active)
        self.assertEqual(staff.user.status, CustomUser.Status.INACTIVE)

    def test_staff_list_single_add_button_populated_and_empty(self):
        self.client.login(username='admin_sm', password=self.password)
        # Populated state:
        response = self.client.get(reverse('accounts:staff_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="header-add-staff-btn"')
        self.assertNotContains(response, 'id="top-add-staff-btn"')
        self.assertNotContains(response, 'Add First Staff Member')

        # Accessible tooltips & labels for View, Edit and Status
        self.assertContains(response, 'title="View Profile & Performance"')
        self.assertContains(response, 'title="Edit Staff Details"')
        self.assertContains(response, 'title="Deactivate Sales Staff"')
        self.assertContains(response, 'data-bs-toggle="tooltip"')

        # Empty state:
        SalesStaffProfile.objects.all().delete()
        response_empty = self.client.get(reverse('accounts:staff_list'))
        self.assertEqual(response_empty.status_code, 200)
        self.assertContains(response_empty, 'id="header-add-staff-btn"')
        self.assertContains(response_empty, 'Add First Staff Member')

    def test_staff_status_toggle_post_only(self):
        self.client.login(username='admin_sm', password=self.password)
        # GET request must return 405 Method Not Allowed
        response = self.client.get(reverse('accounts:staff_status', kwargs={'pk': self.staff.pk}))
        self.assertEqual(response.status_code, 405)
        self.staff.user.refresh_from_db()
        self.assertEqual(self.staff.user.status, CustomUser.Status.ACTIVE)

    def test_staff_status_csrf_protected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username='admin_sm', password=self.password)
        # POST without CSRF token must return 403 Forbidden
        response = csrf_client.post(reverse('accounts:staff_status', kwargs={'pk': self.staff.pk}), {
            'status': 'INACTIVE'
        })
        self.assertEqual(response.status_code, 403)

    def test_staff_status_safe_and_unsafe_next_redirect(self):
        self.client.login(username='admin_sm', password=self.password)
        # Safe next redirect within site
        response = self.client.post(reverse('accounts:staff_status', kwargs={'pk': self.staff.pk}), {
            'status': 'INACTIVE',
            'next': '/accounts/staff/?page=1'
        })
        self.assertRedirects(response, '/accounts/staff/?page=1')

        # Unsafe external redirect must be rejected and fallback to staff_list
        response_unsafe = self.client.post(reverse('accounts:staff_status', kwargs={'pk': self.staff.pk}), {
            'status': 'ACTIVE',
            'next': 'https://malicious-site.com/steal'
        })
        self.assertRedirects(response_unsafe, reverse('accounts:staff_list'))

    def test_staff_status_synchronizes_is_active(self):
        self.client.login(username='admin_sm', password=self.password)
        # Deactivate
        self.client.post(reverse('accounts:staff_status', kwargs={'pk': self.staff.pk}), {
            'status': 'INACTIVE'
        })
        self.staff.refresh_from_db()
        self.staff.user.refresh_from_db()
        self.assertEqual(self.staff.user.status, CustomUser.Status.INACTIVE)
        self.assertFalse(self.staff.user.is_active)

        # Reactivate
        self.client.post(reverse('accounts:staff_status', kwargs={'pk': self.staff.pk}), {
            'status': 'ACTIVE'
        })
        self.staff.refresh_from_db()
        self.staff.user.refresh_from_db()
        self.assertEqual(self.staff.user.status, CustomUser.Status.ACTIVE)
        self.assertTrue(self.staff.user.is_active)

    def test_admin_cannot_deactivate_own_staff_account(self):
        # Admin with linked staff profile cannot deactivate self
        admin_staff = SalesStaffProfile.objects.create(user=self.admin, staff_code='STAFF-ADM1')
        self.client.login(username='admin_sm', password=self.password)
        response = self.client.post(reverse('accounts:staff_status', kwargs={'pk': admin_staff.pk}), {
            'status': 'INACTIVE'
        })
        self.assertRedirects(response, reverse('accounts:staff_list'))
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.status, CustomUser.Status.ACTIVE)
        self.assertTrue(self.admin.is_active)

    def test_staff_status_change_creates_activity_log(self):
        self.client.login(username='admin_sm', password=self.password)
        initial_count = ActivityLog.objects.count()
        self.client.post(reverse('accounts:staff_status', kwargs={'pk': self.staff.pk}), {
            'status': 'INACTIVE'
        })
        self.assertEqual(ActivityLog.objects.count(), initial_count + 1)
        log = ActivityLog.objects.first()
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.module_name, 'StaffManagement')
        self.assertIn('admin_sm', log.description)
        self.assertIn(self.staff.staff_code, log.description)
        self.assertIn('ACTIVE', log.description)
        self.assertIn('INACTIVE', log.description)

    def test_staff_create_form_atomic_creation(self):
        from accounts.management_forms import AdminStaffCreateForm
        form_data = {
            'full_name': 'Atomic Staff',
            'username': 'atomic_staff',
            'email': 'atomic_staff@example.com',
            'phone': '9876543211',
            'designation': 'Sales Executive',
            'department': 'Sales',
            'assigned_target': '100000.00',
            'password': 'SecurePassword123!',
            'status': 'ACTIVE',
        }
        form = AdminStaffCreateForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)

        # Simulate exception during profile creation
        with patch.object(SalesStaffProfile.objects, 'create', side_effect=RuntimeError("Profile DB Error")):
            with self.assertRaises(RuntimeError):
                form.save()

        # User creation must have rolled back
        self.assertFalse(User.objects.filter(username='atomic_staff').exists())

    def test_staff_can_view_own_detail_but_not_colleague(self):
        # Staff 1 viewing own profile succeeds
        self.client.login(username='staff_sm', password=self.password)
        response = self.client.get(reverse('accounts:staff_detail', kwargs={'pk': self.staff.pk}))
        self.assertEqual(response.status_code, 200)

        # Staff 1 viewing Staff 2 profile gets 403 Forbidden
        response2 = self.client.get(reverse('accounts:staff_detail', kwargs={'pk': self.staff2.pk}))
        self.assertEqual(response2.status_code, 403)


