from django.urls import path
from sales import views

app_name = 'sales'

urlpatterns = [
    # Sales Orders
    path('', views.SaleListView.as_view(), name='sale_list'),
    path('create/', views.SaleCreateView.as_view(), name='sale_create'),
    path('<int:pk>/', views.SaleDetailView.as_view(), name='sale_detail'),
    path('<int:pk>/edit/', views.SaleUpdateView.as_view(), name='sale_update'),
    path('<int:pk>/confirm/', views.SaleConfirmView.as_view(), name='sale_confirm'),
    path('<int:pk>/complete/', views.SaleCompleteView.as_view(), name='sale_complete'),
    path('<int:pk>/cancel/', views.SaleCancelView.as_view(), name='sale_cancel'),

    # Invoices
    path('invoices/', views.InvoiceListView.as_view(), name='invoice_list'),
    path('invoices/<int:pk>/', views.InvoiceDetailView.as_view(), name='invoice_detail'),
    path('invoices/<int:pk>/print/', views.InvoicePrintView.as_view(), name='invoice_print'),

    # Payments
    path('payments/', views.PaymentListView.as_view(), name='payment_list'),
    path('invoices/<int:invoice_pk>/pay/', views.PaymentCreateView.as_view(), name='payment_create'),
    path('payments/<int:pk>/', views.PaymentDetailView.as_view(), name='payment_detail'),
    path('payments/<int:pk>/print/', views.PaymentReceiptPrintView.as_view(), name='payment_receipt_print'),
    path('payments/<int:pk>/reverse/', views.PaymentReverseView.as_view(), name='payment_reverse'),

    # Customer Portal
    path('portal/purchases/', views.CustomerSaleListView.as_view(), name='customer_sale_list'),
    path('portal/purchases/<int:pk>/', views.CustomerSaleDetailView.as_view(), name='customer_sale_detail'),
    path('portal/invoices/', views.CustomerInvoiceListView.as_view(), name='customer_invoice_list'),
    path('portal/invoices/<int:pk>/', views.CustomerInvoiceDetailView.as_view(), name='customer_invoice_detail'),
    path('portal/invoices/<int:pk>/print/', views.InvoicePrintView.as_view(), name='customer_invoice_print'),
    path('portal/payments/', views.CustomerPaymentListView.as_view(), name='customer_payment_list'),
    path('portal/payments/<int:pk>/', views.CustomerPaymentDetailView.as_view(), name='customer_payment_detail'),
    path('portal/payments/<int:pk>/print/', views.PaymentReceiptPrintView.as_view(), name='customer_payment_receipt_print'),
]
