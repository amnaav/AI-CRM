from activity_logs.models import ActivityLog
from activity_logs.sanitizer import sanitize_metadata


def get_client_ip(request):
    """Extract client IP address handling proxies safely."""
    if not request or not hasattr(request, 'META'):
        return None
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_activity(
    request=None,
    action=ActivityLog.Action.OTHER,
    module_name="",
    record_id="",
    description="",
    metadata=None,
    user=None,
    entity_type="",
    entity_id="",
    ip_address=None
):
    """
    Log an event to the ActivityLog table.
    Sanitizes contextual metadata through the central sanitizer before creation.
    Never stores raw passwords, secrets, tokens, or sensitive credentials.
    """
    if user is None:
        if request and hasattr(request, 'user') and request.user.is_authenticated:
            user = request.user
        elif request and hasattr(request, 'is_authenticated'):
            # request argument passed directly as user
            user = request

    if not ip_address and request and hasattr(request, 'META'):
        ip_address = get_client_ip(request)

    final_module = module_name or entity_type or "General"
    final_record_id = str(record_id or entity_id or "")

    clean_metadata = sanitize_metadata(metadata) if metadata else {}

    return ActivityLog.objects.create(
        user=user,
        action=action,
        module_name=final_module,
        record_id=final_record_id,
        description=description,
        metadata=clean_metadata,
        ip_address=ip_address
    )
