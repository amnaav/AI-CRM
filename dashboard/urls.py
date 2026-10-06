from django.urls import path
from dashboard import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.DashboardIndexView.as_view(), name='index'),
    path('admin/', views.AdminDashboardView.as_view(), name='admin_dashboard'),
    path('staff/', views.StaffDashboardView.as_view(), name='staff_dashboard'),
    path('customer/', views.CustomerDashboardView.as_view(), name='customer_dashboard'),
]
