from django.urls import path
from ai_assistant.views import AssistantIndexView, ClearAssistantHistoryView

app_name = 'ai_assistant'

urlpatterns = [
    path('', AssistantIndexView.as_view(), name='index'),
    path('clear/', ClearAssistantHistoryView.as_view(), name='clear'),
]
