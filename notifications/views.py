from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.http import JsonResponse, HttpResponseNotAllowed
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.paginator import Paginator
from notifications.models import Notification
from notifications.services import mark_notification_as_read, mark_all_notifications_as_read
from notifications.validators import validate_internal_target_url
from django.core.exceptions import ValidationError


class NotificationListView(LoginRequiredMixin, View):
    template_name = 'notifications/notification_list.html'

    def get(self, request):
        status_filter = request.GET.get('status', 'all')
        notifications = Notification.objects.filter(user=request.user)

        if status_filter == 'unread':
            notifications = notifications.filter(is_read=False)
        elif status_filter == 'read':
            notifications = notifications.filter(is_read=True)

        paginator = Paginator(notifications, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()

        context = {
            'page_title': 'My Notifications',
            'page_obj': page_obj,
            'status_filter': status_filter,
            'unread_count': unread_count,
        }
        return render(request, self.template_name, context)


class NotificationMarkReadView(LoginRequiredMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, user=request.user)
        mark_notification_as_read(notification.pk, request.user)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'status': 'ok', 'is_read': True})
        next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'notifications:list'
        return redirect(next_url)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class NotificationMarkAllReadView(LoginRequiredMixin, View):
    def post(self, request):
        count = mark_all_notifications_as_read(request.user)
        messages.success(request, f"Marked {count} notification(s) as read.")
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'status': 'ok', 'updated_count': count})
        return redirect('notifications:list')

    def get(self, request):
        return HttpResponseNotAllowed(['POST'])


class NotificationFollowView(LoginRequiredMixin, View):
    """
    Marks notification as read and redirects safely to the internal target URL.
    Falls back to user's home dashboard if URL is missing or fails safety validation.
    POST-only to protect against CSRF and unintended state modification via prefetch.
    """
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, user=request.user)
        notification.mark_read()

        target = notification.target_url
        if target:
            try:
                safe_url = validate_internal_target_url(target)
                return redirect(safe_url)
            except ValidationError:
                pass

        return redirect('dashboard:index')

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class NotificationUnreadCountView(LoginRequiredMixin, View):
    def get(self, request):
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
        return JsonResponse({'unread_count': unread_count})
