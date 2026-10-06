from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from activity_logs.models import ActivityLog
from activity_logs.services import log_activity
from activity_logs.sanitizer import sanitize_metadata, SENSITIVE_MASK

User = get_user_model()


class ActivityLogSanitizerTests(TestCase):
    def test_sanitize_sensitive_keys(self):
        data = {
            'username': 'john_doe',
            'password': 'SuperSecretPassword123!',
            'api_key': 'abc123xyz',
            'auth_token': 'token-999',
            'credit_card': '4111222233334444',
            'session_cookie': 'sessionid=abc',
            'idempotency_key': 'idemp_key_123',
            'nested': {
                'access_token': 'secret-nested-token',
                'normal_field': 'hello'
            },
            'list_data': [
                {'pin': '1234', 'note': 'test'},
                'plain_string'
            ]
        }
        sanitized = sanitize_metadata(data)
        self.assertEqual(sanitized['username'], 'john_doe')
        self.assertEqual(sanitized['password'], SENSITIVE_MASK)
        self.assertEqual(sanitized['api_key'], SENSITIVE_MASK)
        self.assertEqual(sanitized['auth_token'], SENSITIVE_MASK)
        self.assertEqual(sanitized['credit_card'], SENSITIVE_MASK)
        self.assertEqual(sanitized['session_cookie'], SENSITIVE_MASK)
        self.assertEqual(sanitized['idempotency_key'], SENSITIVE_MASK)
        self.assertEqual(sanitized['nested']['access_token'], SENSITIVE_MASK)
        self.assertEqual(sanitized['nested']['normal_field'], 'hello')
        self.assertEqual(sanitized['list_data'][0]['pin'], SENSITIVE_MASK)
        self.assertEqual(sanitized['list_data'][0]['note'], 'test')
        self.assertEqual(sanitized['list_data'][1], 'plain_string')

    def test_sanitize_patterns_in_strings(self):
        data = {
            'header': 'Bearer my-secret-jwt-token-12345',
            'card_mention': 'Charged to 4111-2222-3333-4444 successfully',
            'url_with_secret': 'https://example.com/api?token=secret123&other=val',
        }
        sanitized = sanitize_metadata(data)
        self.assertIn(SENSITIVE_MASK, sanitized['header'])
        self.assertNotIn('my-secret-jwt-token-12345', sanitized['header'])
        self.assertIn(SENSITIVE_MASK, sanitized['card_mention'])
        self.assertNotIn('4111-2222-3333-4444', sanitized['card_mention'])
        self.assertIn(SENSITIVE_MASK, sanitized['url_with_secret'])
        self.assertNotIn('token=secret123', sanitized['url_with_secret'])

    def test_non_dict_metadata(self):
        self.assertEqual(sanitize_metadata(None), {})
        self.assertEqual(sanitize_metadata('string'), {})


class ActivityLogImmutabilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='admin_test',
            email='admin@example.com',
            password='password123',
            role='ADMIN'
        )

    def test_create_via_service_persists_sanitized_metadata(self):
        log = log_activity(
            user=self.user,
            action=ActivityLog.ActionChoices.CREATE,
            entity_type='CustomerProfile',
            entity_id=1,
            description='Created new customer profile',
            metadata={'password': 'secret_password', 'customer_name': 'Acme Corp'},
            ip_address='127.0.0.1'
        )
        self.assertIsNotNone(log.pk)
        self.assertEqual(log.metadata['customer_name'], 'Acme Corp')
        self.assertEqual(log.metadata['password'], SENSITIVE_MASK)

    def test_save_existing_raises_permission_denied(self):
        log = log_activity(
            user=self.user,
            action=ActivityLog.ActionChoices.UPDATE,
            entity_type='Sale',
            entity_id=10,
            description='Initial sale log'
        )
        log.description = 'Tampered log description'
        with self.assertRaises(PermissionDenied):
            log.save()

    def test_delete_existing_raises_permission_denied(self):
        log = log_activity(
            user=self.user,
            action=ActivityLog.ActionChoices.DELETE,
            entity_type='Lead',
            entity_id=5,
            description='Delete log'
        )
        with self.assertRaises(PermissionDenied):
            log.delete()

    def test_queryset_update_raises_permission_denied(self):
        log_activity(
            user=self.user,
            action=ActivityLog.ActionChoices.CREATE,
            entity_type='Lead',
            entity_id=1,
            description='Lead log'
        )
        with self.assertRaises(PermissionDenied):
            ActivityLog.objects.filter(entity_type='Lead').update(description='Tampered')

    def test_queryset_delete_raises_permission_denied(self):
        log_activity(
            user=self.user,
            action=ActivityLog.ActionChoices.CREATE,
            entity_type='Lead',
            entity_id=1,
            description='Lead log'
        )
        with self.assertRaises(PermissionDenied):
            ActivityLog.objects.filter(entity_type='Lead').delete()


class ActivityLogViewsAndPermissionsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_user(
            username='admin_user',
            email='admin@crm.local',
            password='Password123!',
            role='ADMIN',
            is_staff=True
        )
        self.staff_user = User.objects.create_user(
            username='staff_user',
            email='staff@crm.local',
            password='Password123!',
            role='SALES_STAFF'
        )
        self.customer_user = User.objects.create_user(
            username='cust_user',
            email='cust@crm.local',
            password='Password123!',
            role='CUSTOMER'
        )
        self.log = log_activity(
            user=self.admin_user,
            action=ActivityLog.ActionChoices.LOGIN,
            entity_type='User',
            entity_id=self.admin_user.id,
            description='Admin logged in',
            metadata={'ip': '127.0.0.1'}
        )

    def test_admin_can_view_log_list_and_detail(self):
        self.client.login(username='admin_user', password='Password123!')
        res_list = self.client.get(reverse('activity_logs:log_list'))
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, 'Admin logged in')

        res_detail = self.client.get(reverse('activity_logs:log_detail', args=[self.log.pk]))
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, 'Admin logged in')

    def test_staff_cannot_view_activity_logs(self):
        self.client.login(username='staff_user', password='Password123!')
        res = self.client.get(reverse('activity_logs:log_list'))
        self.assertEqual(res.status_code, 403)

    def test_customer_cannot_view_activity_logs(self):
        self.client.login(username='cust_user', password='Password123!')
        res = self.client.get(reverse('activity_logs:log_list'))
        self.assertEqual(res.status_code, 403)
