from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from notifications.models import Notification
from notifications.services import (
    create_notification,
    mark_notification_as_read,
    mark_all_notifications_as_read
)
from notifications.validators import validate_internal_target_url, build_notification_url

User = get_user_model()


class NotificationURLValidatorTests(TestCase):
    def test_valid_internal_target_urls(self):
        valid_urls = [
            None,
            '',
            '/',
            '/dashboard/',
            '/sales/12/',
            '/reminders/?status=PENDING',
            '/interactions/detail/45/',
        ]
        for u in valid_urls:
            # Should not raise
            validate_internal_target_url(u)

    def test_reject_external_and_unsafe_urls(self):
        unsafe_urls = [
            'http://evil.com',
            'https://evil.com/phish',
            '//evil.com/path',
            '///evil.com',
            '\\evil.com/path',
            'javascript:alert(1)',
            'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
            '/path/with\nnewline',
            '/path/with\rcarriage',
            '/path/with\tcontrol',
            'relative/without/leading/slash',
        ]
        for u in unsafe_urls:
            with self.assertRaises(ValidationError, msg=f"URL should be rejected: {u}"):
                validate_internal_target_url(u)

    def test_build_notification_url_allowlist(self):
        url = build_notification_url('sale_detail', 42)
        self.assertEqual(url, reverse('sales:sale_detail', args=[42]))

        url_unknown = build_notification_url('non_existent_key', 1)
        self.assertEqual(url_unknown, '')


class NotificationModelAndServiceTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='user1', email='u1@crm.local', password='password123', role='SALES_STAFF')
        self.user2 = User.objects.create_user(username='user2', email='u2@crm.local', password='password123', role='CUSTOMER')

    def test_create_notification_basic(self):
        notif = create_notification(
            recipient=self.user1,
            title='Test Alert',
            message='You have a new assignment',
            target_url='/reminders/'
        )
        self.assertIsNotNone(notif.pk)
        self.assertFalse(notif.is_read)
        self.assertIsNone(notif.read_at)
        self.assertEqual(notif.target_url, '/reminders/')

    def test_idempotency_key_deduplication(self):
        key = 'unique:alert:user1:2026-09-11'
        n1 = create_notification(
            recipient=self.user1,
            title='Duplicate Alert',
            message='Original message',
            idempotency_key=key
        )
        n2 = create_notification(
            recipient=self.user1,
            title='Duplicate Alert Try 2',
            message='Should not create duplicate',
            idempotency_key=key
        )
        self.assertEqual(n1.pk, n2.pk)
        self.assertEqual(Notification.objects.filter(recipient=self.user1, idempotency_key=key).count(), 1)

    def test_null_idempotency_keys_multiple_allowed(self):
        n1 = create_notification(recipient=self.user1, title='Notice 1', message='msg1', idempotency_key=None)
        n2 = create_notification(recipient=self.user1, title='Notice 2', message='msg2', idempotency_key=None)
        self.assertNotEqual(n1.pk, n2.pk)
        self.assertEqual(Notification.objects.filter(recipient=self.user1).count(), 2)

    def test_duplicate_idempotency_key_raises_integrity_error(self):
        from django.db import IntegrityError
        from django.core.exceptions import ValidationError
        key = 'system:broadcast:1'
        Notification.objects.create(user=self.user1, title='Bcast 1', message='msg', idempotency_key=key)
        with self.assertRaises((IntegrityError, ValidationError)):
            Notification.objects.create(user=self.user2, title='Bcast 2', message='msg', idempotency_key=key)

    def test_mark_as_read_lifecycle(self):
        notif = create_notification(recipient=self.user1, title='Read Test', message='msg')
        self.assertFalse(notif.is_read)

        updated = mark_notification_as_read(notif.pk, user=self.user1)
        self.assertTrue(updated.is_read)
        self.assertIsNotNone(updated.read_at)

        # Idempotent second call
        updated_again = mark_notification_as_read(notif.pk, user=self.user1)
        self.assertEqual(updated.read_at, updated_again.read_at)

    def test_mark_all_as_read(self):
        create_notification(recipient=self.user1, title='N1', message='m1')
        create_notification(recipient=self.user1, title='N2', message='m2')
        create_notification(recipient=self.user2, title='N3', message='m3')

        count = mark_all_notifications_as_read(self.user1)
        self.assertEqual(count, 2)
        self.assertEqual(Notification.objects.filter(recipient=self.user1, is_read=False).count(), 0)
        # user2 remains unread
        self.assertEqual(Notification.objects.filter(recipient=self.user2, is_read=False).count(), 1)


class NotificationViewsAndIsolationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user1 = User.objects.create_user(username='staff1', email='s1@crm.local', password='Password123!', role='SALES_STAFF')
        self.user2 = User.objects.create_user(username='staff2', email='s2@crm.local', password='Password123!', role='SALES_STAFF')

        self.notif1 = create_notification(
            recipient=self.user1,
            title='Staff1 Secret Notice',
            message='Sensitive message 1',
            target_url='/reminders/',
            idempotency_key='secret-key-1'
        )
        self.notif2 = create_notification(
            recipient=self.user2,
            title='Staff2 Secret Notice',
            message='Sensitive message 2',
            target_url='/sales/'
        )

    def test_notification_list_recipient_isolation(self):
        self.client.login(username='staff1', password='Password123!')
        res = self.client.get(reverse('notifications:notification_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Staff1 Secret Notice')
        self.assertNotContains(res, 'Staff2 Secret Notice')
        # Ensure idempotency_key is never leaked in HTML context
        self.assertNotContains(res, 'secret-key-1')

    def test_mark_read_post_only_and_csrf(self):
        self.client.login(username='staff1', password='Password123!')
        url = reverse('notifications:mark_read', args=[self.notif1.pk])

        # GET should be rejected with 405 Method Not Allowed
        res_get = self.client.get(url)
        self.assertEqual(res_get.status_code, 405)

        # POST succeeds
        res_post = self.client.post(url)
        self.assertEqual(res_post.status_code, 302)
        self.notif1.refresh_from_db()
        self.assertTrue(self.notif1.is_read)

    def test_mark_read_user_isolation(self):
        self.client.login(username='staff1', password='Password123!')
        # Attempt to mark user2's notification as read
        url = reverse('notifications:mark_read', args=[self.notif2.pk])
        res = self.client.post(url)
        self.assertEqual(res.status_code, 404)
        self.notif2.refresh_from_db()
        self.assertFalse(self.notif2.is_read)

    def test_mark_all_read_post_only(self):
        self.client.login(username='staff1', password='Password123!')
        url = reverse('notifications:mark_all_read')

        # GET should be rejected with 405
        res_get = self.client.get(url)
        self.assertEqual(res_get.status_code, 405)

        # POST succeeds
        res_post = self.client.post(url)
        self.assertEqual(res_post.status_code, 302)
        self.notif1.refresh_from_db()
        self.assertTrue(self.notif1.is_read)

    def test_follow_notification_post_only_and_redirect(self):
        self.client.login(username='staff1', password='Password123!')
        url = reverse('notifications:follow_notification', args=[self.notif1.pk])

        # GET should be 405
        res_get = self.client.get(url)
        self.assertEqual(res_get.status_code, 405)

        # POST should mark read and redirect to target_url
        res_post = self.client.post(url)
        self.assertRedirects(res_post, '/reminders/')
        self.notif1.refresh_from_db()
        self.assertTrue(self.notif1.is_read)

    def test_unread_count_view(self):
        self.client.login(username='staff1', password='Password123!')
        res = self.client.get(reverse('notifications:unread_count'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get('unread_count'), 1)
