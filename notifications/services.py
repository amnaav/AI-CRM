from django.db import transaction, IntegrityError
from django.utils import timezone
from notifications.models import Notification
from notifications.validators import validate_internal_target_url


@transaction.atomic
def create_notification(
    user=None,
    title="",
    message="",
    notification_type=Notification.Type.INFO,
    reference_type="",
    reference_id=None,
    target_url="",
    idempotency_key=None,
    recipient=None,
    **kwargs
):
    """
    Safely creates an in-app notification with idempotency support and URL sanitization.
    If idempotency_key is provided and already exists, returns existing record without duplication.
    """
    target_user = recipient or user
    if not target_user:
        raise ValueError("A user/recipient is required for notification.")

    if idempotency_key:
        existing = Notification.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    clean_url = validate_internal_target_url(target_url) if target_url else ""

    try:
        notification = Notification(
            user=target_user,
            title=title,
            message=message,
            notification_type=notification_type,
            reference_type=reference_type,
            reference_id=reference_id,
            target_url=clean_url,
            idempotency_key=idempotency_key or None
        )
        notification.full_clean()
        notification.save()
        return notification
    except IntegrityError:
        # Race condition safeguard on idempotency_key
        if idempotency_key:
            return Notification.objects.filter(idempotency_key=idempotency_key).first()
        raise


@transaction.atomic
def mark_notification_as_read(notification_id, user):
    """Marks a single notification as read, enforcing strict user ownership."""
    notification = Notification.objects.filter(pk=notification_id, user=user).first()
    if notification and not notification.is_read:
        notification.mark_read()
    return notification


@transaction.atomic
def mark_all_notifications_as_read(user):
    """Marks all unread notifications for the given user as read."""
    return Notification.objects.filter(user=user, is_read=False).update(
        is_read=True,
        read_at=timezone.now()
    )
