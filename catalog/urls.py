from django.urls import path
from catalog import views

app_name = 'catalog'

urlpatterns = [
    # Product / Service Catalog (Admin & Sales Staff)
    path('', views.ProductServiceListView.as_view(), name='product_list'),
    path('items/add/', views.ProductServiceCreateView.as_view(), name='product_create'),
    path('items/<int:pk>/', views.ProductServiceDetailView.as_view(), name='product_detail'),
    path('items/<int:pk>/edit/', views.ProductServiceUpdateView.as_view(), name='product_update'),
    path('items/<int:pk>/toggle-status/', views.ProductServiceToggleStatusView.as_view(), name='product_toggle_status'),
    path('items/<int:pk>/delete/', views.ProductServiceDeleteView.as_view(), name='product_delete'),
    path('items/<int:pk>/adjust-stock/', views.ProductServiceStockAdjustView.as_view(), name='product_stock_adjust'),

    # Category Management (Admin only)
    path('categories/', views.CategoryListView.as_view(), name='category_list'),
    path('categories/add/', views.CategoryCreateView.as_view(), name='category_create'),
    path('categories/<int:pk>/', views.CategoryDetailView.as_view(), name='category_detail'),
    path('categories/<int:pk>/edit/', views.CategoryUpdateView.as_view(), name='category_update'),
    path('categories/<int:pk>/toggle-status/', views.CategoryToggleStatusView.as_view(), name='category_toggle_status'),
    path('categories/<int:pk>/delete/', views.CategoryDeleteView.as_view(), name='category_delete'),

    # Customer Portal Catalog
    path('portal/', views.CustomerCatalogListView.as_view(), name='customer_catalog'),
    path('portal/<int:pk>/', views.CustomerProductDetailView.as_view(), name='customer_product_detail'),
]
