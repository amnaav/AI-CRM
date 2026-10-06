from django.urls import path
from notifications.views import (
    NotificationListView,
    NotificationMarkReadView,
    NotificationMarkAllReadView,
    NotificationFollowView,
    NotificationUnreadCountView,
)

app_name = 'notifications'

urlpatterns = [
    path('', NotificationListView.as_view(), name='notification_list'),
    path('all/', NotificationListView.as_view(), name='list'),
    path('<int:pk>/read/', NotificationMarkReadView.as_view(), name='mark_read'),
    path('read-all/', NotificationMarkAllReadView.as_view(), name='mark_all_read'),
    path('<int:pk>/follow/', NotificationFollowView.as_view(), name='follow_notification'),
    path('<int:pk>/go/', NotificationFollowView.as_view(), name='follow'),
    path('unread-count/', NotificationUnreadCountView.as_view(), name='unread_count'),
]
