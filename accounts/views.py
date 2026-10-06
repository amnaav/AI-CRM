from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView
from django.contrib.auth.views import (
    LoginView, PasswordChangeView
)
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from accounts.forms import CustomLoginForm, CustomerRegistrationForm, ProfileUpdateForm, CustomerProfileDetailsForm
from activity_logs.services import log_activity, get_client_ip


class CustomLoginView(LoginView):
    form_class = CustomLoginForm
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def form_valid(self, form):
        user = form.get_user()
        # Save last login IP
        user.last_login_ip = get_client_ip(self.request)
        user.save(update_fields=['last_login_ip'])

        # Audit log login event
        log_activity(
            self.request,
            action='LOGIN',
            module_name='Authentication',
            record_id=str(user.id),
            description=f"User {user.username} ({user.get_role_display()}) successfully logged in."
        )

        messages.success(self.request, f"Welcome back, {user.full_name or user.username}!")
        return super().form_valid(form)

    def get_success_url(self):
        user = self.request.user
        if user.is_admin:
            return reverse_lazy('dashboard:admin_dashboard')
        elif user.is_sales_staff:
            return reverse_lazy('dashboard:staff_dashboard')
        else:
            return reverse_lazy('dashboard:customer_dashboard')


class CustomLogoutView(View):
    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            log_activity(
                request,
                action='LOGOUT',
                module_name='Authentication',
                record_id=str(request.user.id),
                description=f"User {request.user.username} logged out."
            )
            logout(request)
        messages.info(request, "You have been securely logged out.")
        return redirect('accounts:login')

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)


class CustomerRegisterView(CreateView):
    form_class = CustomerRegistrationForm
    template_name = 'registration/register.html'
    success_url = reverse_lazy('accounts:login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('dashboard:index')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        log_activity(
            self.request,
            action='CREATE',
            module_name='CustomerRegistration',
            record_id=str(user.id),
            description=f"New customer registered: {user.full_name} ({user.email})"
        )
        messages.success(
            self.request,
            "Your customer account has been created successfully! You can now log in."
        )
        return super().form_valid(form)


class ProfileView(LoginRequiredMixin, View):
    template_name = 'registration/profile.html'

    def get(self, request):
        user_form = ProfileUpdateForm(instance=request.user)
        customer_form = None
        if request.user.is_customer and hasattr(request.user, 'customer_profile'):
            customer_form = CustomerProfileDetailsForm(instance=request.user.customer_profile)

        return render(request, self.template_name, {
            'user_form': user_form,
            'customer_form': customer_form,
        })

    def post(self, request):
        user_form = ProfileUpdateForm(request.POST, request.FILES, instance=request.user)
        customer_form = None
        if request.user.is_customer and hasattr(request.user, 'customer_profile'):
            customer_form = CustomerProfileDetailsForm(request.POST, instance=request.user.customer_profile)

        if user_form.is_valid() and (customer_form is None or customer_form.is_valid()):
            user_form.save()
            if customer_form:
                customer_form.save()
            log_activity(
                request,
                action='UPDATE',
                module_name='Profile',
                record_id=str(request.user.id),
                description=f"User {request.user.username} updated profile details."
            )
            messages.success(request, "Your profile details have been successfully updated.")
            return redirect('accounts:profile')

        return render(request, self.template_name, {
            'user_form': user_form,
            'customer_form': customer_form,
        })


class CustomPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    template_name = 'registration/password_change.html'
    success_url = reverse_lazy('accounts:profile')

    def form_valid(self, form):
        messages.success(self.request, "Your password has been changed successfully.")
        log_activity(
            self.request,
            action='UPDATE',
            module_name='Authentication',
            record_id=str(self.request.user.id),
            description="User changed account password."
        )
        return super().form_valid(form)
