from functools import wraps
from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.contrib import messages
from accounts.models import CustomUser


class RoleRequiredMixin(AccessMixin):
    """Verify that the current user is authenticated, active, and possesses one of the allowed roles."""
    allowed_roles = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if not request.user.is_account_active:
            messages.error(request, "Your account has been deactivated or locked. Please contact the administrator.")
            return redirect('accounts:login')

        if request.user.role not in self.allowed_roles and not request.user.is_superuser:
            raise PermissionDenied("You do not have permission to access this resource.")

        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]


class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = [CustomUser.Role.ADMIN]


class StaffRequiredMixin(RoleRequiredMixin):
    """Allows Sales Staff and Admins."""
    allowed_roles = [CustomUser.Role.ADMIN, CustomUser.Role.SALES_STAFF]


class CustomerRequiredMixin(RoleRequiredMixin):
    allowed_roles = [CustomUser.Role.CUSTOMER]


def role_required(allowed_roles):
    """Function view decorator for role-based access control."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.warning(request, "Please log in to continue.")
                return redirect('accounts:login')

            if not request.user.is_account_active:
                messages.error(request, "Your account has been deactivated or locked.")
                return redirect('accounts:login')

            if request.user.role not in allowed_roles and not request.user.is_superuser:
                raise PermissionDenied("Access denied. Unauthorized role.")

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


admin_required = role_required([CustomUser.Role.ADMIN])
staff_required = role_required([CustomUser.Role.ADMIN, CustomUser.Role.SALES_STAFF])
customer_required = role_required([CustomUser.Role.CUSTOMER])
