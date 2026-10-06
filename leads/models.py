import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from accounts.models import SalesStaffProfile, CustomerProfile


class Lead(models.Model):
    class Source(models.TextChoices):
        WEBSITE = 'WEBSITE', _('Website / Inbound')
        REFERRAL = 'REFERRAL', _('Customer Referral')
        SOCIAL_MEDIA = 'SOCIAL_MEDIA', _('Social Media Campaign')
        EMAIL_CAMPAIGN = 'EMAIL_CAMPAIGN', _('Email Marketing')
        PHONE_ENQUIRY = 'PHONE_ENQUIRY', _('Phone Enquiry')
        WALK_IN = 'WALK_IN', _('Walk-in')
        COLD_CALL = 'COLD_CALL', _('Outbound Call')
        EVENT = 'EVENT', _('Trade Show / Event')
        OTHER = 'OTHER', _('Other')

    class Status(models.TextChoices):
        NEW = 'NEW', _('New Lead')
        CONTACTED = 'CONTACTED', _('Contacted')
        QUALIFIED = 'QUALIFIED', _('Qualified')
        PROPOSAL = 'PROPOSAL', _('Proposal Sent')
        NEGOTIATION = 'NEGOTIATION', _('Under Negotiation')
        WON = 'WON', _('Closed Won')
        LOST = 'LOST', _('Closed Lost')

    class Priority(models.TextChoices):
        HIGH = 'HIGH', _('High')
        MEDIUM = 'MEDIUM', _('Medium')
        LOW = 'LOW', _('Low')

    class ScoringMethod(models.TextChoices):
        NOT_SCORED = 'NOT_SCORED', _('Not Scored')
        HEURISTIC = 'HEURISTIC', _('Weighted Heuristic Engine')
        MACHINE_LEARNING = 'MACHINE_LEARNING', _('Scikit-learn Logistic Regression')

    lead_code = models.CharField(max_length=30, unique=True, db_index=True)
    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    company = models.CharField(max_length=150, blank=True)
    source = models.CharField(max_length=30, choices=Source.choices, default=Source.WEBSITE)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.NEW, db_index=True)
    estimated_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    assigned_staff = models.ForeignKey(
        SalesStaffProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='leads'
    )
    probability_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="AI Predicted conversion score 0-100%"
    )
    ai_priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    scoring_method = models.CharField(
        max_length=30,
        choices=ScoringMethod.choices,
        default=ScoringMethod.NOT_SCORED
    )
    scoring_explanation = models.JSONField(
        default=list,
        blank=True,
        help_text="Structured factor breakdown affecting AI score"
    )
    last_scored_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, help_text="Soft-deletion/archive flag")
    converted_customer = models.OneToOneField(
        CustomerProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='origin_lead'
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['source'], name='lead_source_idx'),
            models.Index(fields=['ai_priority'], name='lead_priority_idx'),
            models.Index(fields=['is_active'], name='lead_is_active_idx'),
            models.Index(fields=['created_at'], name='lead_created_at_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(probability_score__gte=Decimal('0.00'), probability_score__lte=Decimal('100.00')),
                name='lead_prob_score_range_0_100'
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.lead_code:
            self.lead_code = f"LED-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

    @property
    def is_converted(self):
        return self.converted_customer is not None

    def __str__(self):
        return f"{self.full_name} ({self.lead_code}) - {self.get_status_display()}"


class LeadStatusHistory(models.Model):
    """Immutable audit record for lead stage transitions."""
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='status_history')
    old_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lead_status_changes'
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Lead Status History'
        verbose_name_plural = 'Lead Status Histories'
        indexes = [
            models.Index(fields=['lead', '-created_at'], name='lead_hist_lead_dt_idx'),
        ]

    def __str__(self):
        return f"{self.lead.lead_code}: {self.old_status or 'None'} -> {self.new_status} at {self.created_at}"
