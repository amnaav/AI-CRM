import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.translation import gettext_lazy as _
from django.utils import timezone


class CustomUserManager(BaseUserManager):
    """Custom user manager where email is the unique identifier for auth if preferred, with username fallback."""
    def create_user(self, username, email, password=None, **extra_fields):
        if not email:
            raise ValueError(_('An email address is required.'))
        if not username:
            raise ValueError(_('A username is required.'))
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', CustomUser.Role.ADMIN)
        extra_fields.setdefault('status', CustomUser.Status.ACTIVE)

        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True.'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True.'))

        return self.create_user(username, email, password, **extra_fields)


class CustomUser(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', _('System Administrator')
        SALES_STAFF = 'SALES_STAFF', _('Sales Staff')
        CUSTOMER = 'CUSTOMER', _('Customer')

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', _('Active')
        INACTIVE = 'INACTIVE', _('Inactive')
        LOCKED = 'LOCKED', _('Locked')

    user_code = models.CharField(max_length=30, unique=True, db_index=True)
    full_name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    profile_image = models.ImageField(upload_to='profiles/', blank=True, null=True)
    last_login_ip = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CustomUserManager()

    def save(self, *args, **kwargs):
        if not self.user_code:
            prefix = 'ADM' if self.role == self.Role.ADMIN else ('STF' if self.role == self.Role.SALES_STAFF else 'CUST')
            random_suffix = uuid.uuid4().hex[:6].upper()
            self.user_code = f"{prefix}-{random_suffix}"
        if not self.full_name and (self.first_name or self.last_name):
            self.full_name = f"{self.first_name} {self.last_name}".strip()
        super().save(*args, **kwargs)

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_sales_staff(self):
        return self.role == self.Role.SALES_STAFF

    @property
    def is_customer(self):
        return self.role == self.Role.CUSTOMER

    @property
    def is_account_active(self):
        return self.status == self.Status.ACTIVE and self.is_active

    def __str__(self):
        return f"{self.full_name or self.username} ({self.get_role_display()})"


class SalesStaffProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='staff_profile')
    staff_code = models.CharField(max_length=30, unique=True, db_index=True)
    designation = models.CharField(max_length=100, default='Sales Executive')
    department = models.CharField(max_length=100, default='Direct Sales')
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    joining_date = models.DateField(default=timezone.now)
    assigned_target = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="Monthly revenue target")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.staff_code:
            self.staff_code = self.user.user_code or f"STF-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.full_name or self.user.username} - {self.designation}"


class CustomerProfile(models.Model):
    class CustomerType(models.TextChoices):
        INDIVIDUAL = 'INDIVIDUAL', _('Individual')
        BUSINESS = 'BUSINESS', _('Business / Corporate')

    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='customer_profile')
    customer_code = models.CharField(max_length=30, unique=True, db_index=True)
    company = models.CharField(max_length=150, blank=True)
    customer_type = models.CharField(max_length=20, choices=CustomerType.choices, default=CustomerType.INDIVIDUAL)
    assigned_staff = models.ForeignKey(
        SalesStaffProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_customers'
    )
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, help_text="Soft deletion flag")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.customer_code:
            self.customer_code = self.user.user_code or f"CUST-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.company if (self.customer_type == self.CustomerType.BUSINESS and self.company) else self.user.full_name
        return f"{label} ({self.customer_code})"
