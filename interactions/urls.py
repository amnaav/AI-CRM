from django.urls import path
from interactions.views import (
    InteractionListView,
    InteractionDetailView,
    InteractionCreateView,
    InteractionUpdateView,
    CustomerInteractionListView,
)

app_name = 'interactions'

urlpatterns = [
    path('', InteractionListView.as_view(), name='interaction_list'),
    path('create/', InteractionCreateView.as_view(), name='interaction_create'),
    path('<int:pk>/', InteractionDetailView.as_view(), name='interaction_detail'),
    path('<int:pk>/edit/', InteractionUpdateView.as_view(), name='interaction_update'),
    path('portal/history/', CustomerInteractionListView.as_view(), name='customer_interaction_list'),
]
