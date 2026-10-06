import urllib.parse
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from django.utils.http import url_has_allowed_host_and_scheme


class NotificationQuerySet(models.QuerySet):
    def _filter_or_exclude(self, negate, args, kwargs):
        new_kwargs = {}
        for k, v in kwargs.items():
            if k == 'recipient':
                new_kwargs['user'] = v
            elif k.startswith('recipient__'):
                new_kwargs['user__' + k[len('recipient__'):]] = v
            else:
                new_kwargs[k] = v
        return super()._filter_or_exclude(negate, args, new_kwargs)


class NotificationManager(models.Manager.from_queryset(NotificationQuerySet)):
    pass


class Notification(models.Model):
    class Type(models.TextChoices):
        INFO = 'INFO', _('Information')
        SUCCESS = 'SUCCESS', _('Success')
        WARNING = 'WARNING', _('Warning')
        ALERT = 'ALERT', _('Alert / Urgent')

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    objects = NotificationManager()
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=Type.choices, default=Type.INFO, db_index=True)
    reference_type = models.CharField(max_length=50, blank=True, help_text=_("e.g. INVOICE, LEAD, REMINDER, SALE"))
    reference_id = models.PositiveIntegerField(null=True, blank=True)
    target_url = models.CharField(max_length=255, blank=True, help_text=_("Relative internal path e.g. /sales/orders/1/"))
    idempotency_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=_("Unique deduplication key for automated rules. Null for manual notifications.")
    )
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    @property
    def recipient(self):
        return self.user

    @recipient.setter
    def recipient(self, value):
        self.user = value

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-created_at'], name='notif_user_read_created_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['idempotency_key'],
                condition=models.Q(idempotency_key__isnull=False),
                name='unique_notification_idempotency_key'
            ),
        ]

    def clean(self):
        super().clean()
        if self.idempotency_key == "":
            self.idempotency_key = None

        if self.target_url:
            url = self.target_url.strip()
            # Must start with single '/' and not '//'
            if not url.startswith('/') or url.startswith('//'):
                raise ValidationError({'target_url': _('Target URL must be a relative path starting with a single forward slash.')})
            # Must not contain backslashes
            if '\\' in url:
                raise ValidationError({'target_url': _('Target URL must not contain backslashes.')})
            # Must not contain control characters or whitespace
            if any(ord(c) < 32 or ord(c) == 127 for c in url):
                raise ValidationError({'target_url': _('Target URL contains invalid control characters.')})
            # Must not contain external schemes
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme or parsed.netloc:
                raise ValidationError({'target_url': _('Target URL must not specify an external scheme or host.')})
            if not url_has_allowed_host_and_scheme(url, allowed_hosts={None}, require_https=False):
                raise ValidationError({'target_url': _('Target URL is not a safe internal same-host path.')})
            self.target_url = url

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])

    def __str__(self):
        return f"{self.title} -> {self.user.username} ({'Read' if self.is_read else 'Unread'})"
