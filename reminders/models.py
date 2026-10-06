from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from accounts.models import CustomerProfile
from leads.models import Lead


class Reminder(models.Model):
    class ReminderType(models.TextChoices):
        TASK = 'TASK', _('Task / General Task')
        CALL = 'CALL', _('Phone Call')
        MEETING = 'MEETING', _('Client Meeting')
        EMAIL = 'EMAIL', _('Send Email')
        FOLLOW_UP = 'FOLLOW_UP', _('Follow-Up')
        PAYMENT_FOLLOWUP = 'PAYMENT_FOLLOWUP', _('Payment Follow-up')
        CONTRACT_RENEWAL = 'CONTRACT_RENEWAL', _('Contract Renewal')
        OTHER = 'OTHER', _('Other')

    class Priority(models.TextChoices):
        LOW = 'LOW', _('Low Priority')
        MEDIUM = 'MEDIUM', _('Medium Priority')
        HIGH = 'HIGH', _('High Priority')
        URGENT = 'URGENT', _('Urgent Priority')

    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        COMPLETED = 'COMPLETED', _('Completed')
        CANCELLED = 'CANCELLED', _('Cancelled')

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    reminder_type = models.CharField(
        max_length=30,
        choices=ReminderType.choices,
        default=ReminderType.TASK,
        db_index=True
    )
    customer = models.ForeignKey(
        CustomerProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reminders'
    )
    lead = models.ForeignKey(
        Lead,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reminders'
    )
    sale = models.ForeignKey(
        'sales.Sale',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reminders'
    )
    due_date = models.DateField(db_index=True)
    due_time = models.TimeField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    is_customer_visible = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Whether visible to customer portal.")
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='assigned_reminders'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_reminders'
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='completed_reminders'
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_date', 'due_time', '-priority']
        indexes = [
            models.Index(fields=['assigned_to', 'status', 'due_date'], name='reminder_assign_stat_due_idx'),
            models.Index(fields=['customer', 'is_customer_visible', 'status'], name='reminder_cust_vis_stat_idx'),
            models.Index(fields=['status', 'due_date'], name='reminder_status_due_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    (models.Q(status='COMPLETED', completed_at__isnull=False)) |
                    (~models.Q(status='COMPLETED') & models.Q(completed_at__isnull=True, completed_by__isnull=True))
                ),
                name='reminder_completed_metadata_consistency'
            ),
        ]

    def clean(self):
        super().clean()
        if hasattr(self.due_date, 'date'):
            self.due_date = self.due_date.date()

        if self.sale and not self.customer:
            self.customer = self.sale.customer

        if self.status == self.Status.COMPLETED:
            if not self.completed_at:
                raise ValidationError({'completed_at': _('Completed reminders require a completion timestamp.')})
        else:
            if self.completed_at or self.completed_by:
                raise ValidationError({'completed_at': _('Pending or cancelled reminders cannot retain completion metadata.')})

        if self.is_customer_visible and not self.customer:
            raise ValidationError({'is_customer_visible': _('Customer visible reminder requires a linked customer.')})

        if self.assigned_to_id and hasattr(self.assigned_to, 'role'):
            if self.assigned_to.role not in ['ADMIN', 'SALES_STAFF']:
                raise ValidationError({'assigned_to': _('Reminders can only be assigned to Admin or Sales Staff.')})

        if self.sale and self.customer and self.sale.customer_id != self.customer_id:
            raise ValidationError({'sale': _('Selected sale belongs to a different customer.')})

        if self.lead and self.customer:
            if self.lead.converted_customer_id and self.lead.converted_customer_id != self.customer_id:
                raise ValidationError({'customer': _('Selected customer does not match the converted lead customer.')})

        if self.sale and self.lead:
            if not self.lead.is_converted or self.sale.customer_id != self.lead.converted_customer_id:
                raise ValidationError({'sale': _('Selected sale does not belong to the converted lead customer.')})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def mark_completed(self, user=None):
        self.status = self.Status.COMPLETED
        if not self.completed_at:
            self.completed_at = timezone.now()
        if user:
            self.completed_by = user
        self.save()

    def mark_cancelled(self):
        self.status = self.Status.CANCELLED
        self.completed_at = None
        self.completed_by = None
        self.save()

    def mark_pending(self):
        self.status = self.Status.PENDING
        self.completed_at = None
        self.completed_by = None
        self.save()

    @property
    def is_overdue(self):
        return self.status == self.Status.PENDING and self.due_date < timezone.localdate()

    def __str__(self):
        return f"{self.title} - Due: {self.due_date} [{self.get_status_display()}]"
