from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction, models
from accounts.models import CustomUser
from leads.models import Lead
from sales.models import Invoice
from interactions.models import Interaction
from reminders.models import Reminder
from notifications.models import Notification
from notifications.validators import build_notification_url


class Command(BaseCommand):
    help = "Smart Follow-Up Engine: Identifies stale leads, high-priority opportunities, overdue invoices, and scheduled touchpoints, creating deduplicated reminders and notifications."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Evaluate all rules and report statistics without committing database writes.",
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        today = timezone.localdate()
        now = timezone.now()

        mode_str = "DRY RUN (Simulated - No Database Writes)" if dry_run else "LIVE RUN"
        self.stdout.write(self.style.NOTICE(f"\n======================================================="))
        self.stdout.write(self.style.NOTICE(f" AI CRM Smart Follow-Up Engine [{mode_str}]"))
        self.stdout.write(self.style.NOTICE(f" Date: {today} | Timezone: {timezone.get_current_timezone_name()}"))
        self.stdout.write(self.style.NOTICE(f"=======================================================\n"))

        # Fallback admin recipient if assigned staff is missing/inactive
        fallback_admin = CustomUser.objects.filter(
            role=CustomUser.Role.ADMIN, status='ACTIVE'
        ).first()

        stats = {
            'rules_evaluated': 4,
            'reminders_created': 0,
            'reminders_skipped': 0,
            'notifications_created': 0,
            'notifications_skipped': 0,
        }

        # ---------------------------------------------------------------------
        # RULE 1: Stale Leads (No interaction for >= 3 days)
        # ---------------------------------------------------------------------
        stale_cutoff = now - timedelta(days=3)
        stale_leads = Lead.objects.filter(
            is_active=True,
            status__in=[Lead.Status.NEW, Lead.Status.CONTACTED]
        ).exclude(
            interactions__interaction_date__gte=stale_cutoff
        ).filter(
            created_at__lte=stale_cutoff
        ).select_related('assigned_staff__user')

        for lead in stale_leads:
            recipient = self._resolve_staff_recipient(lead.assigned_staff, fallback_admin)
            if not recipient:
                continue

            idempotency_key = f"followup:lead:{lead.pk}:LEAD_STALE:{recipient.pk}:{today}"

            # Reminder check
            has_pending_reminder = Reminder.objects.filter(
                lead=lead,
                status=Reminder.Status.PENDING,
                due_date=today
            ).exists()

            if not has_pending_reminder:
                stats['reminders_created'] += 1
                if not dry_run:
                    Reminder.objects.create(
                        title=f"Follow-up with stale lead: {lead.full_name}",
                        description=f"Lead {lead.lead_code} has had no logged touchpoint in the past 3 days.",
                        reminder_type=Reminder.ReminderType.CALL,
                        priority=Reminder.Priority.HIGH,
                        lead=lead,
                        due_date=today,
                        assigned_to=recipient,
                        created_by=recipient
                    )
            else:
                stats['reminders_skipped'] += 1

            # Notification check
            if not Notification.objects.filter(idempotency_key=idempotency_key).exists():
                stats['notifications_created'] += 1
                if not dry_run:
                    Notification.objects.create(
                        user=recipient,
                        title=f"Stale Lead Alert: {lead.full_name}",
                        message=f"Lead {lead.lead_code} ({lead.full_name}) requires follow-up. No communication recorded for 3+ days.",
                        notification_type=Notification.Type.WARNING,
                        reference_type="LEAD",
                        reference_id=lead.pk,
                        target_url=build_notification_url('leads:lead_detail', pk=lead.pk),
                        idempotency_key=idempotency_key
                    )
            else:
                stats['notifications_skipped'] += 1

        # ---------------------------------------------------------------------
        # RULE 2: High Probability Lead Inactivity (Score >= 70%, inactive >= 2 days)
        # ---------------------------------------------------------------------
        high_prob_cutoff = now - timedelta(days=2)
        high_prob_leads = Lead.objects.filter(
            is_active=True,
            probability_score__gte=70.00,
            status__in=[Lead.Status.QUALIFIED, Lead.Status.PROPOSAL, Lead.Status.NEGOTIATION]
        ).exclude(
            interactions__interaction_date__gte=high_prob_cutoff
        ).select_related('assigned_staff__user')

        for lead in high_prob_leads:
            recipient = self._resolve_staff_recipient(lead.assigned_staff, fallback_admin)
            if not recipient:
                continue

            idempotency_key = f"followup:lead:{lead.pk}:LEAD_HIGH_PROB:{recipient.pk}:{today}"

            has_pending_reminder = Reminder.objects.filter(
                lead=lead,
                priority=Reminder.Priority.URGENT,
                status=Reminder.Status.PENDING,
                due_date=today
            ).exists()

            if not has_pending_reminder:
                stats['reminders_created'] += 1
                if not dry_run:
                    Reminder.objects.create(
                        title=f"Urgent: High Probability Lead Attention ({lead.probability_score}%): {lead.full_name}",
                        description=f"High-conversion lead {lead.lead_code} has had no contact in 2+ days.",
                        reminder_type=Reminder.ReminderType.CALL,
                        priority=Reminder.Priority.URGENT,
                        lead=lead,
                        due_date=today,
                        assigned_to=recipient,
                        created_by=recipient
                    )
            else:
                stats['reminders_skipped'] += 1

            if not Notification.objects.filter(idempotency_key=idempotency_key).exists():
                stats['notifications_created'] += 1
                if not dry_run:
                    Notification.objects.create(
                        user=recipient,
                        title=f"High Value Lead Alert: {lead.full_name}",
                        message=f"High probability lead {lead.lead_code} ({lead.probability_score}%) needs immediate attention to maintain momentum.",
                        notification_type=Notification.Type.ALERT,
                        reference_type="LEAD",
                        reference_id=lead.pk,
                        target_url=build_notification_url('leads:lead_detail', pk=lead.pk),
                        idempotency_key=idempotency_key
                    )
            else:
                stats['notifications_skipped'] += 1

        # ---------------------------------------------------------------------
        # RULE 3: Overdue Invoices
        # ---------------------------------------------------------------------
        overdue_invoices = Invoice.objects.filter(
            invoice_status='OVERDUE',
            balance_amount__gt=0
        ).select_related('customer__user', 'sale__sales_staff__user')

        for inv in overdue_invoices:
            recipient = None
            if hasattr(inv, 'sale') and inv.sale and inv.sale.sales_staff and inv.sale.sales_staff.user.is_account_active:
                recipient = inv.sale.sales_staff.user
            elif inv.customer and inv.customer.assigned_staff and inv.customer.assigned_staff.user.is_account_active:
                recipient = inv.customer.assigned_staff.user
            else:
                recipient = fallback_admin

            if not recipient:
                continue

            idempotency_key = f"followup:invoice:{inv.pk}:INV_OVERDUE:{recipient.pk}:{today}"

            has_pending_reminder = Reminder.objects.filter(
                customer=inv.customer,
                reminder_type=Reminder.ReminderType.PAYMENT_FOLLOWUP,
                status=Reminder.Status.PENDING,
                due_date=today
            ).exists()

            if not has_pending_reminder:
                stats['reminders_created'] += 1
                if not dry_run:
                    Reminder.objects.create(
                        title=f"Payment Follow-Up: Invoice #{inv.invoice_number} ({inv.customer.user.full_name})",
                        description=f"Invoice #{inv.invoice_number} is overdue with outstanding balance of ${inv.balance_amount}.",
                        reminder_type=Reminder.ReminderType.PAYMENT_FOLLOWUP,
                        priority=Reminder.Priority.HIGH,
                        customer=inv.customer,
                        sale=inv.sale,
                        due_date=today,
                        assigned_to=recipient,
                        created_by=recipient
                    )
            else:
                stats['reminders_skipped'] += 1

            if not Notification.objects.filter(idempotency_key=idempotency_key).exists():
                stats['notifications_created'] += 1
                if not dry_run:
                    Notification.objects.create(
                        user=recipient,
                        title=f"Overdue Invoice: #{inv.invoice_number}",
                        message=f"Invoice #{inv.invoice_number} for {inv.customer.user.full_name} is overdue. Balance: ${inv.balance_amount}.",
                        notification_type=Notification.Type.WARNING,
                        reference_type="INVOICE",
                        reference_id=inv.pk,
                        target_url=build_notification_url('sales:invoice_detail', pk=inv.pk),
                        idempotency_key=idempotency_key
                    )
            else:
                stats['notifications_skipped'] += 1

        # ---------------------------------------------------------------------
        # RULE 4: Scheduled Next Follow-Up Due Today
        # ---------------------------------------------------------------------
        due_interactions = Interaction.objects.filter(
            next_follow_up=today
        ).select_related('sales_staff__user', 'customer', 'lead', 'sale')

        for interact in due_interactions:
            recipient = None
            if interact.sales_staff and interact.sales_staff.user.is_account_active:
                recipient = interact.sales_staff.user
            elif interact.created_by and interact.created_by.is_account_active:
                recipient = interact.created_by
            else:
                recipient = fallback_admin

            if not recipient:
                continue

            idempotency_key = f"followup:interaction:{interact.pk}:INTERACT_DUE:{recipient.pk}:{today}"

            has_pending_reminder = Reminder.objects.filter(
                models.Q(customer=interact.customer) if interact.customer else models.Q(lead=interact.lead),
                status=Reminder.Status.PENDING,
                due_date=today
            ).exists()

            if not has_pending_reminder:
                stats['reminders_created'] += 1
                if not dry_run:
                    target_name = interact.customer.user.full_name if interact.customer else (interact.lead.full_name if interact.lead else "Client")
                    Reminder.objects.create(
                        title=f"Follow-up due: {interact.subject} ({target_name})",
                        description=f"Scheduled follow-up from interaction #{interact.pk} on {interact.interaction_date.strftime('%Y-%m-%d')}.",
                        reminder_type=Reminder.ReminderType.FOLLOW_UP,
                        priority=Reminder.Priority.HIGH,
                        customer=interact.customer,
                        lead=interact.lead,
                        sale=interact.sale,
                        due_date=today,
                        assigned_to=recipient,
                        created_by=recipient
                    )
            else:
                stats['reminders_skipped'] += 1

            if not Notification.objects.filter(idempotency_key=idempotency_key).exists():
                stats['notifications_created'] += 1
                if not dry_run:
                    Notification.objects.create(
                        user=recipient,
                        title=f"Touchpoint Due Today: {interact.subject}",
                        message=f"You scheduled a follow-up for today regarding '{interact.subject}'.",
                        notification_type=Notification.Type.INFO,
                        reference_type="INTERACTION",
                        reference_id=interact.pk,
                        target_url=build_notification_url('interactions:interaction_detail', pk=interact.pk),
                        idempotency_key=idempotency_key
                    )
            else:
                stats['notifications_skipped'] += 1

        # Summary output
        total_created = stats['reminders_created'] + stats['notifications_created']
        self.stdout.write(self.style.SUCCESS(f"SMART FOLLOW-UP RUN COMPLETE"))
        self.stdout.write(f"Created: {total_created}")
        self.stdout.write(f" - Rules Evaluated:             {stats['rules_evaluated']}")
        self.stdout.write(f" - Reminders Created:           {stats['reminders_created']}")
        self.stdout.write(f" - Reminders Skipped:           {stats['reminders_skipped']} (already pending for today)")
        self.stdout.write(f" - Notifications Created:       {stats['notifications_created']}")
        self.stdout.write(f" - Notifications Skipped:       {stats['notifications_skipped']} (idempotency key matched)")
        self.stdout.write(f" - Mode:                        {mode_str}\n")

    def _resolve_staff_recipient(self, staff_profile, fallback_user):
        if staff_profile and staff_profile.user and staff_profile.user.is_account_active:
            return staff_profile.user
        return fallback_user
