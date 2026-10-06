from django.urls import path
from reports import views

app_name = 'reports'

urlpatterns = [
    # Hub & Analytics Dashboards
    path('', views.ReportsHomeView.as_view(), name='index'),
    path('analytics/', views.AdminAnalyticsView.as_view(), name='admin_analytics'),
    path('analytics/staff/', views.StaffAnalyticsView.as_view(), name='staff_analytics'),
    path('forecast/', views.SalesForecastView.as_view(), name='sales_forecast'),

    # Data Reports (HTML preview + ?export=csv|xlsx|pdf)
    path('customers/', views.CustomerReportView.as_view(), name='customer_report'),
    path('leads/', views.LeadReportView.as_view(), name='lead_report'),
    path('sales/', views.SalesReportView.as_view(), name='sales_report'),
    path('invoices/', views.InvoiceReportView.as_view(), name='invoice_report'),
    path('payments/', views.PaymentReportView.as_view(), name='payment_report'),
    path('catalog/', views.CatalogReportView.as_view(), name='catalog_report'),
    path('staff-performance/', views.StaffPerformanceReportView.as_view(), name='staff_performance_report'),
    path('activities/', views.ActivityReportView.as_view(), name='activity_report'),
]
