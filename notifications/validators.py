import urllib.parse
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _

ALLOWED_NOTIFICATION_ROUTES = {
    'leads:lead_detail',
    'leads:lead_list',
    'sales:sale_detail',
    'sales:sale_list',
    'sales:invoice_detail',
    'sales:invoice_list',
    'sales:payment_list',
    'reminders:reminder_detail',
    'reminders:reminder_list',
    'reminders:customer_reminder_list',
    'interactions:interaction_detail',
    'interactions:interaction_list',
    'dashboard:admin_dashboard',
    'dashboard:staff_dashboard',
    'dashboard:customer_dashboard',
}


ROUTE_SHORTCUTS = {
    'sale_detail': 'sales:sale_detail',
    'sale_list': 'sales:sale_list',
    'invoice_detail': 'sales:invoice_detail',
    'invoice_list': 'sales:invoice_list',
    'payment_list': 'sales:payment_list',
    'lead_detail': 'leads:lead_detail',
    'lead_list': 'leads:lead_list',
    'reminder_detail': 'reminders:reminder_detail',
    'reminder_list': 'reminders:reminder_list',
    'customer_reminder_list': 'reminders:customer_reminder_list',
    'interaction_detail': 'interactions:interaction_detail',
    'interaction_list': 'interactions:interaction_list',
    'admin_dashboard': 'dashboard:admin_dashboard',
    'staff_dashboard': 'dashboard:staff_dashboard',
    'customer_dashboard': 'dashboard:customer_dashboard',
}


def build_notification_url(route_name, *args, **kwargs):
    """
    Generate a validated internal target URL from a trusted named route allowlist.
    Supports both direct namespaced routes and registered shortcut names.
    """
    resolved_route = ROUTE_SHORTCUTS.get(route_name, route_name)
    if resolved_route not in ALLOWED_NOTIFICATION_ROUTES:
        return ""
    try:
        if args:
            return reverse(resolved_route, args=args)
        if kwargs:
            return reverse(resolved_route, kwargs=kwargs)
        return reverse(resolved_route)
    except Exception:
        return ""


def validate_internal_target_url(url):
    """
    Strictly validates that a target URL is a safe, internal, same-host relative path:
    - Must start with a single forward slash ('/').
    - Rejects protocol-relative URLs ('//').
    - Rejects backslashes ('\\').
    - Rejects ASCII control characters and whitespace.
    - Rejects URI schemes (http:, https:, javascript:, data:, etc.).
    - Validates via url_has_allowed_host_and_scheme.
    """
    if not url:
        return ""

    if not isinstance(url, str):
        raise ValidationError(_("Target URL must be a string."))

    cleaned = url.strip()

    if not cleaned.startswith('/') or cleaned.startswith('//'):
        raise ValidationError(_("Target URL must be a relative path beginning with a single forward slash."))

    if '\\' in cleaned:
        raise ValidationError(_("Target URL must not contain backslashes."))

    if any(ord(c) < 32 or ord(c) == 127 for c in cleaned):
        raise ValidationError(_("Target URL contains forbidden control characters."))

    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme or parsed.netloc:
        raise ValidationError(_("Target URL must not contain external scheme or host."))

    if not url_has_allowed_host_and_scheme(cleaned, allowed_hosts={None}, require_https=False):
        raise ValidationError(_("Target URL is not a safe internal path."))

    return cleaned
