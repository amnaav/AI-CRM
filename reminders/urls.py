from django.urls import path
from reminders.views import (
    ReminderListView,
    ReminderDetailView,
    ReminderCreateView,
    ReminderUpdateView,
    ReminderCompleteView,
    ReminderCancelView,
    CustomerReminderListView,
)

app_name = 'reminders'

urlpatterns = [
    path('', ReminderListView.as_view(), name='reminder_list'),
    path('create/', ReminderCreateView.as_view(), name='reminder_create'),
    path('<int:pk>/', ReminderDetailView.as_view(), name='reminder_detail'),
    path('<int:pk>/edit/', ReminderUpdateView.as_view(), name='reminder_update'),
    path('<int:pk>/complete/', ReminderCompleteView.as_view(), name='reminder_complete'),
    path('<int:pk>/cancel/', ReminderCancelView.as_view(), name='reminder_cancel'),
    path('portal/schedule/', CustomerReminderListView.as_view(), name='customer_reminder_list'),
]
