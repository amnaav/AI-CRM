from django.urls import path
from activity_logs.views import ActivityLogListView, ActivityLogDetailView

app_name = 'activity_logs'

urlpatterns = [
    path('', ActivityLogListView.as_view(), name='log_list'),
    path('<int:pk>/', ActivityLogDetailView.as_view(), name='log_detail'),
]
