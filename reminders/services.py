from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from reminders.models import Reminder
from activity_logs.services import log_activity
from notifications.services import create_notification
from notifications.validators import build_notification_url


def validate_reminder_relationships(customer=None, lead=None, sale=None, assigned_to=None):
    """
    Validates cross-entity consistency for a reminder:
    - If sale is provided, sale.customer must match customer.
    - If lead is converted, lead.converted_customer must match customer.
    - If sale and lead are provided, sale.customer must match lead.converted_customer.
    - assigned_to must be an active staff or admin user.
    """
    if sale and customer and sale.customer_id != customer.id:
        raise ValidationError({'sale': _("Selected sale belongs to a different customer.")})

    if lead and customer:
        if lead.converted_customer_id and lead.converted_customer_id != customer.id:
            raise ValidationError({'customer': _("Selected customer does not match the converted lead customer.")})

    if sale and lead:
        if not lead.is_converted or sale.customer_id != lead.converted_customer_id:
            raise ValidationError({'sale': _("Selected sale does not belong to the converted lead customer.")})

    if assigned_to:
        if not assigned_to.is_account_active:
            raise ValidationError({'assigned_to': _("Assigned user account is inactive.")})
        if assigned_to.is_customer:
            raise ValidationError({'assigned_to': _("Reminders cannot be assigned to customer user accounts.")})


@transaction.atomic
def create_reminder(
    title="",
    due_date=None,
    assigned_to=None,
    reminder_type=Reminder.ReminderType.TASK,
    customer=None,
    lead=None,
    sale=None,
    priority=Reminder.Priority.MEDIUM,
    description="",
    due_time=None,
    is_customer_visible=False,
    created_by=None,
    request=None,
    user=None,
    **kwargs
):
    """
    Atomic service function to create a reminder.
    Validates relationships, creates task, logs audit event,
    and sends notification to assignee.
    """
    creator = created_by or user or (request.user if (request and hasattr(request, 'user') and request.user.is_authenticated) else None)
    if sale and not customer:
        customer = sale.customer

    validate_reminder_relationships(customer=customer, lead=lead, sale=sale, assigned_to=assigned_to)

    reminder = Reminder(
        title=title.strip(),
        description=description.strip(),
        reminder_type=reminder_type,
        customer=customer,
        lead=lead,
        sale=sale,
        due_date=due_date,
        due_time=due_time,
        priority=priority,
        status=Reminder.Status.PENDING,
        is_customer_visible=is_customer_visible,
        assigned_to=assigned_to,
        created_by=creator,
    )
    reminder.full_clean()
    reminder.save()

    # Log activity
    log_activity(
        request=request,
        user=creator,
        action="CREATE",
        module_name="Reminders",
        record_id=str(reminder.pk),
        description=f"Created reminder #{reminder.pk} '{reminder.title}' due {reminder.due_date} assigned to {assigned_to.username}",
        metadata={'reminder_id': reminder.pk, 'due_date': str(reminder.due_date)}
    )

    # In-app notification for assigned user (if different from creator)
    if created_by != assigned_to:
        target_url = build_notification_url('reminders:reminder_detail', pk=reminder.pk)
        create_notification(
            user=assigned_to,
            title=f"New Task Assigned: {reminder.title}",
            message=f"You have been assigned a task: '{reminder.title}', due on {reminder.due_date}.",
            notification_type="INFO",
            reference_type="REMINDER",
            reference_id=reminder.pk,
            target_url=target_url
        )

    return reminder


@transaction.atomic
def complete_reminder(reminder_id, user, request=None):
    """
    Idempotently marks a reminder as COMPLETED with completed_at and completed_by.
    """
    reminder = Reminder.objects.select_for_update().filter(pk=reminder_id).first()
    if not reminder:
        return None

    # Idempotent check
    if reminder.status == Reminder.Status.COMPLETED:
        return reminder

    reminder.mark_completed(user=user)

    log_activity(
        request=request,
        action="UPDATE",
        module_name="Reminders",
        record_id=str(reminder.pk),
        description=f"Completed reminder #{reminder.pk} '{reminder.title}' by {user.username}",
        metadata={'reminder_id': reminder.pk, 'status': 'COMPLETED'}
    )
    return reminder


@transaction.atomic
def cancel_reminder(reminder_id, user, request=None):
    """
    Idempotently marks a reminder as CANCELLED, clearing completion metadata.
    """
    reminder = Reminder.objects.select_for_update().filter(pk=reminder_id).first()
    if not reminder:
        return None

    # Idempotent check
    if reminder.status == Reminder.Status.CANCELLED:
        return reminder

    reminder.mark_cancelled()

    log_activity(
        request=request,
        action="UPDATE",
        module_name="Reminders",
        record_id=str(reminder.pk),
        description=f"Cancelled reminder #{reminder.pk} '{reminder.title}' by {user.username}",
        metadata={'reminder_id': reminder.pk, 'status': 'CANCELLED'}
    )
    return reminder
