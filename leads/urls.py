from django.urls import path
from leads.views import (
    LeadListView,
    LeadDetailView,
    LeadCreateView,
    LeadEditView,
    LeadStatusUpdateView,
    LeadAssignView,
    LeadScoreView,
    LeadConvertView,
    LeadArchiveView,
)

app_name = 'leads'

urlpatterns = [
    path('', LeadListView.as_view(), name='lead_list'),
    path('create/', LeadCreateView.as_view(), name='lead_create'),
    path('<int:pk>/', LeadDetailView.as_view(), name='lead_detail'),
    path('<int:pk>/edit/', LeadEditView.as_view(), name='lead_edit'),
    path('<int:pk>/status/', LeadStatusUpdateView.as_view(), name='lead_status'),
    path('<int:pk>/assign/', LeadAssignView.as_view(), name='lead_assign'),
    path('<int:pk>/score/', LeadScoreView.as_view(), name='lead_score'),
    path('<int:pk>/convert/', LeadConvertView.as_view(), name='lead_convert'),
    path('<int:pk>/archive/', LeadArchiveView.as_view(), name='lead_archive'),
]
