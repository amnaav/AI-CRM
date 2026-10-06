from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from accounts.models import CustomerProfile, SalesStaffProfile
from leads.models import Lead


class Interaction(models.Model):
    class InteractionType(models.TextChoices):
        CALL = 'CALL', _('Phone Call')
        EMAIL = 'EMAIL', _('Email Communication')
        MEETING = 'MEETING', _('In-Person / Virtual Meeting')
        NOTE = 'NOTE', _('Internal Note')
        FOLLOW_UP = 'FOLLOW_UP', _('Follow-Up')
        WHATSAPP = 'WHATSAPP', _('WhatsApp / Instant Message')
        OTHER = 'OTHER', _('Other')
        # Legacy compatibility alias
        PHONE_CALL = 'PHONE_CALL', _('Phone Call (Legacy)')

    customer = models.ForeignKey(
        CustomerProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='interactions'
    )
    lead = models.ForeignKey(
        Lead,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='interactions'
    )
    sale = models.ForeignKey(
        'sales.Sale',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='interactions'
    )
    sales_staff = models.ForeignKey(
        SalesStaffProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='interactions_logged'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_interactions'
    )
    interaction_type = models.CharField(
        max_length=30,
        choices=InteractionType.choices,
        default=InteractionType.CALL,
        db_index=True
    )
    subject = models.CharField(max_length=200)
    description = models.TextField()
    is_customer_visible = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Whether visible to customer portal.")
    )
    interaction_date = models.DateTimeField(default=timezone.now, db_index=True)
    next_follow_up = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-interaction_date']
        indexes = [
            models.Index(fields=['customer', 'is_customer_visible', '-interaction_date'], name='interact_cust_vis_date_idx'),
            models.Index(fields=['lead', '-interaction_date'], name='interact_lead_date_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(customer__isnull=False) | models.Q(lead__isnull=False) | models.Q(sale__isnull=False),
                name='interaction_must_have_target'
            ),
            models.CheckConstraint(
                check=~models.Q(interaction_type='NOTE', is_customer_visible=True),
                name='internal_notes_never_customer_visible'
            ),
        ]

    def __init__(self, *args, **kwargs):
        if not args:
            if 'notes' in kwargs and 'description' not in kwargs:
                kwargs['description'] = kwargs.pop('notes')
            if 'staff' in kwargs and 'sales_staff' not in kwargs:
                kwargs['sales_staff'] = kwargs.pop('staff')
            kwargs.setdefault('description', '')
        super().__init__(*args, **kwargs)

    @property
    def notes(self):
        return self.description

    @notes.setter
    def notes(self, val):
        self.description = val

    @property
    def staff(self):
        return self.sales_staff

    @staff.setter
    def staff(self, val):
        self.sales_staff = val

    def clean_fields(self, exclude=None):
        if not self.description:
            self.description = self.subject or "Interaction touchpoint"
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        # Normalization: if sale is provided without customer, auto-assign from sale
        if self.sale and not self.customer:
            self.customer = self.sale.customer

        # Internal notes can never be customer-visible
        if self.interaction_type == self.InteractionType.NOTE and self.is_customer_visible:
            raise ValidationError({'is_customer_visible': _('Internal notes cannot be visible to customers.')})

        # Customer visible interaction requires customer
        if self.is_customer_visible and not self.customer:
            raise ValidationError({'is_customer_visible': _('Customer visible interaction requires a linked customer.')})

        # Target rule
        if not self.customer and not self.lead and not self.sale:
            raise ValidationError(_('An interaction must target at least a customer, a lead, or a sale.'))

        # Cross-entity consistency checks
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

    def __str__(self):
        target = self.customer or self.lead or (self.sale and self.sale.customer) or "General"
        return f"{self.get_interaction_type_display()} with {target} - {self.subject}"

