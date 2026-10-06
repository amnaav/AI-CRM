from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import PermissionDenied


class ActivityLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionDenied("Batch updates on ActivityLog are forbidden.")

    def delete(self):
        raise PermissionDenied("Batch deletions on ActivityLog are forbidden.")

    def _filter_or_exclude(self, negate, args, kwargs):
        new_kwargs = {}
        for k, v in kwargs.items():
            if k == 'entity_type':
                new_kwargs['module_name'] = v
            elif k.startswith('entity_type__'):
                new_kwargs['module_name__' + k[len('entity_type__'):]] = v
            elif k == 'entity_id':
                new_kwargs['record_id'] = str(v)
            elif k.startswith('entity_id__'):
                new_kwargs['record_id__' + k[len('entity_id__'):]] = str(v)
            else:
                new_kwargs[k] = v
        return super()._filter_or_exclude(negate, args, new_kwargs)


class ActivityLogManager(models.Manager.from_queryset(ActivityLogQuerySet)):
    pass


class ActivityLog(models.Model):
    class Action(models.TextChoices):
        LOGIN = 'LOGIN', _('User Login')
        LOGOUT = 'LOGOUT', _('User Logout')
        CREATE = 'CREATE', _('Record Created')
        UPDATE = 'UPDATE', _('Record Updated')
        DELETE = 'DELETE', _('Record Deleted / Soft-Deleted')
        EXPORT = 'EXPORT', _('Data Export')
        CONVERT_LEAD = 'CONVERT_LEAD', _('Lead Converted to Customer')
        PAYMENT = 'PAYMENT', _('Payment Processed')
        OTHER = 'OTHER', _('System Action')

    ActionChoices = Action

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs'
    )
    action = models.CharField(max_length=30, choices=Action.choices, default=Action.OTHER, db_index=True)
    module_name = models.CharField(max_length=100, db_index=True)
    record_id = models.CharField(max_length=50, blank=True, db_index=True)
    description = models.TextField()
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Sanitized contextual metadata.")
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = ActivityLogManager()

    @property
    def entity_type(self):
        return self.module_name

    @entity_type.setter
    def entity_type(self, val):
        self.module_name = val

    @property
    def entity_id(self):
        return self.record_id

    @entity_id.setter
    def entity_id(self, val):
        self.record_id = str(val)


    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['module_name', 'record_id', '-created_at'], name='actlog_mod_rec_created_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None and ActivityLog.objects.filter(pk=self.pk).exists():
            raise PermissionDenied("ActivityLog records are immutable and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("ActivityLog records cannot be deleted.")

    def __str__(self):
        actor = self.user.username if self.user else "System"
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M')}] {actor} - {self.action} on {self.module_name}"
