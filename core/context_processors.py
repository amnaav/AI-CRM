def crm_context(request):
    """
    Injected globally across all templates:
    System branding, role flags, unread notifications.
    """
    context = {
        'SYSTEM_NAME': 'AI CRM',
        'SYSTEM_TITLE': 'AI CRM – Intelligent Customer Relationship Management',
        'unread_notifications_count': 0,
        'recent_notifications': [],
        'user_customer_profile': None,
        'user_staff_profile': None,
    }

    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        # Safely fetch unread notifications
        try:
            user_notifs = request.user.notifications.all()
            context['unread_notifications_count'] = user_notifs.filter(is_read=False).count()
            context['recent_notifications'] = user_notifs[:5]
        except Exception:
            pass

        # Safely inject role-linked profiles
        if hasattr(request.user, 'customer_profile'):
            context['user_customer_profile'] = request.user.customer_profile
        if hasattr(request.user, 'staff_profile'):
            context['user_staff_profile'] = request.user.staff_profile

    return context
