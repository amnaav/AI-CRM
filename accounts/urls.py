from django.urls import path
from django.contrib.auth import views as auth_views
from accounts import views
from accounts import customer_views
from accounts import staff_views

app_name = 'accounts'

urlpatterns = [
    # Authentication & Profile
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    path('register/', views.CustomerRegisterView.as_view(), name='register'),
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('password-change/', views.CustomPasswordChangeView.as_view(), name='password_change'),

    # Password Reset URLs
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='registration/password_reset_form.html',
            email_template_name='registration/password_reset_email.html',
            subject_template_name='registration/password_reset_subject.txt',
            success_url='/accounts/password-reset/done/'
        ),
        name='password_reset'
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='registration/password_reset_done.html'
        ),
        name='password_reset_done'
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='registration/password_reset_confirm.html',
            success_url='/accounts/reset/done/'
        ),
        name='password_reset_confirm'
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='registration/password_reset_complete.html'
        ),
        name='password_reset_complete'
    ),

    # Customer Management URLs
    path('customers/', customer_views.CustomerListView.as_view(), name='customer_list'),
    path('customers/create/', customer_views.CustomerCreateView.as_view(), name='customer_create'),
    path('customers/<int:pk>/', customer_views.CustomerDetailView.as_view(), name='customer_detail'),
    path('customers/<int:pk>/edit/', customer_views.CustomerUpdateView.as_view(), name='customer_edit'),
    path('customers/<int:pk>/status/', customer_views.CustomerStatusToggleView.as_view(), name='customer_status'),
    path('customers/<int:pk>/assign/', customer_views.CustomerAssignStaffView.as_view(), name='customer_assign'),
    path('customers/<int:pk>/delete/', customer_views.CustomerDeleteView.as_view(), name='customer_delete'),

    # Sales Staff Management URLs
    path('staff/', staff_views.StaffListView.as_view(), name='staff_list'),
    path('staff/create/', staff_views.StaffCreateView.as_view(), name='staff_create'),
    path('staff/<int:pk>/', staff_views.StaffDetailView.as_view(), name='staff_detail'),
    path('staff/<int:pk>/edit/', staff_views.StaffUpdateView.as_view(), name='staff_edit'),
    path('staff/<int:pk>/status/', staff_views.StaffStatusToggleView.as_view(), name='staff_status'),
    path('staff/<int:pk>/delete/', staff_views.StaffDeleteView.as_view(), name='staff_delete'),
]
