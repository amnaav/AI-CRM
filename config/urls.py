from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from core.views import LandingPageView, home_redirect

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', LandingPageView.as_view(), name='home'),
    path('accounts/', include('accounts.urls', namespace='accounts')),
    path('dashboard/', include('dashboard.urls', namespace='dashboard')),
    path('leads/', include('leads.urls', namespace='leads')),
    path('catalog/', include('catalog.urls', namespace='catalog')),
    path('sales/', include('sales.urls', namespace='sales')),
    path('interactions/', include('interactions.urls', namespace='interactions')),
    path('reminders/', include('reminders.urls', namespace='reminders')),
    path('notifications/', include('notifications.urls', namespace='notifications')),
    path('activity-logs/', include('activity_logs.urls', namespace='activity_logs')),
    path('reports/', include('reports.urls', namespace='reports')),
    path('assistant/', include('ai_assistant.urls', namespace='ai_assistant')),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler400 = 'core.views.error_400'
handler403 = 'core.views.error_403'
handler404 = 'core.views.error_404'
handler500 = 'core.views.error_500'
